#!/usr/bin/env python3
"""Prepare, apply, and restore a locked-down Codex config without dependencies.

Preparation writes a candidate and a plan; it never changes the active config.
Apply checks both hashes, backs up the exact active bytes, then atomically replaces
the config. All output files containing configuration data are mode 0600 on POSIX.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit


DISABLED_FEATURES = (
    "shell_tool", "apps", "plugins", "browser_use", "browser_use_external",
    "hooks", "multi_agent", "skill_mcp_dependency_install", "view_image",
)
REMOVED_ROOT_KEYS = {
    "default_permissions", "permissions", "approval_policy", "sandbox_mode",
    "sandbox_workspace_write", "profiles", "profile", "mcp_servers",
    "features", "tools", "web_search", "plugins", "apps", "agents", "hooks", "projects",
    "notify", "experimental_use_rmcp_client", "model_instructions_file",
    "experimental_instructions_file", "experimental_compact_prompt_file",
    "project_doc_max_bytes", "project_doc_fallback_filenames",
}


class ConfigError(Exception):
    """A recoverable configuration or migration error."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_regular(path: Path, *, missing_ok: bool = False) -> bytes | None:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            raise ConfigError(f"Refusing symlink: {path}")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ConfigError(f"Missing file: {path}") from None
    except OSError as exc:
        raise ConfigError(f"Cannot open regular file {path}: {exc.strerror}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ConfigError(f"Not a regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def _parse_toml(data: bytes, label: str) -> dict:
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ConfigError(f"Invalid TOML in {label}") from None


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(
            f"{_quote(str(key))} = {_toml_value(item)}" for key, item in value.items()
        ) + " }"
    raise ConfigError(f"Unsupported TOML value: {type(value).__name__}")


def _serialize(config: dict) -> bytes:
    content = "".join(f"{_quote(key)} = {_toml_value(value)}\n" for key, value in config.items())
    result = content.encode("utf-8")
    if _parse_toml(result, "generated config") != config:
        raise ConfigError("Generated TOML did not preserve configuration values")
    return result


def _proxy_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("Proxy URL must use HTTP or HTTPS with a hostname")
    if "@" in parsed.netloc or parsed.query or parsed.fragment:
        raise ConfigError("Proxy URL cannot contain credentials, query, or fragment")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError("Proxy URL has an invalid port") from exc
    if parsed.scheme == "http":
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            raise ConfigError("HTTP proxy URL must use a numeric loopback address") from None
        if not address.is_loopback or port is None:
            raise ConfigError("HTTP proxy URL must use a loopback address and explicit port")
    return value


def _protected_paths(values: list[str]) -> list[str]:
    paths: list[str] = []
    for value in values:
        path = Path(value)
        if not path.is_absolute() or ".." in path.parts:
            raise ConfigError(f"Protected path must be absolute and normalized: {value}")
        paths.append(str(path))
    return sorted(set(paths))


def build_config(source: dict, proxy_url: str, protect: list[str]) -> dict:
    """Return a new config while preserving unrelated parsed TOML values."""
    url = _proxy_url(proxy_url)
    paths = _protected_paths(protect)
    result = {key: value for key, value in source.items() if key not in REMOVED_ROOT_KEYS}
    filesystem = {
        ":root": "deny", ":minimal": "read", ":workspace_roots": "deny",
        ":tmpdir": "deny", ":slash_tmp": "deny",
    }
    filesystem.update({path: "deny" for path in paths})
    result.update({
        "default_permissions": "private_proxy",
        "permissions": {"private_proxy": {
            "filesystem": filesystem, "network": {"enabled": False},
        }},
        "approval_policy": "never",
        "mcp_servers": {"private_proxy": {
            "url": url, "required": True, "enabled": True,
        }},
        "features": {feature: False for feature in DISABLED_FEATURES},
        "web_search": "disabled",
        "project_doc_max_bytes": 0,
        "project_doc_fallback_filenames": [],
    })
    if source.get("projects"):
        result["projects"] = {path: {"trust_level": "untrusted"} for path in source["projects"]}
    return result


def _fsync_dir(directory: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ConfigError(f"Refusing to overwrite existing file: {path}") from None
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    finally:
        os.close(fd)
    _fsync_dir(path.parent)


def _replace_atomic(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
        os.replace(temporary_path, path)
        _fsync_dir(path.parent)
    finally:
        os.close(fd)
        temporary_path.unlink(missing_ok=True)


def _absolute(path: str) -> Path:
    return Path(os.path.abspath(os.path.expanduser(path)))


def _distinct(*paths: Path) -> None:
    if len(set(paths)) != len(paths):
        raise ConfigError("Config, candidate, and plan paths must differ")


def prepare(args: argparse.Namespace) -> None:
    config, output, plan = map(_absolute, (args.config, args.output, args.plan))
    _distinct(config, output, plan)
    source = _read_regular(config, missing_ok=True)
    parsed = _parse_toml(source, str(config)) if source is not None else {}
    candidate = _serialize(build_config(parsed, args.proxy_url, args.protect))
    metadata = {
        "version": 1, "config": str(config), "candidate": str(output),
        "source_sha256": _sha(source) if source is not None else None,
        "candidate_sha256": _sha(candidate),
    }
    if output.exists() or output.is_symlink() or plan.exists() or plan.is_symlink():
        raise ConfigError("Candidate and plan paths must be new")
    _write_new(output, candidate)
    try:
        _write_new(plan, (json.dumps(metadata, indent=2) + "\n").encode())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    print(f"Candidate: {output}\nPlan: {plan}")


def _load_plan(path: Path) -> dict:
    try:
        plan = json.loads(_read_regular(path))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"Invalid plan: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise ConfigError("Unsupported plan version")
    for field in ("config", "candidate", "candidate_sha256"):
        if not isinstance(plan.get(field), str):
            raise ConfigError(f"Plan lacks {field}")
    if plan.get("source_sha256") is not None and not isinstance(plan["source_sha256"], str):
        raise ConfigError("Plan has invalid source hash")
    config, candidate = Path(plan["config"]), Path(plan["candidate"])
    if not config.is_absolute() or not candidate.is_absolute():
        raise ConfigError("Plan paths must be absolute")
    _distinct(config, candidate, path)
    return plan


def _check_candidate(data: bytes) -> None:
    config = _parse_toml(data, "candidate")
    profile = config.get("permissions", {}).get("private_proxy", {})
    fs = profile.get("filesystem", {})
    expected = {":root": "deny", ":minimal": "read", ":workspace_roots": "deny",
                ":tmpdir": "deny", ":slash_tmp": "deny"}
    if (config.get("default_permissions") != "private_proxy" or
            config.get("approval_policy") != "never" or
            profile.get("network") != {"enabled": False} or
            any(fs.get(key) != value for key, value in expected.items()) or
            config.get("web_search") != "disabled" or
            config.get("project_doc_max_bytes") != 0 or
            config.get("project_doc_fallback_filenames") != [] or
            any(config.get("features", {}).get(key) is not False for key in DISABLED_FEATURES) or
            set(config.get("mcp_servers", {})) != {"private_proxy"} or
            any(key in config for key in REMOVED_ROOT_KEYS - {
                "default_permissions", "permissions", "approval_policy", "mcp_servers",
                "features", "web_search", "project_doc_max_bytes", "projects",
                "project_doc_fallback_filenames",
            })):
        raise ConfigError("Candidate does not satisfy private proxy restrictions")
    if any(project.get("trust_level") != "untrusted" for project in config.get("projects", {}).values()):
        raise ConfigError("Trusted project config could bypass proxy restrictions")
    server = config["mcp_servers"]["private_proxy"]
    if server.get("required") is not True or server.get("enabled") is not True:
        raise ConfigError("Private proxy MCP must be required and enabled")
    _proxy_url(server.get("url", ""))


def _backup(config: Path, data: bytes) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = config.with_name(f"{config.name}.backup-{stamp}-{secrets.token_hex(4)}")
    _write_new(backup, data)
    return backup


def apply(args: argparse.Namespace) -> None:
    plan = _load_plan(_absolute(args.plan))
    config, candidate_path = Path(plan["config"]), Path(plan["candidate"])
    if config != _absolute(args.config):
        raise ConfigError("Plan targets a different config than the explicit --config")
    candidate = _read_regular(candidate_path)
    if _sha(candidate) != plan["candidate_sha256"]:
        raise ConfigError("Candidate changed since preparation")
    _check_candidate(candidate)
    original = _read_regular(config, missing_ok=True)
    if (_sha(original) if original is not None else None) != plan["source_sha256"]:
        raise ConfigError("Active config changed since preparation")
    backup = _backup(config, original) if original is not None else None
    # Catch ordinary edits during backup; atomic replacement then makes the switch.
    current = _read_regular(config, missing_ok=True)
    if current != original:
        raise ConfigError("Active config changed while backing it up")
    _replace_atomic(config, candidate)
    print(f"Applied: {config}")
    if backup:
        print(f"Backup: {backup}")


def restore(args: argparse.Namespace) -> None:
    config, backup_path = map(_absolute, (args.config, args.backup))
    _distinct(config, backup_path)
    old = _read_regular(backup_path)
    _parse_toml(old, str(backup_path))
    current = _read_regular(config, missing_ok=True)
    saved = _backup(config, current) if current is not None else None
    if _read_regular(config, missing_ok=True) != current:
        raise ConfigError("Active config changed while backing it up")
    _replace_atomic(config, old)
    print(f"Restored: {config}")
    if saved:
        print(f"Previous active config backup: {saved}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="create a reviewable candidate and plan")
    prep.add_argument("--config", required=True)
    prep.add_argument("--output", required=True)
    prep.add_argument("--plan", required=True)
    prep.add_argument("--proxy-url", required=True)
    prep.add_argument("--protect", action="append", default=[], metavar="ABSOLUTE_PATH")
    prep.set_defaults(func=prepare)
    install = sub.add_parser("apply", help="back up and atomically install a candidate")
    install.add_argument("--plan", required=True)
    install.add_argument("--config", required=True)
    install.set_defaults(func=apply)
    revert = sub.add_parser("restore", help="back up active config and restore an older one")
    revert.add_argument("--config", required=True)
    revert.add_argument("--backup", required=True)
    revert.set_defaults(func=restore)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ConfigError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
