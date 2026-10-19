# Install and register external document MCP servers

The user chooses which servers to install. Verify their official installation docs,
license and platform/runtime requirements before executing setup. Pin sources and
package versions; do not use `npx --yes` or download dependencies at proxy startup.
The revisions below were inspected for this gateway; changes still need integration
validation on synthetic documents. They are recommendations for a compatible adapter,
not a claim that every Office feature is preserved.

| Profile | Official source | Inspected revision |
| --- | --- | --- |
| docx | https://github.com/knorq-ai/docx-mcp-server | `9b36ea3e5aaa71af0acb13328d05e439ebeb7e1c` |
| excel | https://github.com/negokaz/excel-mcp-server | `1ff4340573c3e421920282da1602afd89f3bb282` |
| pptx | https://github.com/knorq-ai/pptx-mcp-server | `d5c41228fcc81e7a0ffdd7b870500a1d25136d0f` |
| filesystem | https://github.com/modelcontextprotocol/servers | `51b6382a3e827aceb6bce430b1208d69b93d95ea` |

Clone into an operator-owned backend-installation directory outside the proxy image.
Check out the exact revision; inspect its manifests before executing build scripts.
Use local built executables in configuration, not runtime installers:

- DOCX: Node.js project; `npm ci --ignore-scripts`, `npm run build`, then invoke
  the chosen Node executable with the absolute `dist/index.js` path.
- Filesystem: Node.js >=24 at this revision. In the repository root install the
  `@modelcontextprotocol/server-filesystem` workspace from the lockfile, build that
  workspace, then invoke its `src/filesystem/dist/index.js`. Keep root and any
  workspace-local `node_modules` in their build layout. Its allowed-directory
  argument must be the backend-visible private workspace, not the sources folder.
- Excel: this repository has an npm launcher and a Go implementation. For direct
  native execution use Go 1.24-compatible tooling and `go build -o <absolute-output>
  ./cmd/excel-mcp-server`. The pinned revision needs the gateway repository's
  `patches/excel-mixed-values.patch`: verify with `git apply --check`, apply it, then
  build. It fixes mixed numeric/bool/null cell validation; do not silently coerce
  numeric values to strings. Preserve the license alongside the binary.
- PPTX: create a separate Python venv and install the checked-out project with
  its `[mcp]` extra. Use its installed `pptx-mcp-server` executable. Disable automatic
  rendering with `PPTX_MCP_AUTO_RENDER=0`; rendering is outside the proxy text policy.

On a host needing the stdio bridge, install the proxy base package into a dedicated
Python 3.11+ venv with `pip install /absolute/path/to/gateway-checkout`. The `pii` extra
and spaCy model are needed only in the serving proxy, not in the bridge. This keeps
third-party runtimes and binaries outside the proxy image.

## Native stdio registration

`backends.toml` on the backend machine can contain only the server map:

```toml
[mcp_servers.files]
profile = "filesystem"
command = "/absolute/path/to/node"
args = ["/absolute/path/to/filesystem/dist/index.js", "/home/operator/private-proxy-workspace"]
enabled_tools = ["read_text_file", "edit_file"]
# env = { MY_REQUIRED_SETTING = "value" }
```

Run one private HTTP bridge per stdio server (separate terminal/service):

```console
privacy-gateway bridge --config backends.toml --server files --host 0.0.0.0 --port 8101 --bearer-token-env FILES_BACKEND_TOKEN --allowed-host "host.docker.internal:*"
```

Generate a random bearer token into a mode-0600 environment file without echoing it.
Configure the backend service and proxy process to receive the same environment
variable. Do not put token values into TOML, command-line literals, logs or Codex
config. The bridge requires a token on every bind, including loopback; restrict its reachable
interface/firewall as described in deployment.md. Bridge health is not sanitization.

## Proxy-side HTTP registration

The proxy container configuration, independent of `backends.toml`:

```toml
source_roots = ["/sources"]
workspace = "/workspace"
language = "en"
spacy_model = "en_core_web_lg"
entities = ["EMAIL_ADDRESS", "PERSON"]
score_threshold = 0.35
access_scope = "local-user"
max_source_bytes = 10485760
max_output_chars = 1000000
timeout_seconds = 30

[mcp_servers.files]
profile = "filesystem"
url = "http://host.docker.internal:8101/mcp"
bearer_token_env = "FILES_BACKEND_TOKEN"
workspace_path = "/home/operator/private-proxy-workspace"
enabled_tools = ["read_text_file", "edit_file"]

[[documents]]
id = "doc_text_12345678"
path = "/sources/note.txt"
backend = "files"
```

For native proxy+backend stdio on the same machine use `command/args` directly in
that same server entry, omitting `url`, `workspace_path` and HTTP credential settings.
The proxy Docker container cannot execute a host's `C:/.../node.exe` or `/usr/bin/node`:
those commands belong in a host bridge or a separate backend container.

Names such as `files`, `word_local` and `finance` are operator-defined server IDs.
`profile` selects the gateway's semantic rules. Tools become `files__read_text_file`,
not unfiltered aliases. `enabled_tools` is an allowlist checked against discovered
upstream schemas. Registration alone does not enable unsupported tools.

Profiles/tools: DOCX read_document/edit_paragraphs; Excel excel_describe_sheets,
excel_read_sheet/excel_write_to_sheet; PPTX pptx_get_info/pptx_read_slide/pptx_edit_text;
filesystem read_text_file/edit_file. DOCX edits use a fixed untracked policy for
repeatable writes. XLSX documents need a `sheets` mapping of public aliases to real
sheet names. Public commands take document_id; local paths are injected by the proxy.

Before Codex migration validate synthetic PII masking, token restoration on edit,
SHA conflict rejection, invalid document rejection, and restart persistence. Test
all chosen profiles. Keep originals and the shared workspace inaccessible to the
Codex identity when an enforced proxy-only deployment is required.
