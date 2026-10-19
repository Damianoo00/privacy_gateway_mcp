"""Container entry point: persist a private key before starting the proxy."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile

from . import cli


def ensure_key(path: Path) -> None:
    """Create a complete key once, without replacing an existing key."""
    if os.path.lexists(path):
        cli._key(str(path))
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".gateway-key-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(os.urandom(32))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)  # Atomic publication; never overwrite a key.
        except FileExistsError:
            pass  # Another starter won; validate its key below.
        cli._key(str(path))
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "serve" and not any(
        flag in sys.argv[2:] for flag in ("--help", "-h")
    ):
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--key-file", required=True)
        args, _ = parser.parse_known_args(sys.argv[2:])
        try:
            ensure_key(Path(args.key_file))
        except Exception:
            print("Private key initialization failed; existing keys are never replaced", file=sys.stderr)
            raise SystemExit(1) from None
    cli.main()


if __name__ == "__main__":
    main()
