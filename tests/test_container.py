from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from privacy_gateway.cli import _key
from privacy_gateway.container import ensure_key, main


class ContainerTests(unittest.TestCase):
    def test_concurrent_initialization_preserves_key_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "private" / "gateway.key"
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(ensure_key, [key] * 8))
            original = _key(str(key))
            ensure_key(key)
            self.assertEqual(_key(str(key)), original)
            self.assertEqual(len(original), 32)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(key.parent.iterdir()), [key])

    def test_existing_invalid_key_and_symlink_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "gateway.key"
            key.write_bytes(b"broken")
            key.chmod(0o600)
            with self.assertRaises(ValueError):
                ensure_key(key)
            self.assertEqual(key.read_bytes(), b"broken")
            link = Path(temp) / "link.key"
            link.symlink_to(key)
            with self.assertRaises(OSError):
                ensure_key(link)
            self.assertTrue(link.is_symlink())

    def test_serve_initializes_key_then_runs_cli(self):
        events = []
        with patch("sys.argv", ["container", "serve", "--key-file=/data/gateway.key"]), \
             patch("privacy_gateway.container.ensure_key", side_effect=lambda path: events.append(path)), \
             patch("privacy_gateway.cli.main", side_effect=lambda: events.append("serve")):
            main()
        self.assertEqual(events, [Path("/data/gateway.key"), "serve"])

    def test_bad_key_fails_closed_without_starting_server(self):
        with patch("sys.argv", ["container", "serve", "--key-file", "/data/gateway.key"]), \
             patch("privacy_gateway.container.ensure_key", side_effect=ValueError("private path")), \
             patch("privacy_gateway.cli.main") as cli, redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 1)
        cli.assert_not_called()
        self.assertNotIn("private path", error.getvalue())

    def test_help_does_not_create_a_key(self):
        with patch("sys.argv", ["container", "serve", "--help"]), \
             patch("privacy_gateway.container.ensure_key") as ensure, patch("privacy_gateway.cli.main") as cli:
            main()
        ensure.assert_not_called()
        cli.assert_called_once_with()
