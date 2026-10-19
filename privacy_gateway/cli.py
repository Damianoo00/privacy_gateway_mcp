"""Key management and HTTP entry point for the MCP proxy."""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import stat
import sys

def _key(path: str) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Key file must be owned by this user and mode 0600")
        with os.fdopen(os.dup(fd), "rb") as stream:
            key = stream.read(33)
    finally:
        os.close(fd)
    if len(key) != 32:
        raise ValueError("Key file must contain 32 bytes")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(prog="privacy-gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-key", help="Create a private random key file")
    init.add_argument("key_file")
    serve = sub.add_parser("serve", help="Serve the configured MCP proxy over HTTP")
    serve.add_argument("--config", required=True)
    serve.add_argument("--key-file", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--allow-non-loopback", action="store_true",
                       help="Allow unauthenticated HTTP outside loopback; only behind a protected network boundary")
    bridge = sub.add_parser("bridge", help="Serve one approved native stdio MCP server over HTTP")
    bridge.add_argument("--config", required=True)
    bridge.add_argument("--server", required=True)
    bridge.add_argument("--host", default="127.0.0.1")
    bridge.add_argument("--port", type=int, default=8101)
    bridge.add_argument("--bearer-token-env", required=True,
                        help="Environment variable containing the bridge HTTP bearer token")
    bridge.add_argument("--allowed-host", action="append", default=[],
                        help="Additional HTTP Host accepted by the bridge, e.g. host.docker.internal:*")
    args = parser.parse_args()
    try:
        if args.command == "init-key":
            fd = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
            return
        if args.command == "bridge":
            from .bridge import StdioBridge
            from .proxy import load_config_file, parse_backend
            from .http import serve_http
            import ipaddress
            import re

            raw = load_config_file(args.config)
            servers = raw.get("mcp_servers", raw.get("backends", {}))
            if not isinstance(servers, dict) or args.server not in servers:
                raise ValueError("Unknown MCP server")
            backend = parse_backend(args.server, servers[args.server], legacy="backends" in raw)
            bridge = StdioBridge(backend)
            token = os.environ.get(args.bearer_token_env)
            if not token or "\r" in token or "\n" in token:
                raise ValueError("Invalid bridge bearer token")
            loopback = args.host == "localhost" or ipaddress.ip_address(args.host).is_loopback
            if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}(?::(?:[0-9]{1,5}|\*))?", host)
                   for host in args.allowed_host):
                raise ValueError("Invalid allowed Host")

            async def run_bridge():
                async with bridge.connected():
                    await serve_http(bridge.server(), args.host, args.port,
                                     allow_non_loopback=not loopback, bearer_token=token,
                                     allowed_hosts=args.allowed_host)

            asyncio.run(run_bridge())
            return
        from .proxy import PrivacyProxy, ProxyConfig, proxy_backends, proxy_server
        from .http import serve_http
        proxy = PrivacyProxy(ProxyConfig.load(args.config), _key(args.key_file))

        async def run_proxy_http():
            async with proxy_backends(proxy):
                await serve_http(proxy_server(proxy), args.host, args.port,
                                 allow_non_loopback=args.allow_non_loopback)

        asyncio.run(run_proxy_http())
    except Exception:
        # Never print exception chains or source paths from libraries.
        print("Gateway initialization or operation failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
