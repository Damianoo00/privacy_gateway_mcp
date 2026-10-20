"""Check the public MCP endpoint without reading or editing user files."""
import argparse
import asyncio
import sys


async def check(url: str) -> None:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"OK MCP: {len(tools.tools)} available tools")
            for tool in tools.tools:
                print(tool.name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp")
    args = parser.parse_args()
    try:
        asyncio.run(check(args.url))
    except Exception:
        print("MCP connection failed; check proxy health and configuration", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
