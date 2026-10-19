"""Real loopback HTTP protocol tests with deterministic PII detection."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class Detector:
    identity = "http-test"

    def detect(self, text, language, entities, threshold):
        return [(i, i + 5, "PERSON") for i in range(len(text)) if text.startswith("Alice", i)]


@asynccontextmanager
async def running_http(server):
    import uvicorn
    from privacy_gateway.http import serve_http

    real_server = uvicorn.Server
    instances = []

    def capture(config):
        instance = real_server(config)
        instances.append(instance)
        return instance

    with patch("uvicorn.Server", capture):
        task = asyncio.create_task(serve_http(server, "127.0.0.1", 0))
        try:
            async with asyncio.timeout(10):
                while not instances or not instances[0].started:
                    if task.done():
                        await task
                        raise AssertionError("HTTP server stopped before startup")
                    await asyncio.sleep(0.01)
            port = instances[0].servers[0].sockets[0].getsockname()[1]
            yield f"http://127.0.0.1:{port}"
        finally:
            if instances:
                instances[0].should_exit = True
            await asyncio.wait_for(task, timeout=15)


@unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK not installed")
class HTTPTests(unittest.TestCase):
    def test_public_bind_requires_explicit_override(self):
        from privacy_gateway.http import serve_http

        async def flow():
            for host in ("0.0.0.0", "192.168.1.5", "::"):
                with self.assertRaises(ValueError):
                    await serve_http(None, host, 8000)

        asyncio.run(flow())

    def test_proxy_backend_lifetime_read_edit_and_error(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx
        from privacy_gateway.proxy import PrivacyProxy, ProxyConfig, proxy_backends, proxy_server

        async def flow(root):
            sources = root / "sources"
            sources.mkdir()
            source = sources / "note.txt"
            source.write_text("Alice", encoding="utf-8")
            ident = "doc_http_12345678"
            config = ProxyConfig({
                "source_roots": [str(sources)], "workspace": str(root / "workspace"),
                "language": "en", "spacy_model": "en_core_web_lg", "entities": ["PERSON"],
                "score_threshold": 0.35, "access_scope": "test", "max_source_bytes": 10000,
                "max_output_chars": 10000,
                "backends": {"filesystem": {"command": str(Path(sys.executable).absolute()),
                    "args": [str(Path(__file__).with_name("fixture_mcp_server.py").resolve())],
                    "tools": ["read_text_file", "edit_file"]}},
                "documents": [{"id": ident, "path": str(source), "backend": "filesystem"}],
            })
            proxy = PrivacyProxy(config, bytes(range(32)), Detector())
            async with proxy_backends(proxy):
                async with running_http(proxy_server(proxy)) as base:
                    async with httpx.AsyncClient() as client:
                        self.assertEqual((await client.get(base + "/healthz")).text, "ok")
                        headers = {"accept": "application/json, text/event-stream"}
                        response = await client.post(base + "/mcp", headers={**headers, "Host": "evil.example"}, json={})
                        self.assertEqual(response.status_code, 421)
                        response = await client.post(base + "/mcp", headers={**headers, "Origin": "https://evil.example"}, json={})
                        self.assertEqual(response.status_code, 403)
                    async with streamable_http_client(base + "/mcp") as streams:
                        async with ClientSession(streams[0], streams[1]) as session:
                            await session.initialize()
                            self.assertEqual(len((await session.list_tools()).tools), 2)
                            result = await session.call_tool("filesystem__read_text_file", {"document_id": ident})
                            self.assertFalse(result.isError)
                            self.assertNotIn("Alice", str(result))
                            # HTTP result carries the filtered backend text inside the gateway's JSON envelope.
                            self.assertIn("[[PG:", str(result))
                            edited = await session.call_tool("filesystem__edit_file", {
                                "document_id": ident, "expected_sha256": hashlib.sha256(b"Alice").hexdigest(),
                                "edits": [{"oldText": "Alice", "newText": "Changed"}]})
                            self.assertFalse(edited.isError)
                            reread = await session.call_tool("filesystem__read_text_file", {"document_id": ident})
                            self.assertFalse(reread.isError)
                            self.assertEqual(source.read_text(), "Changed")
                            denied = await session.call_tool("filesystem__read_text_file", {"document_id": "doc_unknown"})
                            self.assertTrue(denied.isError)
            self.assertEqual(proxy.sessions, {})

        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(flow(Path(temp)))
