"""Local Streamable HTTP transport for the MCP proxy."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import ipaddress


def create_http_app(server, *, bearer_token: str | None = None, allowed_hosts: list[str] | None = None):
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*", "[::1]", "[::1]:*", *(allowed_hosts or [])],
        allowed_origins=[
            "http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*",
            "https://localhost:*", "https://127.0.0.1:*", "https://[::1]:*",
        ],
    )
    manager = StreamableHTTPSessionManager(app=server, stateless=True, json_response=True, security_settings=security)

    class MCPApp:
        async def __call__(self, scope, receive, send):
            if bearer_token is not None:
                supplied = dict(scope.get("headers", [])).get(b"authorization", b"")
                expected = ("Bearer " + bearer_token).encode("utf-8")
                if not hmac.compare_digest(supplied, expected):
                    await PlainTextResponse("Unauthorized", status_code=401)(scope, receive, send)
                    return
            await manager.handle_request(scope, receive, send)

    async def healthz(_request):
        return PlainTextResponse("ok")

    @asynccontextmanager
    async def lifespan(_app):
        async with manager.run():
            yield

    return Starlette(routes=[Route("/mcp", MCPApp()), Route("/healthz", healthz, methods=["GET"])], lifespan=lifespan)


async def serve_http(server, host: str, port: int, *, allow_non_loopback: bool = False,
                     bearer_token: str | None = None, allowed_hosts: list[str] | None = None) -> None:
    if not allow_non_loopback and host != "localhost" and not ipaddress.ip_address(host).is_loopback:
        raise ValueError("Non-loopback HTTP requires an explicit protected-network override")
    if not 0 <= port <= 65535:
        raise ValueError("Invalid HTTP port")
    import uvicorn

    app = create_http_app(server, bearer_token=bearer_token, allowed_hosts=allowed_hosts)
    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False,
        timeout_graceful_shutdown=10,
    )
    await uvicorn.Server(config).serve()
