"""Allowlisted, local MCP proxy for registered office documents."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import sqlite3
import stat
import tempfile
import tomllib
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

from cryptography.fernet import Fernet

from .core import GatewayError, PresidioDetector

DENIED = "Operation denied or document unavailable"
TOKEN = re.compile(r"\[\[PG:([A-Z_]+):([0-9a-f]{32})\]\]")
URL = re.compile(r"(?i)\b(?:https?://|file://|data:|www\.)[^\s<>\"']+")
ABS_PATH = re.compile(r"(?<![\w])/(?:[^\s<>\"']+/)+[^\s<>\"']+")
CELL_RANGE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}(?::[A-Z]{1,3}[1-9][0-9]{0,6})?$")
ANCHOR = re.compile(r"^[A-Fa-f0-9]{8}$")
DOC_ID = re.compile(r"^doc_[A-Za-z0-9_-]{8,64}$")

# The names and argument keys are those of the upstream projects, not a generic MCP passthrough.
PROFILES = {
    "docx": {
        "read_document": ("read", {"start_paragraph": "index", "end_paragraph": "index", "show_anchors": "bool"}),
        "edit_paragraphs": ("write", {"edits": "paragraph_edits"}),
    },
    "excel": {
        "excel_describe_sheets": ("read", {}),
        "excel_read_sheet": ("read", {"sheet": "sheet", "range": "range"}),
        "excel_write_to_sheet": ("write", {"sheet": "sheet", "range": "range", "values": "values"}),
    },
    "pptx": {
        "pptx_get_info": ("read", {}),
        "pptx_read_slide": ("read", {"slide_index": "index"}),
        "pptx_edit_text": ("write", {"slide_index": "index", "shape_index": "index", "paragraph_index": "index", "text": "text"}),
    },
    "filesystem": {
        "read_text_file": ("read", {"head": "positive", "tail": "positive"}),
        "edit_file": ("write", {"edits": "text_edits"}),
    },
}
PATH_KEYS = {"docx": "file_path", "excel": "fileAbsolutePath", "pptx": "file_path", "filesystem": "path"}
REQUIRED = {
    "docx": {"edit_paragraphs": {"edits"}},
    "excel": {"excel_read_sheet": {"sheet"}, "excel_write_to_sheet": {"sheet", "range", "values"}},
    "pptx": {"pptx_read_slide": {"slide_index"}, "pptx_edit_text": {"slide_index", "shape_index", "text"}},
    "filesystem": {"edit_file": {"edits"}},
}
SUFFIXES = {"docx": {".docx"}, "excel": {".xlsx"}, "pptx": {".pptx"}, "filesystem": {".txt", ".md", ".csv", ".html"}}


def _deny() -> GatewayError:
    return GatewayError(DENIED)


def _object(value: Any, keys: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict) or set(value) - keys or not required <= set(value):
        raise _deny()
    return value


def _string(value: Any, limit: int = 100000) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise _deny()
    return value


def _index(value: Any, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > 1000000:
        raise _deny()
    return value


@dataclass(frozen=True)
class Backend:
    profile: str
    tools: tuple[str, ...]
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    url: str | None = None
    bearer_token_env: str | None = None
    workspace_path: str | None = None


SERVER_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_backend(name: str, item: dict, *, legacy: bool = False) -> Backend:
    if not isinstance(name, str) or not SERVER_ID.fullmatch(name) or "__" in name:
        raise ValueError("Invalid MCP server ID")
    _object(item, {"profile", "enabled_tools", "tools", "command", "args", "env", "url", "bearer_token_env", "workspace_path"})
    profile = item.get("profile", name if legacy else None)
    if profile not in PROFILES:
        raise ValueError("Unknown backend profile")
    if "enabled_tools" in item and "tools" in item:
        raise ValueError("Specify one tool allowlist")
    tools = item.get("enabled_tools", item.get("tools"))
    if not isinstance(tools, list) or not tools or any(not isinstance(t, str) or t not in PROFILES[profile] for t in tools) or len(set(tools)) != len(tools):
        raise ValueError("Invalid tool allowlist")
    has_url = "url" in item
    has_command = "command" in item
    if has_url == has_command:
        raise ValueError("MCP server needs exactly one transport")
    if has_url:
        if any(k in item for k in ("args", "env")):
            raise ValueError("HTTP MCP server cannot specify stdio settings")
        url = item["url"]
        if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
            raise ValueError("Invalid MCP server URL")
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Invalid MCP server URL")
        try:
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("Invalid MCP server port")
        except ValueError as exc:
            raise ValueError("Invalid MCP server port") from exc
        token_env = item.get("bearer_token_env")
        if token_env is not None and (not isinstance(token_env, str) or not ENV_NAME.fullmatch(token_env)):
            raise ValueError("Invalid bearer token environment variable")
        workspace_path = item.get("workspace_path")
        if workspace_path is not None:
            if not isinstance(workspace_path, str) or "\x00" in workspace_path:
                raise ValueError("Invalid backend workspace path")
            remote = Path(workspace_path) if workspace_path.startswith("/") else PureWindowsPath(workspace_path)
            if not remote.is_absolute() or ".." in remote.parts:
                raise ValueError("Invalid backend workspace path")
        return Backend(profile, tuple(tools), url=url, bearer_token_env=token_env, workspace_path=workspace_path)
    if "bearer_token_env" in item or "workspace_path" in item:
        raise ValueError("Stdio MCP server cannot specify HTTP authorization")
    command = Path(item["command"])
    if not command.is_absolute() or ".." in command.parts or not command.resolve().is_file():
        raise ValueError("Backend executable must be a local absolute path")
    args = item.get("args", [])
    env = item.get("env", {})
    if not isinstance(args, list) or any(not isinstance(a, str) or "\x00" in a for a in args):
        raise ValueError("Invalid backend arguments")
    if not isinstance(env, dict) or any(not isinstance(k, str) or not ENV_NAME.fullmatch(k) or not isinstance(v, str) or "\x00" in v for k, v in env.items()):
        raise ValueError("Invalid backend environment")
    return Backend(profile, tuple(tools), str(command), tuple(args), dict(env))


@dataclass(frozen=True)
class Document:
    ident: str
    path: Path
    backend: str
    sheets: dict[str, str]


class ProxyConfig:
    def __init__(self, raw: dict):
        _object(raw, {"source_roots", "workspace", "language", "spacy_model", "entities", "score_threshold", "access_scope", "max_source_bytes", "max_output_chars", "timeout_seconds", "backends", "mcp_servers", "documents"},
                {"source_roots", "workspace", "language", "spacy_model", "entities", "score_threshold", "access_scope", "max_source_bytes", "max_output_chars"})
        if "backends" in raw and "mcp_servers" in raw:
            raise ValueError("Specify one MCP server map")
        self.roots = tuple(Path(p) for p in raw["source_roots"])
        if not self.roots or any(not p.is_absolute() or ".." in p.parts or not p.is_dir() or p.is_symlink() for p in self.roots):
            raise ValueError("Invalid source roots")
        self.workspace = Path(raw["workspace"])
        if not self.workspace.is_absolute() or ".." in self.workspace.parts or self.workspace.is_symlink() or any(self.workspace == p or self.workspace.is_relative_to(p) or p.is_relative_to(self.workspace) for p in self.roots):
            raise ValueError("Invalid workspace")
        self.language = _string(raw["language"], 8)
        self.spacy_model = _string(raw["spacy_model"], 100)
        self.entities = tuple(raw["entities"])
        if not self.entities or any(not isinstance(e, str) or not re.fullmatch("[A-Z_]+", e) for e in self.entities):
            raise ValueError("Invalid entities")
        self.threshold = float(raw["score_threshold"])
        self.scope = _string(raw["access_scope"], 64)
        self.max_source = raw["max_source_bytes"]
        self.max_output = raw["max_output_chars"]
        if type(self.max_source) is not int or not 1 <= self.max_source <= 100_000_000 or type(self.max_output) is not int or not 1 <= self.max_output <= 10_000_000:
            raise ValueError("Invalid size limits")
        self.timeout = _index(raw.get("timeout_seconds", 30), positive=True)
        if not 0 <= self.threshold <= 1 or not re.fullmatch("[A-Za-z0-9_-]+", self.scope) or self.timeout > 300:
            raise ValueError("Invalid proxy policy")
        servers = raw.get("mcp_servers", raw.get("backends", {}))
        if not isinstance(servers, dict):
            raise ValueError("Invalid MCP server map")
        self.backends = {name: parse_backend(name, item, legacy="backends" in raw) for name, item in servers.items()}
        self.documents = {}
        seen_paths = set()
        seen_files = set()
        for item in raw.get("documents", []):
            _object(item, {"id", "path", "backend", "sheets"}, {"id", "path", "backend"})
            ident, path, backend = item["id"], Path(item["path"]), item["backend"]
            if not isinstance(ident, str) or not DOC_ID.fullmatch(ident) or ident in self.documents or backend not in self.backends or not path.is_absolute() or ".." in path.parts or path.suffix.lower() not in SUFFIXES[self.backends[backend].profile]:
                raise ValueError("Invalid document registration")
            sheets = item.get("sheets", {})
            if not isinstance(sheets, dict) or any(not re.fullmatch("[A-Za-z0-9_-]{1,64}", k) or not isinstance(v, str) or not v or len(v) > 31 for k, v in sheets.items()) or len(set(sheets.values())) != len(sheets):
                raise ValueError("Invalid sheet aliases")
            if self.backends[backend].profile == "excel" and not sheets:
                raise ValueError("Excel documents need explicit sheet aliases")
            if path in seen_paths:
                raise ValueError("Duplicate document path")
            seen_paths.add(path)
            self.documents[ident] = Document(ident, path, backend, sheets)
        self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.workspace.resolve() != self.workspace or any(root.resolve() != root for root in self.roots):
            raise ValueError("Symlink in configured directory")
        if any(self.workspace == root or self.workspace.is_relative_to(root) or root.is_relative_to(self.workspace) for root in self.roots):
            raise ValueError("Workspace overlaps a source root")
        os.chmod(self.workspace, 0o700)
        for doc in self.documents.values():
            self.source(doc)  # startup path and size preflight
            identity = (doc.path.stat().st_dev, doc.path.stat().st_ino)
            if identity in seen_files:
                raise ValueError("Duplicate document file")
            seen_files.add(identity)

    @classmethod
    def load(cls, path: str) -> "ProxyConfig":
        return cls(load_config_file(path))


    def source(self, doc: Document) -> bytes:
        """Open every path component without following a link, then read a regular file."""
        try:
            if not any(doc.path.is_relative_to(root) for root in self.roots):
                raise _deny()
            parts = doc.path.parts[1:]
            fds = [os.open("/", os.O_RDONLY | os.O_DIRECTORY)]
            try:
                for part in parts[:-1]:
                    fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
                fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fds[-1])
                fds.append(fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > self.max_source:
                    raise _deny()
                with os.fdopen(os.dup(fd), "rb") as stream:
                    data = stream.read(self.max_source + 1)
                if len(data) > self.max_source:
                    raise _deny()
                return data
            finally:
                for fd in reversed(fds):
                    os.close(fd)
        except Exception as exc:
            raise _deny() from exc


def load_config_file(path: str) -> dict:
    data = Path(path).read_bytes()
    return tomllib.loads(data.decode("utf-8")) if Path(path).suffix.lower() == ".toml" else json.loads(data)


class Vault:
    def __init__(self, workspace: Path, key: bytes, scope: str):
        if len(key) != 32:
            raise ValueError("Key must be 32 bytes")
        self.key, self.scope = key, scope
        self.fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key + b"proxy-vault").digest()))
        self.db = workspace / "proxy_vault.sqlite3"
        if self.db.is_symlink():
            raise ValueError("Vault path is a symlink")
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS tokens (token TEXT PRIMARY KEY, value BLOB NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS grants (token TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(token,document))")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.db, timeout=10)
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def issue(self, doc: str, entity: str, value: str) -> str:
        digest = hmac.new(self.key, (self.scope + "\0" + entity + "\0" + value).encode(), hashlib.sha256).hexdigest()[:32]
        token = f"[[PG:{entity}:{digest}]]"
        payload = self.fernet.encrypt((self.scope + "\0" + value).encode())
        with self.connection() as con:
            con.execute("INSERT OR IGNORE INTO tokens VALUES (?,?)", (token, payload))
            con.execute("INSERT OR IGNORE INTO grants VALUES (?,?)", (token, doc))
        return token

    def restore(self, doc: str, text: str) -> str:
        def replace(match):
            token = match.group()
            with self.connection() as con:
                row = con.execute("SELECT value FROM tokens WHERE token=? AND EXISTS (SELECT 1 FROM grants WHERE token=? AND document=?)", (token, token, doc)).fetchone()
            if row is None:
                raise _deny()
            try:
                scope, value = self.fernet.decrypt(row[0]).decode().split("\0", 1)
                if scope != self.scope:
                    raise _deny()
                expected = hmac.new(self.key, (scope + "\0" + match.group(1) + "\0" + value).encode(), hashlib.sha256).hexdigest()[:32]
                if not hmac.compare_digest(expected, match.group(2)):
                    raise _deny()
                return value
            except Exception as exc:
                raise _deny() from exc
        if "[[PG:" in TOKEN.sub("", text):
            raise _deny()
        return TOKEN.sub(replace, text)


class PrivacyProxy:
    def __init__(self, config: ProxyConfig, key: bytes, detector=None):
        self.config = config
        self.detector = detector if detector is not None else PresidioDetector(config.language, config.spacy_model)
        if detector is None:
            supported = set(self.detector.engine.registry.get_supported_entities(language=config.language))
            if not set(config.entities) <= supported:
                raise RuntimeError("Configured detection entity unavailable")
        self.vault = Vault(config.workspace, key, config.scope)
        self.sessions: dict[str, Any] = {}
        self.locks = {ident: asyncio.Lock() for ident in config.documents}
        self.backend_locks = {name: asyncio.Lock() for name in config.backends}
        self.failed_backends: set[str] = set()

    def public_tools(self) -> list[str]:
        return [f"{name}__{tool}" for name, backend in self.config.backends.items() for tool in backend.tools]

    def _backend_path(self, backend: Backend, local_path: str) -> str:
        if backend.workspace_path is None:
            return local_path
        relative = Path(local_path).relative_to(self.config.workspace)
        root = Path(backend.workspace_path) if backend.workspace_path.startswith("/") else PureWindowsPath(backend.workspace_path)
        return str(root.joinpath(*relative.parts))

    def _validate(self, doc: Document, tool: str, arguments: dict) -> tuple[str, dict]:
        profile = self.config.backends[doc.backend].profile
        kind, fields = PROFILES[profile][tool]
        _object(arguments, set(fields) | {"document_id", "expected_sha256"}, {"document_id"} | REQUIRED.get(profile, {}).get(tool, set()) | ({"expected_sha256"} if kind == "write" else set()))
        if arguments["document_id"] != doc.ident:
            raise _deny()
        if kind == "write" and (not isinstance(arguments["expected_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", arguments["expected_sha256"])):
            raise _deny()
        out = {}
        for name, field in fields.items():
            if name not in arguments:
                continue
            value = arguments[name]
            if field == "index":
                out[name] = _index(value)
            elif field == "positive":
                out[name] = _index(value, positive=True)
            elif field == "bool":
                if type(value) is not bool:
                    raise _deny()
                out[name] = value
            elif field == "range":
                if not isinstance(value, str) or not CELL_RANGE.fullmatch(value):
                    raise _deny()
                out[name] = value
            elif field == "sheet":
                if value not in doc.sheets:
                    raise _deny()
                out["sheetName"] = doc.sheets[value]
            elif field == "text":
                out[name] = self.vault.restore(doc.ident, _string(value, self.config.max_output))
            elif field == "paragraph_edits":
                if not isinstance(value, list) or not 1 <= len(value) <= 100:
                    raise _deny()
                edits = []
                for edit in value:
                    _object(edit, {"paragraph_index", "anchor", "new_text"}, {"new_text"})
                    if ("paragraph_index" in edit) == ("anchor" in edit):
                        raise _deny()
                    where = {"paragraph_index": _index(edit["paragraph_index"])} if "paragraph_index" in edit else {"anchor": edit["anchor"]}
                    if "anchor" in where and (not isinstance(where["anchor"], str) or not ANCHOR.fullmatch(where["anchor"])):
                        raise _deny()
                    edits.append({**where, "new_text": self.vault.restore(doc.ident, _string(edit["new_text"], self.config.max_output))})
                out[name] = edits
            elif field == "text_edits":
                if not isinstance(value, list) or not 1 <= len(value) <= 100:
                    raise _deny()
                edits = []
                for edit in value:
                    _object(edit, {"oldText", "newText"}, {"oldText", "newText"})
                    edits.append({k: self.vault.restore(doc.ident, _string(edit[k], self.config.max_output)) for k in ("oldText", "newText")})
                out[name] = edits
            elif field == "values":
                if not isinstance(value, list) or not 1 <= len(value) <= 100 or any(not isinstance(row, list) or not 1 <= len(row) <= 100 for row in value):
                    raise _deny()
                if sum(map(len, value)) > 1000:
                    raise _deny()
                rows = []
                for row in value:
                    cells = []
                    for cell in row:
                        if cell is None or type(cell) in (bool, int, float):
                            cells.append(cell)
                        elif isinstance(cell, str):
                            restored = self.vault.restore(doc.ident, _string(cell, self.config.max_output))
                            if restored.startswith("="):
                                raise _deny()  # formula injection is outside the text-only policy
                            if TOKEN.fullmatch(cell) and cell.startswith("[[PG:NUMBER:") and re.fullmatch(r"-?\d{9,19}", restored):
                                cells.append(int(restored))
                            else:
                                cells.append(restored)
                        else:
                            raise _deny()
                    rows.append(cells)
                out[name] = rows
        if profile == "docx" and tool == "edit_paragraphs":
            # A tracked edit leaves pending revisions, and this upstream rejects
            # the next edit of that paragraph. Use its explicit untracked mode
            # for repeatable plain-text edits through the gateway.
            out["track_changes"] = False
            out["allow_untracked_edit"] = True
        elif profile == "excel":
            out["fileAbsolutePath"] = ""  # replaced by transaction path
            if tool == "excel_write_to_sheet":
                out["newSheet"] = False
            if tool == "excel_read_sheet":
                out["showFormula"] = False
                out["showStyle"] = False
        elif profile == "filesystem" and tool == "read_text_file" and "head" in out and "tail" in out:
            raise _deny()
        return kind, out

    def _safe_text(self, doc: Document, text: str, path: str) -> str:
        if len(text) > self.config.max_output:
            raise _deny()
        for secret_path in (str(doc.path), path):
            text = text.replace(secret_path, doc.ident)
        text = text.replace(doc.path.name, doc.ident)
        text = text.replace("[[PG:", "[[PG_LITERAL:")
        spans = self.detector.detect(text, self.config.language, self.config.entities, self.config.threshold)
        spans += [(m.start(), m.end(), "URL") for m in URL.finditer(text)]
        spans += [(m.start(), m.end(), "PATH") for m in ABS_PATH.finditer(text)]
        spans = sorted(spans, key=lambda x: (x[0], -x[1]))
        merged = []
        for start, end, entity in spans:
            if not (0 <= start < end <= len(text)) or entity not in (*self.config.entities, "URL", "PATH"):
                raise _deny()
            if merged and start < merged[-1][1]:
                old_start, old_end, _ = merged[-1]
                merged[-1] = (old_start, max(old_end, end), "PII")
            else:
                merged.append((start, end, entity))
        result, cursor = [], 0
        for start, end, entity in merged:
            result.extend((text[cursor:start], self.vault.issue(doc.ident, entity, text[start:end])))
            cursor = end
        result.append(text[cursor:])
        return "".join(result)

    def _safe_value(self, doc: Document, value: Any, path: str, depth: int = 0) -> Any:
        if depth > 12:
            raise _deny()
        if isinstance(value, str):
            return self._safe_text(doc, value, path)
        if type(value) is int and 9 <= len(str(abs(value))) <= 19:
            return self.vault.issue(doc.ident, "NUMBER", str(value))
        if type(value) is float:
            if not math.isfinite(value):
                raise _deny()
            if value.is_integer() and 9 <= len(str(abs(int(value)))) <= 19:
                return self.vault.issue(doc.ident, "NUMBER", str(int(value)))
        if value is None or type(value) in (bool, int, float):
            return value
        if isinstance(value, list) and len(value) <= 10000:
            return [self._safe_value(doc, v, path, depth + 1) for v in value]
        if isinstance(value, dict) and len(value) <= 1000 and all(isinstance(k, str) for k in value):
            if value.get("type") in ("image", "audio", "resource", "resource_link") or any(k in value for k in ("_meta", "blob", "mimeType")):
                raise _deny()
            return {self._safe_text(doc, k, path): self._safe_value(doc, v, path, depth + 1) for k, v in value.items()}
        raise _deny()

    def _excel_describe(self, doc: Document, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("sheets"), list):
            raise _deny()
        aliases = {real: alias for alias, real in doc.sheets.items()}
        result = dict(value)
        result["sheets"] = []
        for sheet in value["sheets"]:
            if not isinstance(sheet, dict) or not isinstance(sheet.get("name"), str):
                raise _deny()
            if sheet["name"] in aliases:
                result["sheets"].append({**sheet, "name": aliases[sheet["name"]]})
        return result

    def _excel_html(self, doc: Document, text: str) -> str:
        pattern = re.compile(r"(<li>sheet name: )([^<\n]*)(</li>)")
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            raise _deny()
        aliases = {real: alias for alias, real in doc.sheets.items()}
        original = html.unescape(matches[0].group(2))
        if original not in aliases:
            raise _deny()
        match = matches[0]
        return text[:match.start(2)] + aliases[original] + text[match.end(2):]

    def _html_decode_bounded(self, text: str) -> str:
        for _ in range(4):
            decoded = html.unescape(text)
            if decoded == text:
                return text
            if len(decoded) > self.config.max_output:
                raise _deny()
            text = decoded
        if html.unescape(text) != text:
            raise _deny()
        return text

    def _result(self, doc: Document, result: Any, path: str, tool: str) -> dict:
        profile = self.config.backends[doc.backend].profile if doc.backend in self.config.backends else doc.backend
        if result.isError or getattr(result, "meta", None) or getattr(result, "_meta", None):
            raise _deny()
        content = []
        total_chars = 0
        for item in result.content:
            if item.type != "text" or getattr(item, "_meta", None) or getattr(item, "meta", None):
                raise _deny()
            if profile == "pptx":
                try:
                    envelope = json.loads(item.text)
                except (ValueError, TypeError) as exc:
                    raise _deny() from exc
                if not isinstance(envelope, dict) or envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
                    raise _deny()
            try:
                decoded = json.loads(item.text)
            except (ValueError, TypeError):
                decoded = None
            if profile == "excel" and tool == "excel_describe_sheets":
                safe_text = json.dumps(self._safe_value(doc, self._excel_describe(doc, decoded), path), ensure_ascii=False)
            elif profile == "excel" and tool in ("excel_read_sheet", "excel_write_to_sheet"):
                safe_text = self._safe_text(doc, self._html_decode_bounded(self._excel_html(doc, item.text)), path)
            elif isinstance(decoded, (dict, list)):
                safe_text = json.dumps(self._safe_value(doc, decoded, path), ensure_ascii=False)
            elif isinstance(decoded, str):
                safe_text = json.dumps(self._safe_text(doc, decoded, path), ensure_ascii=False)
            else:
                safe_text = self._safe_text(doc, item.text, path)
            total_chars += len(safe_text)
            if total_chars > self.config.max_output:
                raise _deny()
            content.append({"type": "text", "text": safe_text})
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            if profile == "excel" and tool == "excel_describe_sheets":
                structured = self._excel_describe(doc, structured)
            structured = self._safe_value(doc, structured, path)
            if len(json.dumps(structured, ensure_ascii=False)) + total_chars > self.config.max_output:
                raise _deny()
        if not content and structured is None:
            raise _deny()
        return {"content": content, "structuredContent": structured}

    @asynccontextmanager
    async def _file_lock(self, doc: Document):
        import fcntl
        lock_path = self.config.workspace / (hashlib.sha256(str(doc.path).encode()).hexdigest() + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        acquired = False
        try:
            deadline = asyncio.get_running_loop().time() + self.config.timeout
            while not acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise _deny()
                    await asyncio.sleep(0.05)
            yield
        finally:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    async def call(self, public_name: str, arguments: dict) -> dict:
        try:
            backend, tool = public_name.split("__", 1)
            if backend not in self.config.backends or tool not in self.config.backends[backend].tools:
                raise _deny()
            doc = self.config.documents.get(arguments.get("document_id")) if isinstance(arguments, dict) else None
            if doc is None or doc.backend != backend:
                raise _deny()
            kind, upstream = self._validate(doc, tool, arguments)
            async with self.locks[doc.ident]:
                async with self._file_lock(doc):
                    original = self.config.source(doc)
                    digest = hashlib.sha256(original).hexdigest()
                    if kind == "write" and arguments["expected_sha256"] != digest:
                        raise _deny()
                    # All backend calls use a bounded private copy. Reads cannot mutate a source.
                    with tempfile.TemporaryDirectory(prefix="proxy-", dir=self.config.workspace) as tmp:
                        path = str(Path(tmp) / ("document" + doc.path.suffix.lower()))
                        Path(path).write_bytes(original)
                        backend_config = self.config.backends[backend]
                        remote_path = self._backend_path(backend_config, path)
                        upstream[PATH_KEYS[backend_config.profile]] = remote_path
                        async with self.backend_locks[backend]:
                            if backend in self.failed_backends:
                                raise _deny()
                            try:
                                result = await asyncio.wait_for(self.sessions[backend].call_tool(tool, upstream), timeout=self.config.timeout)
                            except Exception:
                                self.failed_backends.add(backend)
                                raise
                        safe = self._result(doc, result, remote_path, tool)
                        if kind == "write":
                            changed_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                            try:
                                info = os.fstat(changed_fd)
                                if not stat.S_ISREG(info.st_mode) or info.st_size > self.config.max_source:
                                    raise _deny()
                                with os.fdopen(os.dup(changed_fd), "rb") as stream:
                                    changed = stream.read(self.config.max_source + 1)
                                if len(changed) > self.config.max_source:
                                    raise _deny()
                            finally:
                                os.close(changed_fd)
                            # A successful edit must actually produce a changed working copy.
                            if changed == original:
                                raise _deny()
                            if hashlib.sha256(self.config.source(doc)).hexdigest() != digest:
                                raise _deny()
                            # The parent and target are rechecked immediately before replacement.
                            self._commit(doc, changed, digest)
                            safe["sha256"] = hashlib.sha256(changed).hexdigest()
                        else:
                            safe["sha256"] = digest
                        safe["content"].append({"type": "text", "text": json.dumps({"document_id": doc.ident, "sha256": safe["sha256"]})})
                        return safe
        except Exception as exc:
            raise _deny() from exc

    def _commit(self, doc: Document, changed: bytes, expected: str) -> None:
        parent = doc.path.parent
        # Open ancestors individually so a replaced parent symlink cannot redirect commit.
        fds = [os.open("/", os.O_RDONLY | os.O_DIRECTORY)]
        for part in parent.parts[1:]:
            try:
                fds.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
            except Exception:
                for opened in reversed(fds):
                    os.close(opened)
                raise
        fd = fds[-1]
        staged = None
        try:
            target = os.open(doc.path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                info = os.fstat(target)
                if not stat.S_ISREG(info.st_mode):
                    raise _deny()
                with os.fdopen(os.dup(target), "rb") as stream:
                    if hashlib.sha256(stream.read(self.config.max_source + 1)).hexdigest() != expected:
                        raise _deny()
            finally:
                os.close(target)
            staged = ".pg-" + secrets.token_hex(16) + doc.path.suffix
            output = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, info.st_mode & 0o600, dir_fd=fd)
            try:
                with os.fdopen(output, "wb") as stream:
                    stream.write(changed)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(staged, doc.path.name, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                staged = None
            except Exception:
                raise
        finally:
            if staged:
                try:
                    os.unlink(staged, dir_fd=fd)
                except OSError:
                    pass
            for opened in reversed(fds):
                os.close(opened)


def _public_schema(backend: str, tool: str) -> dict:
    kind, fields = PROFILES[backend][tool]
    properties = {"document_id": {"type": "string", "enum": []}}
    for name, field in fields.items():
        if field in ("index", "positive"):
            properties[name] = {"type": "integer", "minimum": 1 if field == "positive" else 0}
        elif field == "bool":
            properties[name] = {"type": "boolean"}
        elif field == "range":
            properties[name] = {"type": "string", "pattern": CELL_RANGE.pattern}
        elif field == "sheet":
            properties[name] = {"type": "string", "description": "Configured public sheet alias"}
        elif field == "text":
            properties[name] = {"type": "string", "description": "Text to write; issued document tokens are restored locally"}
        elif field == "paragraph_edits":
            properties[name] = {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "object", "properties": {
                "paragraph_index": {"type": "integer", "minimum": 0}, "anchor": {"type": "string", "pattern": ANCHOR.pattern},
                "new_text": {"type": "string"}}, "required": ["new_text"], "oneOf": [{"required": ["paragraph_index"]}, {"required": ["anchor"]}], "additionalProperties": False}}
        elif field == "text_edits":
            properties[name] = {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "object", "properties": {
                "oldText": {"type": "string"}, "newText": {"type": "string"}}, "required": ["oldText", "newText"], "additionalProperties": False}}
        elif field == "values":
            properties[name] = {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": ["string", "number", "boolean", "null"]}}}
    required = ["document_id", *sorted(REQUIRED.get(backend, {}).get(tool, set()))]
    if kind == "write":
        properties["expected_sha256"] = {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "SHA-256 returned by the most recent read of this document; prevents overwriting a changed source"}
        required.append("expected_sha256")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _upstream_compatible(backend: str, tool: str, schema: dict) -> bool:
    if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return False
    ours = set(PROFILES[backend][tool][1])
    upstream = (ours - {"sheet"}) | ({"sheetName"} if "sheet" in ours else set()) | {PATH_KEYS[backend]}
    if backend == "excel" and tool == "excel_write_to_sheet":
        upstream |= {"newSheet"}
    if backend == "excel" and tool == "excel_read_sheet":
        upstream |= {"showFormula", "showStyle"}
    if backend == "docx" and tool == "edit_paragraphs":
        upstream |= {"track_changes", "allow_untracked_edit"}
    required = set(schema.get("required", []))
    return upstream <= set(schema["properties"]) and required <= upstream


def proxy_server(proxy: PrivacyProxy):
    """Create public MCP tools for the HTTP server."""
    from mcp import types
    from mcp.server.lowlevel import Server

    server = Server("privacy-gateway-proxy", version="0.2.0")

    @server.list_tools()
    async def list_tools():
        tools = []
        for name, backend in proxy.config.backends.items():
            for tool in backend.tools:
                schema = _public_schema(backend.profile, tool)
                schema["properties"]["document_id"]["enum"] = [d.ident for d in proxy.config.documents.values() if d.backend == name]
                if "sheet" in schema["properties"]:
                    aliases = sorted({alias for d in proxy.config.documents.values() if d.backend == name for alias in d.sheets})
                    schema["properties"]["sheet"]["enum"] = aliases
                kind = PROFILES[backend.profile][tool][0]
                tools.append(types.Tool(name=f"{name}__{tool}", description=f"{kind.title()} registered {backend.profile} document content through the privacy gateway.", inputSchema=schema))
        return tools

    # Upstream sessions and source copy/write operations are shared across HTTP
    # requests. Serialize calls to protect shared backend operations.
    call_lock = asyncio.Lock()

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict | None):
        async with call_lock:
            try:
                result = await proxy.call(name, arguments or {})
                return types.CallToolResult(content=[types.TextContent(type="text", text=x["text"]) for x in result["content"]],
                                            structuredContent={"sha256": result["sha256"], "data": result["structuredContent"]})
            except Exception:
                return types.CallToolResult(content=[types.TextContent(type="text", text=DENIED)], isError=True)

    return server


async def connect_backend(stack, backend: Backend, timeout: int):
    """Open one approved upstream transport and return its initialized session."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    if backend.url is not None:
        import httpx
        from mcp.client.streamable_http import streamable_http_client
        headers = {}
        if backend.bearer_token_env:
            token = os.environ.get(backend.bearer_token_env)
            if not token or "\r" in token or "\n" in token:
                raise ValueError("Missing MCP bearer token")
            headers["Authorization"] = "Bearer " + token
        client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=timeout,
                                                                follow_redirects=False, trust_env=False))
        streams = await stack.enter_async_context(streamable_http_client(backend.url, http_client=client))
    else:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/"),
               "PPTX_MCP_AUTO_RENDER": "0", "NO_COLOR": "1", **(backend.env or {})}
        params = StdioServerParameters(command=backend.command, args=list(backend.args), env=env)
        errlog = stack.enter_context(open(os.devnull, "w"))
        streams = await stack.enter_async_context(stdio_client(params, errlog=errlog))
    session = await stack.enter_async_context(ClientSession(*streams[:2]))
    await asyncio.wait_for(session.initialize(), timeout=timeout)
    listed = await asyncio.wait_for(session.list_tools(), timeout=timeout)
    found = {t.name: t for t in listed.tools}
    for tool in backend.tools:
        if tool not in found or not _upstream_compatible(backend.profile, tool, found[tool].inputSchema):
            raise RuntimeError("Configured upstream schema incompatible")
    return session, found


@asynccontextmanager
async def proxy_backends(proxy: PrivacyProxy):
    """Connect approved external backends for the lifetime of the proxy."""
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        for name, backend in proxy.config.backends.items():
            session, _ = await connect_backend(stack, backend, proxy.config.timeout)
            proxy.sessions[name] = session

        try:
            yield
        finally:
            proxy.sessions.clear()
