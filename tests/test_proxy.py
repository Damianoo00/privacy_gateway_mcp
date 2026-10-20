import asyncio
import hashlib
import json
import importlib.util
import tempfile
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from privacy_gateway.core import GatewayError
from privacy_gateway.proxy import PrivacyProxy, ProxyConfig, Document


class Detector:
    def detect(self, text, language, entities, threshold):
        return [(i, i + 5, "PERSON") for i in range(len(text)) if text.startswith("Alice", i)]


class Session:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments.copy()))
        return self.callback(name, arguments)


def reply(text, *, error=False, structured=None):
    return SimpleNamespace(isError=error, content=[SimpleNamespace(type="text", text=text, _meta=None)], structuredContent=structured, meta=None)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "sources"
        self.source.mkdir()
        self.workspace = root / "workspace"
        self.doc = self.source / "private.txt"
        self.doc.write_text("Alice", encoding="utf-8")
        self.config = {
            "source_roots": [str(self.source)], "workspace": str(self.workspace),
            "language": "en", "spacy_model": "en_core_web_lg", "entities": ["PERSON"],
            "score_threshold": 0.35, "access_scope": "scope", "max_source_bytes": 100000,
            "max_output_chars": 100000, "backends": {"filesystem": {
                "command": str(Path(sys.executable).resolve()), "args": [], "tools": ["read_text_file", "edit_file"]}},
            "documents": [{"id": "doc_first_12345678", "path": str(self.doc), "backend": "filesystem"}],
        }
        self.proxy = PrivacyProxy(ProxyConfig(self.config), bytes(range(32)), Detector())

    def test_unregistered_paths_anywhere_are_masked(self):
        self.config.pop("documents")
        self.config.pop("source_roots")
        config = ProxyConfig(self.config)
        proxy = PrivacyProxy(config, bytes(range(32)), Detector())
        proxy.sessions["filesystem"] = Session(lambda name, args: reply(Path(args["path"]).read_text()))
        for path in (self.source / "new.txt", self.source.parent / "outside.custom"):
            path.write_text("Alice")
            result = asyncio.run(proxy.call("filesystem__read_text_file", {"path": str(path)}))
            self.assertNotIn("Alice", json.dumps(result))
            self.assertIn("[[PG:PERSON:", json.dumps(result))
        self.assertFalse(hasattr(config, "documents"))

    def test_unsafe_paths_denied(self):
        outside = self.source.parent / "outside.txt"
        outside.write_text("fixture")
        link = self.source / "link.txt"
        link.symlink_to(outside)
        hard = self.source / "hard.txt"
        hard.hardlink_to(outside)
        large = self.source / "large.txt"
        large.write_bytes(b"x" * 100001)
        internal = self.workspace / "internal.txt"
        internal.write_text("fixture")
        self.proxy.sessions["filesystem"] = Session(lambda name, args: reply("fixture"))
        for path in [link, hard, self.source / "missing.txt", large,
                     self.source / ".." / "outside.txt", self.source, internal, Path("relative.txt")]:
            with self.subTest(path=path), self.assertRaises(GatewayError):
                asyncio.run(self.proxy.call("filesystem__read_text_file", {"path": str(path)}))

    def test_read_edit_reread_and_vault(self):
        def call(name, args):
            path = Path(args["path"])
            if name == "read_text_file":
                return reply(path.read_text(), structured={"value": path.read_text()})
            text = path.read_text().replace(args["edits"][0]["oldText"], args["edits"][0]["newText"])
            path.write_text(text)
            return reply("edited " + str(path))
        session = Session(call)
        self.proxy.sessions["filesystem"] = session
        async def flow():
            first = await self.proxy.call("filesystem__read_text_file", {"path": str(self.doc)})
            token = first["content"][0]["text"]
            self.assertEqual(json.loads(first["content"][-1]["text"])["sha256"], first["sha256"])
            self.assertNotIn("Alice", str(first))
            self.assertTrue(token.startswith("[[PG:PERSON:"))
            self.assertNotIn(b"Alice", (self.workspace / "proxy_vault.sqlite3").read_bytes())
            edited = await self.proxy.call("filesystem__edit_file", {"path": str(self.doc), "expected_sha256": first["sha256"], "edits": [{"oldText": token, "newText": token + " has arrived"}]})
            self.assertEqual(self.doc.read_text(), "Alice has arrived")
            self.assertNotEqual(edited["sha256"], first["sha256"])
            with self.assertRaises(GatewayError):
                await self.proxy.call("filesystem__edit_file", {"path": str(self.doc), "expected_sha256": first["sha256"], "edits": [{"oldText": token, "newText": "X"}]})
            second = await self.proxy.call("filesystem__read_text_file", {"path": str(self.doc)})
            self.assertIn(token, second["content"][0]["text"])
        asyncio.run(flow())

    def test_forged_and_cross_document_tokens_denied(self):
        token = self.proxy.vault.issue(self.proxy.config.document(str(self.doc), "filesystem").ident, "PERSON", "Alice")
        second = self.source / "another.txt"
        second.write_text("Bob")
        self.config["documents"].append({"id": "doc_second_12345678", "path": str(second), "backend": "filesystem"})
        proxy = PrivacyProxy(ProxyConfig(self.config), bytes(range(32)), Detector())
        async def flow():
            for bad in (token, "[[PG:PERSON:" + "0" * 32 + "]]"):
                with self.assertRaises(GatewayError):
                    await proxy.call("filesystem__edit_file", {"path": str(second), "expected_sha256": hashlib.sha256(b"Bob").hexdigest(), "edits": [{"oldText": "Bob", "newText": bad}]})
        asyncio.run(flow())

    def test_vault_row_swap_is_rejected(self):
        vault = self.proxy.vault
        first = vault.issue("doc_first_12345678", "PERSON", "Alice")
        second = vault.issue("doc_first_12345678", "PERSON", "Carol")
        with vault.connection() as con:
            other = con.execute("SELECT value FROM tokens WHERE token=?", (second,)).fetchone()[0]
            con.execute("UPDATE tokens SET value=? WHERE token=?", (other, first))
        with self.assertRaises(GatewayError):
            vault.restore("doc_first_12345678", first)

    def test_excel_html_metadata_and_json_escape(self):
        excel = Document("doc_excel_12345678", self.doc, "excel")
        source = '<h2>Read Sheet</h2>\n<table><tr><td>A1</td><td>Alice</td></tr></table>\n<li>sheet name: A</li>\n'
        safe = self.proxy._result(excel, reply(source), "/tmp/copy.xlsx", "excel_read_sheet")
        text = safe["content"][0]["text"]
        self.assertIn("<td>A1</td>", text)
        self.assertIn("sheet name: [[PG:SHEET:", text)
        self.assertNotIn("Alice", text)
        nested = '<h2>Read Sheet</h2>\n<table><tr><td>A&amp;#108;ice</td></tr></table>\n<li>sheet name: A</li>\n'
        nested_safe = self.proxy._result(excel, reply(nested), "/tmp/copy.xlsx", "excel_read_sheet")
        self.assertNotIn("Alice", nested_safe["content"][0]["text"])
        self.assertNotIn("&#108;", nested_safe["content"][0]["text"])
        pptx = Document("doc_pptx_12345678", self.doc, "pptx")
        escaped = '{"ok":true,"result":{"message":"\\u0041lice"}}'
        safe = self.proxy._result(pptx, reply(escaped), "/tmp/copy.pptx", "pptx_read_slide")
        self.assertNotIn("Alice", safe["content"][0]["text"])

    def test_unapproved_arguments_and_content_types_denied(self):
        self.proxy.sessions["filesystem"] = Session(lambda n, a: reply("Alice"))
        async def flow():
            with self.assertRaises(GatewayError):
                await self.proxy.call("filesystem__read_text_file", {"path": str(self.doc), "document_id": "obsolete"})
            with self.assertRaises(GatewayError):
                await self.proxy.call("filesystem__read_text_file", {"path": str(self.doc), "head": 1, "tail": 1})
            self.proxy.sessions["filesystem"] = Session(lambda n, a: SimpleNamespace(isError=False, content=[SimpleNamespace(type="image", data="secret")], structuredContent=None, meta=None))
            with self.assertRaises(GatewayError):
                await self.proxy.call("filesystem__read_text_file", {"path": str(self.doc)})
        asyncio.run(flow())

    def test_failed_write_does_not_commit(self):
        def call(name, args):
            Path(args["path"]).write_text("Changed")
            return reply("backend failure", error=True)
        self.proxy.sessions["filesystem"] = Session(call)
        async def flow():
            with self.assertRaises(GatewayError):
                await self.proxy.call("filesystem__edit_file", {"path": str(self.doc), "expected_sha256": hashlib.sha256(b"Alice").hexdigest(), "edits": [{"oldText": "Alice", "newText": "Changed"}]})
        asyncio.run(flow())
        self.assertEqual(self.doc.read_text(), "Alice")

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "MCP SDK not installed; live stdio transport unavailable")
    def test_live_synthetic_stdio_roundtrip(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from privacy_gateway.proxy import _upstream_compatible

        async def flow():
            params = StdioServerParameters(command=str(Path(sys.executable).absolute()), args=[str(Path(__file__).with_name("fixture_mcp_server.py"))])
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    schemas = {tool.name: tool.inputSchema for tool in (await session.list_tools()).tools}
                    self.assertTrue(_upstream_compatible("filesystem", "read_text_file", schemas["read_text_file"]))
                    self.assertTrue(_upstream_compatible("filesystem", "edit_file", schemas["edit_file"]))
                    self.proxy.sessions["filesystem"] = session
                    first = await self.proxy.call("filesystem__read_text_file", {"path": str(self.doc)})
                    token = first["content"][0]["text"]
                    self.assertNotIn("Alice", token)
                    edited = await self.proxy.call("filesystem__edit_file", {"path": str(self.doc), "expected_sha256": first["sha256"], "edits": [{"oldText": token, "newText": token + " updated"}]})
                    self.assertEqual(self.doc.read_text(), "Alice updated")
                    self.assertNotEqual(first["sha256"], edited["sha256"])
        asyncio.run(flow())


if __name__ == "__main__":
    unittest.main()
