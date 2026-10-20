"""External MCP config and profile routing regression tests."""
import asyncio
from contextlib import AsyncExitStack, suppress
import os
import sys
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from privacy_gateway.proxy import PrivacyProxy, ProxyConfig, connect_backend, parse_backend


class Detector:
    def detect(self, text, language, entities, threshold):
        return [(i, i + 5, "PERSON") for i in range(len(text)) if text.startswith("Alice", i)]


class ExternalConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        source = base / "source"
        source.mkdir()
        self.document = source / "private.txt"
        self.document.write_text("Alice")
        self.raw = {
            "source_roots": [str(source)], "workspace": str(base / "workspace"),
            "language": "en", "spacy_model": "en_core_web_lg", "entities": ["PERSON"],
            "score_threshold": .35, "access_scope": "scope", "max_source_bytes": 100000,
            "max_output_chars": 100000,
            "mcp_servers": {"my_files": {"profile": "filesystem", "enabled_tools": ["read_text_file"],
                                         "url": "http://127.0.0.1:8101/mcp", "workspace_path": "/remote/shared"}},
            "documents": [{"id": "doc_first_12345678", "path": str(self.document), "backend": "my_files"}],
        }

    def test_arbitrary_id_routes_through_profile_and_rewrites_path(self):
        proxy = PrivacyProxy(ProxyConfig(self.raw), bytes(range(32)), Detector())
        seen = []

        class Session:
            async def call_tool(self, tool, arguments):
                seen.append((tool, arguments))
                return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text="Alice " + arguments["path"], meta=None)], structuredContent=None, meta=None)

        proxy.sessions["my_files"] = Session()

        async def flow():
            result = await proxy.call("my_files__read_text_file", {"path": str(self.document)})
            self.assertNotIn("Alice", str(result))
            self.assertNotIn("/remote/shared", str(result))
            self.assertEqual(seen[0][0], "read_text_file")
            self.assertTrue(seen[0][1]["path"].startswith("/remote/shared/proxy-"))
            self.assertEqual(proxy.public_tools(), ["my_files__read_text_file"])
            with self.assertRaises(Exception):
                await proxy.call("my_files__edit_file", {"path": str(self.document)})
            with self.assertRaises(Exception):
                await proxy.call("filesystem__read_text_file", {"path": str(self.document)})

        asyncio.run(flow())

    def test_empty_config_and_validation(self):
        self.raw["mcp_servers"] = {}
        self.raw["documents"] = []
        proxy = PrivacyProxy(ProxyConfig(self.raw), bytes(range(32)), Detector())
        self.assertEqual(proxy.public_tools(), [])
        self.raw["mcp_servers"] = {"alias": {"profile": "unknown", "enabled_tools": ["read_text_file"], "url": "http://localhost/mcp"}}
        with self.assertRaises(ValueError):
            ProxyConfig(self.raw)
        self.raw["mcp_servers"]["alias"]["profile"] = "filesystem"
        self.raw["mcp_servers"]["alias"]["enabled_tools"] = ["unapproved"]
        with self.assertRaises(ValueError):
            ProxyConfig(self.raw)
        self.raw["mcp_servers"] = {"bad__alias": self.raw["mcp_servers"]["alias"]}
        with self.assertRaises(ValueError):
            ProxyConfig(self.raw)

    def test_toml_loading_and_transport_exclusion(self):
        path = Path(self.tmp.name) / "config.toml"
        path.write_text("""
source_roots = ["%s"]
workspace = "%s"
language = "en"
spacy_model = "en_core_web_lg"
entities = ["PERSON"]
score_threshold = 0.35
access_scope = "scope"
max_source_bytes = 100000
max_output_chars = 100000
[mcp_servers]
""" % (self.document.parent, Path(self.tmp.name) / "workspace"))
        self.assertEqual(ProxyConfig.load(str(path)).backends, {})
        self.raw["mcp_servers"]["my_files"]["command"] = sys.executable
        with self.assertRaises(ValueError):
            ProxyConfig(self.raw)
        self.raw["mcp_servers"]["my_files"].pop("command")
        self.raw["mcp_servers"]["my_files"]["url"] = "http://localhost:99999/mcp"
        with self.assertRaises(ValueError):
            ProxyConfig(self.raw)

    def test_live_http_bridge_routes_allowlisted_tool(self):
        from privacy_gateway.bridge import StdioBridge
        from privacy_gateway.http import create_http_app
        import uvicorn

        fixture = str(Path(__file__).with_name("fixture_mcp_server.py"))
        native = parse_backend("files", {"profile": "filesystem", "enabled_tools": ["read_text_file"],
                                         "command": sys.executable, "args": [fixture]})
        bridge = StdioBridge(native, timeout=5)

        async def flow():
            async with bridge.connected():
                self.assertEqual(list(bridge.tools), ["read_text_file"])
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                app = create_http_app(bridge.server(), bearer_token="test-token")
                server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
                task = asyncio.create_task(server.serve())
                try:
                    for _ in range(100):
                        if server.started:
                            break
                        await asyncio.sleep(.02)
                    self.assertTrue(server.started)
                    import httpx
                    async with httpx.AsyncClient(trust_env=False) as client:
                        denied = await client.post(f"http://127.0.0.1:{port}/mcp", json={})
                        self.assertEqual(denied.status_code, 401)
                        untrusted = await client.post(f"http://127.0.0.1:{port}/mcp", headers={"Host": "evil.example", "Authorization": "Bearer test-token"}, json={})
                        self.assertEqual(untrusted.status_code, 421)
                    remote = parse_backend("files", {"profile": "filesystem", "enabled_tools": ["read_text_file"],
                                                     "url": f"http://127.0.0.1:{port}/mcp", "bearer_token_env": "TEST_MCP_TOKEN"})
                    os.environ["TEST_MCP_TOKEN"] = "test-token"
                    try:
                        async with AsyncExitStack() as stack:
                            session, found = await connect_backend(stack, remote, 5)
                            self.assertEqual(list(found), ["read_text_file"])
                            result = await session.call_tool("read_text_file", {"path": str(self.document)})
                            self.assertIn("Alice", result.content[0].text)
                    finally:
                        os.environ.pop("TEST_MCP_TOKEN", None)
                finally:
                    server.should_exit = True
                    with suppress(asyncio.CancelledError):
                        await task

        asyncio.run(flow())


if __name__ == "__main__":
    unittest.main()
