"""Synthetic local MCP backend for transport integration tests."""
from pathlib import Path
from mcp.server.fastmcp import FastMCP

server = FastMCP("synthetic-filesystem")


@server.tool()
def read_text_file(path: str, head: int | None = None, tail: int | None = None) -> str:
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if head is not None:
        return "".join(lines[:head])
    if tail is not None:
        return "".join(lines[-tail:])
    return text


@server.tool()
def edit_file(path: str, edits: list[dict[str, str]], dryRun: bool = False) -> str:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    for edit in edits:
        if edit["oldText"] not in text:
            raise ValueError("No match")
        text = text.replace(edit["oldText"], edit["newText"], 1)
    if not dryRun:
        file.write_text(text, encoding="utf-8")
    return "Edited"


if __name__ == "__main__":
    server.run(transport="stdio")
