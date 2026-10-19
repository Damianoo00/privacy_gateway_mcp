---
name: private-proxy-setup
description: Deploy the private MCP privacy proxy, install and connect external document MCP servers, and migrate Codex to a backed-up proxy-only configuration. Use for setting up or repairing this deployment and its Codex access policy.
---

# Private proxy setup

Work from a checkout of the privacy-gateway-mcp repository; locate it from the user's
context or ask for its path. The proxy image contains only the proxy and PII model.
External MCP servers are installed separately and added to `mcp_servers` configuration.

For explanation-only requests, explain without changing configuration. A request to
install/configure this integration authorizes the setup steps, including the final
Codex migration. Respect filesystem/administrator approval requirements; do not ask
again for actions already authorized. Creating this skill alone does not authorize
applying its migration to the current Codex session.

1. Read [deployment.md](references/deployment.md). Choose native or Docker proxy,
   identify source folders and a separate private shared workspace. Start the proxy
   first with an empty server map; health alone is not a working document test.
2. Read [servers.md](references/servers.md). Install only the requested backends at
   pinned revisions. Add command/args for native stdio or URLs for external HTTP;
   stdio-only servers can run behind the private `bridge` command. Never install
   backend binaries in the proxy Dockerfile or download them at proxy startup.
3. Discover upstream tools and expose only the configured profile's approved tools.
   Preserve document IDs, sheet aliases, tokens, SHA-256 conflicts and private-copy
   writes. Unknown profiles/tools require an adapter; do not enable generic passthrough.
   Test on synthetic documents: read → token instead of PII → edit → reread, plus
   rejection of an unknown document and stale SHA. Never print real source contents
   to model context or logs to test masking.
4. Read [codex.md](references/codex.md). Prepare and validate a candidate central
   `config.toml` using `scripts/configure_codex.py`. Review changed keys without
   displaying provider secrets. Keep existing model/provider settings; this workflow
   changes file/MCP access, not the model's API destination or user prompts.
5. Apply the reviewed migration as the final step after proxy tests pass. The helper
   backs up the exact old bytes before replacement. Report the backup path and how
   to restore it from a user-operated terminal. Require a full Codex restart; no
   changes to the current session's tool inventory are implied.
6. Verify the restarted effective configuration and a non-sensitive denied-read
   sentinel. Direct backend MCPs, shell/file tools, apps and plugins must not provide
   an alternate path. Do not re-enable tools to bypass a failed protection test.

Do not equate read-only with no-read. Central config is a user-editable policy layer,
not OS isolation: project/managed layers, CLI overrides, editor attachments and host
access must be assessed. Claim “documents accessible only through proxy” only after
negative tests and host isolation support it. For a stronger guarantee use a separate
Codex account/container without source/workspace/key mounts or backend credentials;
expose only the sanitized proxy endpoint. If enforcement cannot be verified, report
that limitation and do not label deployment fully isolated.
