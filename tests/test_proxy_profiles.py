"""Profile contract checks with synthetic sessions, not Office parser tests."""
import asyncio
import html
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from privacy_gateway.proxy import PrivacyProxy, ProxyConfig, TOKEN, _public_schema, _upstream_compatible


class PersonDetector:
    def detect(self, text, language, entities, threshold):
        return [(i, i + 5, "PERSON") for i in range(len(text)) if text.startswith("Alice", i)]


def response(text, structured=None):
    return SimpleNamespace(isError=False, content=[SimpleNamespace(type="text", text=text, meta=None)],
                           structuredContent=structured, meta=None)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name)
        sources = base / "sources"
        sources.mkdir()
        self.paths = {}
        profiles = {
            "docx": ["read_document", "edit_paragraphs"],
            "excel": ["excel_describe_sheets", "excel_read_sheet", "excel_write_to_sheet"],
            "pptx": ["pptx_get_info", "pptx_read_slide", "pptx_edit_text"],
        }
        docs = []
        for backend, suffix in (("docx", ".docx"), ("excel", ".xlsx"), ("pptx", ".pptx")):
            path = sources / (backend + suffix)
            path.write_text("Alice", encoding="utf-8")
            self.paths[backend] = path
            doc = {"id": "doc_contract_" + backend, "path": str(path), "backend": backend}
            if backend == "excel":
                doc["sheets"] = {"sheet1": "People"}
            docs.append(doc)
        raw = {
            "source_roots": [str(sources)], "workspace": str(base / "private"),
            "language": "en", "spacy_model": "en_core_web_lg", "entities": ["PERSON"],
            "score_threshold": .35, "access_scope": "project", "max_source_bytes": 10485760,
            "max_output_chars": 100000, "documents": docs,
            "backends": {name: {"command": str(Path(sys.executable).resolve()), "args": [], "tools": tools}
                         for name, tools in profiles.items()},
        }
        self.proxy = PrivacyProxy(ProxyConfig(raw), bytes(range(32)), PersonDetector())

    def test_office_profiles_restore_tokens_and_commit(self):
        calls = []
        class Session:
            def __init__(self, backend):
                self.backend = backend

            async def call_tool(self, name, arguments):
                calls.append((self.backend, name, arguments))
                path = Path(arguments["fileAbsolutePath" if self.backend == "excel" else "file_path"])
                if name in ("read_document", "excel_read_sheet", "pptx_read_slide"):
                    content = path.read_text()
                else:
                    if self.backend == "docx":
                        content = arguments["edits"][0]["new_text"]
                    elif self.backend == "excel":
                        self_outer.assertEqual(arguments["sheetName"], "People")
                        self_outer.assertIs(arguments["newSheet"], False)
                        content = arguments["values"][0][0]
                    else:
                        content = arguments["text"]
                    path.write_text(content)
                if self.backend == "pptx":
                    return response(json.dumps({"ok": True, "result": {"message": content}}))
                if self.backend == "excel":
                    return response("<h2>Read Sheet</h2><table><tr><td>" + html.escape(content)
                                    + "</td></tr></table><h2>Metadata</h2><ul>"
                                    + "<li>backend: excelize</li><li>sheet name: People</li>"
                                    + "<li>read range: A1</li></ul>")
                return response(content)

        self_outer = self
        for backend in self.paths:
            self.proxy.sessions[backend] = Session(backend)

        async def flow():
            tokens = []
            for backend, read, write, selectors in (
                ("docx", "read_document", "edit_paragraphs", {}),
                ("excel", "excel_read_sheet", "excel_write_to_sheet", {"sheet": "sheet1", "range": "A1"}),
                ("pptx", "pptx_read_slide", "pptx_edit_text", {"slide_index": 0}),
            ):
                document_id = "doc_contract_" + backend
                first = await self.proxy.call(backend + "__" + read, {"document_id": document_id, **selectors})
                text = first["content"][0]["text"]
                token = TOKEN.search(text).group()
                self.assertNotIn("Alice", text)
                tokens.append(token)
                arguments = {"document_id": document_id, "expected_sha256": first["sha256"], **selectors}
                if backend == "docx":
                    arguments["edits"] = [{"paragraph_index": 0, "new_text": token + " updated"}]
                elif backend == "excel":
                    arguments["values"] = [[token + " updated"]]
                else:
                    arguments.update(shape_index=0, text=token + " updated")
                changed = await self.proxy.call(backend + "__" + write, arguments)
                self.assertNotEqual(first["sha256"], changed["sha256"])
                self.assertEqual(self.paths[backend].read_text(), "Alice updated")
                self.assertNotIn("Alice", str(changed))
                if backend == "docx":
                    upstream = calls[-1][2]
                    self.assertIs(upstream["track_changes"], False)
                    self.assertIs(upstream["allow_untracked_edit"], True)
                    second = await self.proxy.call("docx__edit_paragraphs", {
                        "document_id": document_id, "expected_sha256": changed["sha256"],
                        "edits": [{"paragraph_index": 0, "new_text": token + " twice"}],
                    })
                    self.assertEqual(self.paths[backend].read_text(), "Alice twice")
                    self.assertNotEqual(changed["sha256"], second["sha256"])
            self.assertEqual(len(set(tokens)), 1, "Pseudonyms must be stable within the project")
        asyncio.run(flow())
        for backend, _, arguments in calls:
            self.assertNotEqual(str(self.paths[backend]), arguments.get("file_path", arguments.get("fileAbsolutePath")))

    def test_excel_schema_compatibility_is_per_tool(self):
        def schema(fields, required):
            return {"type": "object", "properties": {field: {} for field in fields}, "required": required}
        self.assertTrue(_upstream_compatible("excel", "excel_describe_sheets", schema(["fileAbsolutePath"], ["fileAbsolutePath"])))
        self.assertTrue(_upstream_compatible("excel", "excel_read_sheet", schema(
            ["fileAbsolutePath", "sheetName", "range", "showFormula", "showStyle"], ["fileAbsolutePath", "sheetName"])))
        self.assertTrue(_upstream_compatible("excel", "excel_write_to_sheet", schema(
            ["fileAbsolutePath", "sheetName", "newSheet", "range", "values"],
            ["fileAbsolutePath", "sheetName", "newSheet", "range", "values"])))

    def test_public_edit_schema_describes_nested_fields(self):
        schema = _public_schema("docx", "edit_paragraphs")
        edit = schema["properties"]["edits"]["items"]
        self.assertIn("new_text", edit["properties"])
        self.assertIn("paragraph_index", edit["properties"])
        self.assertIn("anchor", edit["properties"])


if __name__ == "__main__":
    unittest.main()
