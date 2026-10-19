"""Synthetic-config tests; never inspect the user's Codex home."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/private-proxy-setup/scripts/configure_codex.py"
SPEC = importlib.util.spec_from_file_location("configure_codex", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


SOURCE = b'''model = "gpt-6-sol"
model_provider = "internal"
approval_policy = "on-request"
sandbox_mode = "workspace-write"
web_search = "live"
model_instructions_file = "/private/instructions.md"
experimental_instructions_file = "/private/experimental.md"
experimental_compact_prompt_file = "/private/compact.md"
project_doc_max_bytes = 32768
project_doc_fallback_filenames = ["SECRET.md"]
[model_providers.internal]
name = "Internal"
base_url = "https://example.test/api"
env_key = "PRIVATE_API_TOKEN"
[mcp_servers.direct]
command = "unsafe-server"
[features]
shell_tool = true
apps = true
[projects."/private/project"]
trust_level = "trusted"
[profiles.unsafe]
sandbox_mode = "danger-full-access"
[sandbox_workspace_write]
network_access = true
'''


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.toml"
        self.candidate = self.root / "candidate.toml"
        self.plan = self.root / "plan.json"
        self.config.write_bytes(SOURCE)

    def prepare(self):
        return mod.main([
            "prepare", "--config", str(self.config), "--output", str(self.candidate),
            "--plan", str(self.plan), "--proxy-url", "http://127.0.0.1:8123/mcp",
            "--protect", str(self.root / "Documents"),
        ])

    def test_prepare_preserves_unrelated_values_and_removes_bypasses(self):
        self.assertEqual(self.prepare(), 0)
        self.assertEqual(self.config.read_bytes(), SOURCE)
        config = tomllib.loads(self.candidate.read_text())
        self.assertEqual(config["model"], "gpt-6-sol")
        self.assertEqual(config["model_providers"]["internal"]["env_key"], "PRIVATE_API_TOKEN")
        self.assertEqual(config["default_permissions"], "private_proxy")
        self.assertEqual(config["permissions"]["private_proxy"]["filesystem"][str(self.root / "Documents")], "deny")
        self.assertEqual(config["permissions"]["private_proxy"]["network"], {"enabled": False})
        self.assertEqual(config["mcp_servers"]["private_proxy"]["required"], True)
        self.assertEqual(set(config["mcp_servers"]), {"private_proxy"})
        self.assertFalse(config["features"]["shell_tool"])
        self.assertEqual(config["projects"]["/private/project"]["trust_level"], "untrusted")
        self.assertEqual(config["project_doc_max_bytes"], 0)
        self.assertEqual(config["project_doc_fallback_filenames"], [])
        for removed in ("sandbox_mode", "sandbox_workspace_write", "profiles",
                        "model_instructions_file", "experimental_instructions_file",
                        "experimental_compact_prompt_file"):
            self.assertNotIn(removed, config)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.candidate.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.plan.stat().st_mode), 0o600)

    def test_apply_backs_up_exact_bytes_and_restore_backs_up_current(self):
        self.assertEqual(self.prepare(), 0)
        self.assertEqual(mod.main(["apply", "--plan", str(self.plan), "--config", str(self.config)]), 0)
        backups = list(self.root.glob("config.toml.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), SOURCE)
        self.assertEqual(self.config.read_bytes(), self.candidate.read_bytes())
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        hardened = self.config.read_bytes()
        self.assertEqual(mod.main([
            "restore", "--config", str(self.config), "--backup", str(backups[0]),
        ]), 0)
        self.assertEqual(self.config.read_bytes(), SOURCE)
        self.assertEqual(len(list(self.root.glob("config.toml.backup-*"))), 2)
        self.assertIn(hardened, [p.read_bytes() for p in self.root.glob("config.toml.backup-*")])

    def test_invalid_source_toml_does_not_write_candidate(self):
        secret = "private-secret-value"
        self.config.write_bytes(f'auth = "{secret}"\n[broken\n'.encode())
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertEqual(self.prepare(), 1)
        self.assertNotIn(secret, errors.getvalue())
        self.assertIn("Invalid TOML", errors.getvalue())
        self.assertFalse(self.candidate.exists())
        self.assertFalse(self.plan.exists())
        self.assertEqual(self.config.read_bytes(), f'auth = "{secret}"\n[broken\n'.encode())

    def test_changed_source_aborts_without_backup(self):
        self.assertEqual(self.prepare(), 0)
        self.config.write_bytes(SOURCE + b"\n# changed\n")
        self.assertEqual(mod.main(["apply", "--plan", str(self.plan), "--config", str(self.config)]), 1)
        self.assertEqual(self.config.read_bytes(), SOURCE + b"\n# changed\n")
        self.assertFalse(list(self.root.glob("config.toml.backup-*")))

    def test_changed_candidate_aborts_without_backup(self):
        self.assertEqual(self.prepare(), 0)
        self.candidate.write_bytes(self.candidate.read_bytes() + b"\n# changed\n")
        self.assertEqual(mod.main(["apply", "--plan", str(self.plan), "--config", str(self.config)]), 1)
        self.assertEqual(self.config.read_bytes(), SOURCE)
        self.assertFalse(list(self.root.glob("config.toml.backup-*")))

    def test_symlink_config_rejected(self):
        target = self.root / "real.toml"
        self.config.rename(target)
        self.config.symlink_to(target)
        self.assertEqual(self.prepare(), 1)
        self.assertEqual(target.read_bytes(), SOURCE)
        self.assertFalse(self.candidate.exists())

    def test_non_loopback_http_proxy_rejected(self):
        self.assertRaises(mod.ConfigError, mod.build_config, {}, "http://example.test:8123/mcp", [])
        self.assertRaises(mod.ConfigError, mod.build_config, {}, "http://127.0.0.1:8123/mcp?token=x", [])

    def test_plan_does_not_contain_original_config_values(self):
        self.assertEqual(self.prepare(), 0)
        plan = json.loads(self.plan.read_text())
        self.assertNotIn("PRIVATE_API_TOKEN", self.plan.read_text())
        self.assertEqual(plan["candidate"], str(self.candidate))
        self.assertEqual(len(plan["source_sha256"]), 64)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_fifo_is_rejected_without_waiting_for_writer(self):
        import subprocess
        import sys

        fifo = self.root / "fifo.toml"
        os.mkfifo(fifo)
        result = subprocess.run([
            sys.executable, str(SCRIPT), "prepare", "--config", str(fifo),
            "--output", str(self.candidate), "--plan", str(self.plan),
            "--proxy-url", "http://127.0.0.1:8000/mcp",
        ], capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.candidate.exists())

    def test_plan_cannot_redirect_apply_to_another_config(self):
        self.assertEqual(self.prepare(), 0)
        other = self.root / "other.toml"
        other.write_bytes(SOURCE)
        plan = json.loads(self.plan.read_text())
        plan["config"] = str(other)
        self.plan.write_text(json.dumps(plan))
        self.assertEqual(mod.main(["apply", "--plan", str(self.plan), "--config", str(self.config)]), 1)
        self.assertEqual(other.read_bytes(), SOURCE)
        self.assertFalse(list(self.root.glob("*.backup-*")))


if __name__ == "__main__":
    unittest.main()
