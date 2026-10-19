"""Administrative CLI and stdio MCP entry point."""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import stat
import sys

from .core import Config, Gateway, GatewayError


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


def _server(gateway: Gateway):
    from mcp.server.fastmcp import FastMCP
    server = FastMCP("Privacy Gateway")

    @server.tool()
    def privacy_list_files() -> list[dict]:
        """List opaque IDs of registered sources and sanitized workspace documents."""
        return gateway.list_files()

    @server.tool()
    def privacy_read_file(document_id: str) -> dict:
        """Read sanitized text by opaque document ID."""
        return gateway.read_file(document_id)

    @server.tool()
    def privacy_search(query: str) -> list[dict]:
        """Search sanitized text across accessible documents."""
        return gateway.search(query)

    @server.tool()
    def privacy_get_metadata(document_id: str) -> dict:
        """Get public metadata without a source path or filename."""
        return gateway.metadata(document_id)

    @server.tool()
    def privacy_create_file(content: str) -> dict:
        """Create a sanitized text document in the private workspace."""
        return gateway.create(content)

    @server.tool()
    def privacy_update_file(document_id: str, content: str, expected_version: int) -> dict:
        """Replace a workspace text document after sanitization."""
        return gateway.update(document_id, content, expected_version)

    @server.tool()
    def privacy_export_file(document_id: str) -> dict:
        """Write a sanitized plain-text export inside the workspace."""
        return gateway.export(document_id)

    return server


def main() -> None:
    parser = argparse.ArgumentParser(prog="privacy-gateway")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-key", help="Create a private random key file")
    init.add_argument("key_file")
    for name in ("register", "serve", "serve-proxy"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--key-file", required=True)
        if name == "register":
            command.add_argument("source_file")
    args = parser.parse_args()
    try:
        if args.command == "init-key":
            fd = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
            return
        if args.command == "serve-proxy":
            from .proxy import PrivacyProxy, ProxyConfig, serve_proxy
            asyncio.run(serve_proxy(PrivacyProxy(ProxyConfig.load(args.config), _key(args.key_file))))
            return
        gateway = Gateway(Config.load(args.config), _key(args.key_file))
        if args.command == "register":
            print(gateway.register(args.source_file))
        else:
            _server(gateway).run(transport="stdio")
    except Exception:
        # Never print exception chains or source paths from libraries.
        print("Gateway initialization or operation failed", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
