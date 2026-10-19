# Deploy the proxy

The repository Dockerfile installs only private-proxy code, Presidio and its English
spaCy model. It does not install DOCX/Excel/PPTX/filesystem MCP servers.

From the checkout:

```console
docker compose up -d --build
docker compose ps
docker compose exec gateway python -m privacy_gateway.smoke
```

Default `config.container.example.toml` has `mcp_servers = {}` and no documents.
A healthy endpoint with zero tools is intentional until the operator connects a
backend. The key is created once in `private_data`; a bad existing key blocks start.
Do not delete volumes to repair a key or token issue.

## Configure external servers

Copy `.env.example` to `.env` and `config.container.example.toml` to
`config.container.toml`. Set `PG_CONFIG=./config.container.toml`. Choose existing
host directories for `PG_SOURCES` and `PG_WORKSPACE`; create them as the operator,
with permissions allowing UID 1000 in the proxy to read/write. Do not use chmod 777.
Do not expose the private key directory as the shared workspace.

For a host backend, bind its workspace into the proxy as `/workspace`, then configure
its host-visible path as the backend's `workspace_path`. Example `.env` on Ubuntu:

```dotenv
PG_SOURCES=/home/operator/approved-documents
PG_WORKSPACE=/home/operator/private-proxy-workspace
PG_CONFIG=./config.container.toml
PG_BACKEND_ENV_FILE=./backend-secrets.env
```

On Windows use host paths with forward slashes in `.env`, for example
`C:/Users/Jan/private-proxy-workspace`; that path goes into `workspace_path` as well.
On macOS share the chosen host folder in Docker Desktop if required. The proxy's
own file operations are Linux operations inside the container.

External services must see the SAME working files. With backend containers mount
the shared workspace volume at `/workspace` too. A URL alone does not share files.
With host services, `workspace_path` translates `/workspace/<temporary-copy>` into
the native host root; the proxy still commits its local copy after validation.

The proxy reaches host bridges as `host.docker.internal` (Compose adds host-gateway
on Linux). A host bridge bound only to `127.0.0.1` is generally not reachable from
a container. Use a deliberately selected reachable interface, a bearer token and
host firewall rules limiting access to the proxy. The bridge handles original data;
never register its URL directly in Codex. For remote hosts use authenticated TLS
and private networking. Do not make an unauthenticated raw backend public.

Proxy HTTP itself is published only to host loopback at port 8000. Default bridge
networking allows outbound connections; it is not an egress firewall. For protected
data require trusted backends and appropriate host/network isolation.

After config content changes use `docker compose restart gateway`. If changing
mounts, `.env` or environment variables use `docker compose up -d --force-recreate`.
An unavailable or incompatible configured server blocks startup rather than exposing
unvalidated tools. Add servers during a maintenance window; hot reload is not assumed.

The proxy can also run natively on a compatible Linux environment:

```console
python3 -m venv .venv
.venv/bin/pip install '.[pii]'
.venv/bin/python -m spacy download en_core_web_lg
.venv/bin/privacy-gateway init-key /private/location/gateway.key
.venv/bin/privacy-gateway serve --config config.proxy.toml --key-file /private/location/gateway.key
```

For native serving, paths and command executables must exist on that machine.
Native Windows portability of the proxy file-security code is not promised; use its
Linux container there. The bridge itself launches native backends without the PII
model, after installing the base Python package and backend runtime.
