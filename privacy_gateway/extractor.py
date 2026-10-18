"""Internal restricted parser process; never called as an MCP tool."""
from __future__ import annotations

import resource
import sys

from .core import _extract


def main() -> None:
    try:
        suffix = sys.argv[1]
        max_output = int(sys.argv[2])
        max_source = int(sys.argv[3])
        if suffix not in (".pdf", ".docx", ".xlsx") or max_output < 1 or max_source < 1:
            raise ValueError()
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        data = sys.stdin.buffer.read(max_source + 1)
        if len(data) > max_source:
            raise ValueError()
        text = _extract(data, suffix)
        if len(text) > max_output or len(text.encode("utf-8")) > max_output * 4:
            raise ValueError()
        sys.stdout.buffer.write(text.encode("utf-8"))
    except Exception:
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
