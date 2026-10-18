import json
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from privacy_gateway.core import Config, Gateway, GatewayError


class FakeDetector:
    identity = "test-detector-v1"

    def detect(self, text, language, entities, threshold):
        needle = "Alice"
        return [(i, i + len(needle), "PERSON") for i in range(len(text)) if text.startswith(needle, i)]


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sources = self.base / "sources"
        self.sources.mkdir()
        self.workspace = self.base / "workspace"
        self.file = self.sources / "note.txt"
        self.file.write_text("Alice met Alice", encoding="utf-8")
        config_file = self.base / "config.json"
        config_file.write_text(json.dumps({
            "source_roots": [str(self.sources)], "workspace": str(self.workspace),
            "language": "en", "spacy_model": "en_core_web_lg", "policy_version": "1",
            "entities": ["PERSON"], "score_threshold": 0.35,
            "cache_ttl_seconds": 3600, "max_source_bytes": 10000,
            "max_output_chars": 10000, "access_scope": "test"
        }))
        self.config = Config.load(str(config_file))
        self.gateway = Gateway(self.config, bytes(range(32)), FakeDetector())

    def test_end_to_end_sanitized_and_encrypted(self):
        ident = self.gateway.register(str(self.file))
        result = self.gateway.read_file(ident)
        self.assertNotIn("Alice", result["content"])
        self.assertEqual(result["content"].count("<PERSON_"), 2)
        self.assertEqual(len(set(result["content"].split(" met "))), 1)
        self.assertEqual(self.gateway.search("Alice"), [])
        self.assertEqual(len(self.gateway.search("PERSON_")), 1)
        self.assertNotIn("note.txt", str(self.gateway.list_files()))
        self.assertNotIn("note.txt", str(self.gateway.metadata(ident)))
        output = self.gateway.create("Alice private")
        self.assertNotIn("Alice", self.gateway.read_file(output["id"])["content"])
        self.assertEqual(self.gateway.update(output["id"], "Alice changed", 1)["version"], 2)
        with self.assertRaises(GatewayError):
            self.gateway.update(output["id"], "bad", 1)
        exported = self.gateway.export(output["id"])
        content = (self.workspace / "exports" / exported["export_name"]).read_text()
        self.assertNotIn("Alice", content)
        self.assertEqual(self.file.read_text(), "Alice met Alice")
        raw_db = (self.workspace / "gateway.sqlite3").read_bytes()
        self.assertNotIn(b"Alice", raw_db)
        self.assertNotIn(b"note.txt", raw_db)

    def test_symlink_swap_denied_even_with_cache(self):
        ident = self.gateway.register(str(self.file))
        self.gateway.read_file(ident)
        self.file.unlink()
        outside = self.base / "outside.txt"
        outside.write_text("SECRET")
        self.file.symlink_to(outside)
        with self.assertRaises(GatewayError):
            self.gateway.read_file(ident)

    def test_source_root_ancestor_swap_denied(self):
        anchor = self.base / "anchor"
        nested = anchor / "sources"
        nested.mkdir(parents=True)
        document = nested / "note.txt"
        document.write_text("Alice")
        gateway = Gateway(replace(self.config, roots=(nested,)), bytes(range(32)), FakeDetector())
        ident = gateway.register(str(document))
        gateway.read_file(ident)
        anchor.rename(self.base / "old-anchor")
        replacement = self.base / "replacement"
        (replacement / "sources").mkdir(parents=True)
        (replacement / "sources" / "note.txt").write_text("SECRET")
        anchor.symlink_to(replacement, target_is_directory=True)
        with self.assertRaises(GatewayError):
            gateway.read_file(ident)

    def test_source_change_invalidates_cache(self):
        ident = self.gateway.register(str(self.file))
        first = self.gateway.read_file(ident)["content"]
        self.file.write_text("Bob", encoding="utf-8")
        self.assertNotEqual(first, self.gateway.read_file(ident)["content"])

    def test_path_and_source_update_denied(self):
        outside = self.base / "outside.txt"
        outside.write_text("secret")
        with self.assertRaises(GatewayError):
            self.gateway.register(str(outside))
        link = self.sources / "link.txt"
        link.symlink_to(outside)
        with self.assertRaises(GatewayError):
            self.gateway.register(str(link))
        ident = self.gateway.register(str(self.file))
        with self.assertRaises(GatewayError):
            self.gateway.update(ident, "replacement", 1)

    def test_bad_detector_span_fails_closed(self):
        class BadDetector(FakeDetector):
            def detect(self, *args):
                return [(0, 1000000, "PERSON")]
        self.gateway.detector = BadDetector()
        ident = self.gateway.register(str(self.file))
        with self.assertRaises(GatewayError):
            self.gateway.read_file(ident)

    def test_overlapping_spans_cover_full_union(self):
        class OverlapDetector(FakeDetector):
            def detect(self, *args):
                return [(0, 5, "PERSON"), (3, 10, "PERSON")]
        self.gateway.detector = OverlapDetector()
        result = self.gateway._sanitize("Alice met ")
        self.assertNotIn("Alice", result)
        self.assertNotIn(" met ", result)
        self.assertIn("<PII_", result)

    def test_parser_version_key_does_not_shadow_document_version(self):
        ident = self.gateway.register(str(self.file))
        with self.gateway._db() as db:
            sealed = self.gateway._seal(json.dumps([0, "note.pdf"]).encode(), ident)
            db.execute("UPDATE documents SET source=? WHERE id=?", (sealed, ident))
        (self.sources / "note.pdf").write_bytes(b"fake pdf")
        with patch("privacy_gateway.core._extract_bounded", return_value="Alice"):
            self.assertNotIn("Alice", self.gateway.read_file(ident)["content"])

    def test_corrupt_cache_rejected_and_policy_change_recomputes(self):
        ident = self.gateway.register(str(self.file))
        self.gateway.read_file(ident)
        with self.gateway._db() as db:
            db.execute("UPDATE cache SET payload=?", (b"tampered",))
        with self.assertRaises(GatewayError):
            self.gateway.read_file(ident)
        self.gateway.config = replace(self.config, policy_version="2")
        self.assertNotIn("Alice", self.gateway.read_file(ident)["content"])

    def test_detector_error_fails_closed(self):
        class BrokenDetector(FakeDetector):
            def detect(self, *args):
                raise RuntimeError("private error Alice")
        self.gateway.detector = BrokenDetector()
        ident = self.gateway.register(str(self.file))
        with self.assertRaisesRegex(GatewayError, "PII detection unavailable") as caught:
            self.gateway.read_file(ident)
        self.assertNotIn("Alice", str(caught.exception))

    def test_cache_expiry_and_detector_identity_recompute(self):
        class CountingDetector(FakeDetector):
            calls = 0
            def detect(self, *args):
                self.calls += 1
                return super().detect(*args)
        detector = CountingDetector()
        self.gateway.detector = detector
        ident = self.gateway.register(str(self.file))
        self.gateway.read_file(ident)
        self.gateway.read_file(ident)
        self.assertEqual(detector.calls, 1)
        with self.gateway._db() as db:
            db.execute("UPDATE cache SET expires=0")
        self.gateway.read_file(ident)
        self.assertEqual(detector.calls, 2)
        detector.identity = "test-detector-v2"
        self.gateway.read_file(ident)
        self.assertEqual(detector.calls, 3)

    def test_concurrent_update_conflict(self):
        ident = self.gateway.create("initial")["id"]
        def attempt(value):
            try:
                self.gateway.update(ident, value, 1)
                return True
            except GatewayError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("one", "two")))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.gateway.metadata(ident)["version"], 2)

    def test_parser_slot_admission_and_release(self):
        from privacy_gateway import core
        class Slot:
            admitted = False
            releases = 0
            def acquire(self, timeout):
                self.assert_timeout = timeout
                return self.admitted
            def release(self):
                self.releases += 1
        slot = Slot()
        with patch.object(core, "_PARSER_SLOT", slot), patch.object(core.subprocess, "run") as run:
            with self.assertRaises(GatewayError):
                core._extract_bounded(b"data", ".pdf", 100, 100)
            run.assert_not_called()
            self.assertEqual(slot.releases, 0)
            slot.admitted = True
            run.return_value.returncode = 0
            run.return_value.stdout = b"safe"
            self.assertEqual(core._extract_bounded(b"data", ".pdf", 100, 100), "safe")
            self.assertEqual(slot.releases, 1)
            run.side_effect = RuntimeError("parser failed")
            with self.assertRaises(GatewayError):
                core._extract_bounded(b"data", ".pdf", 100, 100)
            self.assertEqual(slot.releases, 2)


if __name__ == "__main__":
    unittest.main()
