"""Restricted HTTP front end for one native stdio MCP server."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from .proxy import Backend, connect_backend


class StdioBridge:
    def __init__(self, backend: Backend, timeout: int = 30):
        if backend.command is None:
            raise ValueError("Bridge requires a stdio server")
        self.backend = backend
        self.timeout = timeout
        self.session = None
        self.tools = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def connected(self):
        async with AsyncExitStack() as stack:
            self.session, found = await connect_backend(stack, self.backend, self.timeout)
            self.tools = {name: found[name] for name in self.backend.tools}
            try:
                yield
            finally:
                self.session = None
                self.tools = {}

    def server(self):
        from mcp import types
        from mcp.server.lowlevel import Server

        server = Server("privacy-gateway-stdio-bridge", version="0.2.0")

        @server.list_tools()
        async def list_tools():
            return list(self.tools.values())

        @server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict | None):
            if name not in self.tools or self.session is None:
                return types.CallToolResult(content=[types.TextContent(type="text", text="Tool unavailable")], isError=True)
            async with self.lock:
                try:
                    return await asyncio.wait_for(self.session.call_tool(name, arguments or {}), timeout=self.timeout)
                except Exception:
                    return types.CallToolResult(content=[types.TextContent(type="text", text="Backend unavailable")], isError=True)

        return server
