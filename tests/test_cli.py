"""Public CLI routes only to the proxy HTTP service."""
from contextlib import asynccontextmanager, redirect_stderr
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, sentinel


class CLITests(unittest.TestCase):
    def test_serve_runs_proxy_http_with_backend_lifetime(self):
        from privacy_gateway.cli import main

        events = []

        @asynccontextmanager
        async def backends(proxy):
            self.assertIs(proxy, sentinel.proxy)
            events.append("start")
            try:
                yield
            finally:
                events.append("stop")

        async def serve(server, host, port, *, allow_non_loopback):
            self.assertIs(server, sentinel.server)
            self.assertEqual((host, port, allow_non_loopback), ("127.0.0.1", 9876, False))
            events.append("http")

        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "key"
            key.write_bytes(bytes(range(32)))
            key.chmod(0o600)
            with patch("sys.argv", ["privacy-gateway", "serve", "--config", "proxy.json",
                                    "--key-file", str(key), "--port", "9876"]), \
                 patch("privacy_gateway.proxy.ProxyConfig.load", return_value=sentinel.config), \
                 patch("privacy_gateway.proxy.PrivacyProxy", return_value=sentinel.proxy) as constructor, \
                 patch("privacy_gateway.proxy.proxy_backends", side_effect=backends), \
                 patch("privacy_gateway.proxy.proxy_server", return_value=sentinel.server), \
                 patch("privacy_gateway.http.serve_http", side_effect=serve):
                main()
            constructor.assert_called_once_with(sentinel.config, bytes(range(32)))
        self.assertEqual(events, ["start", "http", "stop"])

    def test_old_commands_are_rejected(self):
        from privacy_gateway.cli import main

        for command in ("register", "serve-proxy"):
            with self.subTest(command=command), patch("sys.argv", ["privacy-gateway", command]), \
                 redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                main()
            self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
