# Back up and migrate central Codex configuration

This workflow changes the user's central `config.toml`, not an administrator's
requirements file. Creating the skill is not permission to apply it now. When the
user invokes setup/configuration, use the existing authorization and finish the
working proxy tests before the final replacement.

## Scope and version checks

Find the actual central path: `$CODEX_HOME/config.toml`, otherwise
`~/.codex/config.toml` (`%USERPROFILE%/.codex/config.toml` on Windows). Do not assume
that the current project `.codex/config.toml` is the central file. Never print raw
central config; it can contain provider credentials. Do not send its contents to
another service. Preserve its model/provider choice; only file/MCP access changes.
User prompts and preexisting inline instructions are not anonymized by the proxy.

Check `codex --version`. The candidate shape was checked with Codex CLI 0.163.0.
Validate against the installed executable; if settings are unsupported, stop before
applying. Do not silently drop deny rules to make an old version accept the file.

Official sources (recheck for another version/platform):

- https://learn.chatgpt.com/docs/permissions
- https://learn.chatgpt.com/docs/config-file/config-reference
- https://learn.chatgpt.com/docs/extend/mcp?surface=cli
- https://learn.chatgpt.com/docs/enterprise/managed-configuration

The helper selects `default_permissions = "private_proxy"` and a profile that denies
`:root`, workspace roots and temporary locations, grants only `:minimal` runtime
reads, and denies explicit protected paths. It disables command execution, browser,
image, app/plugin, hook and subagent features and leaves only the proxy MCP entry.
It also disables automatic project-document loading and removes file-based custom
instructions so that setup does not leave an obvious direct context-loading route.
Existing project trust entries become untrusted. Unrelated model/provider settings
are preserved semantically; comments and formatting are not preserved in the new
file, but the backup is byte-for-byte original.

Do not describe `:minimal` as zero filesystem access: Codex still needs runtime
files, config and authentication. The policy aims to deny direct document access.
Read-only sandbox mode still permits reads and is insufficient. Named permissions
are overridden by legacy `sandbox_mode` in any loaded layer, so the helper removes
that central setting and legacy profiles. Audit project/managed config and explicit
launch flags too. Never edit managed policy without separate authorization.

## Prepare and review

Choose new private paths for a candidate and plan; do not overwrite earlier plans.
The script requires Python 3.11+ and has no third-party dependencies. Example:

```console
python3 scripts/configure_codex.py prepare --config /home/operator/.codex/config.toml --output /private/staging/candidate.toml --plan /private/staging/plan.json --proxy-url http://127.0.0.1:8000/mcp --protect /home/operator/approved-documents --protect /home/operator/private-proxy-workspace
```

Here `scripts/` is relative to this skill's directory. Include source folders,
workspace, key/config backup storage and backend secrets in protected paths when
appropriate. On Windows use the installed Python command and native absolute paths.
Mode 0600 protects POSIX files; verify Windows ACLs separately.

Preparation never changes the active file. Review parsed changed keys and tool
inventory without displaying provider secrets. Verify model/provider preservation,
only `mcp_servers.private_proxy`, no direct backends, deny rules, disabled features,
no trusted project configs. HTTP defaults to explicit numeric loopback. A remote
HTTPS URL is only for a user's explicit authenticated remote-proxy deployment,
not an implicit alternative to local setup.

## Validate before switching

Use an isolated temporary `CODEX_HOME` with a copy of the candidate (private mode)
and no project config. Do not point tests at the real user config. On CLI 0.163.0,
`codex mcp list` does NOT support `--strict-config`; do not use that as strict
validation. A harmless app-server initialization can validate the configuration:

```console
codex --no-daemon app-server --strict-config --listen stdio://
```

Send a JSON-line initialize request on stdin, then close stdin:

```json
{"id":1,"method":"initialize","params":{"clientInfo":{"name":"private-proxy-validator","version":"1.0"},"capabilities":{"experimentalApi":true}}}
```

Use a subprocess with a timeout and capture output. This starts no model session
and performs no document read. Delete temporary secret-bearing candidate copies.
Also check the candidate's MCP inventory using `codex mcp list --json` in that same
isolated environment, and inspect effective features/permission settings. A parser
success is NOT a successful sandbox test.

Create a non-sensitive sentinel in an explicitly protected test directory. In the
candidate's isolated environment, verify that an explicit sandboxed command cannot
read it (e.g. `codex sandbox -P private_proxy -- /bin/cat <sentinel>` on Linux).
Capture output and assert the known marker is absent; do not use a real secret as
a test. If sandbox creation is unavailable or the read succeeds, do not declare
protection verified. Investigate platform support and managed/project layers.
On Windows use its supported native sandbox and an equivalent harmless read test.

Verify a synthetic document IS readable through the proxy, with its PII tokenized.
Only after that positive test and the negative direct-read test pass should the
skill apply the central migration. Close other config editors before applying.

## Apply and restart

```console
python3 scripts/configure_codex.py apply --plan /private/staging/plan.json --config /home/operator/.codex/config.toml
```

The explicit config path must match the plan. The helper checks source/candidate
hashes, rejects symlinks/nonregular files, creates a unique 0600 exact-byte backup
alongside the old config, then replaces atomically. If backup fails, do not continue
with an unbacked overwrite. For a previously missing config it creates a new one;
there are no old bytes to back up. Do not repeat after a failure without checking
which file is active.

Record the backup path, exit ALL Codex sessions sharing that config/daemon and
restart. Existing sessions keep their tool inventory. Check the restarted inventory
has only the proxy MCP and no shell/image/browser/apps/plugin route. Repeat the
sentinel test using the effective environment. Do not re-trust a project or pass
`--sandbox`, a profile override, or `--dangerously-bypass-approvals-and-sandbox` to
work around this policy.

## Restore from a user terminal

Commands are intentionally unavailable to the restricted Codex session; restore
from a user-operated terminal rather than re-enabling a bypass inside the agent:

```console
python3 scripts/configure_codex.py restore --config /home/operator/.codex/config.toml --backup /home/operator/.codex/config.toml.backup-TIMESTAMP-RANDOM
```

This backs up the current bytes before restoring the selected backup. Restart Codex
again. Retain backups securely: they can contain old API/provider secrets.

## Enforcement boundary

User config is not an immutable security boundary. Managed layers, project trust,
launch overrides, editor/context attachments, auto-discovered skills and future tools
must be reviewed. `:minimal` can include platform-dependent paths. For a strict
production guarantee run Codex as another OS identity or in an environment with
NO source/workspace/key/backend-credential mounts, no Docker socket and no direct
backend network access; allow only the sanitized proxy endpoint. Central config
plus a skill alone cannot establish that host isolation. Report what was actually
verified and never promise every possible sensitive datum is detected.
