# Document privacy

Read and edit user documents only through the `private_proxy` MCP tools. Never
fall back to shell, Python, filesystem tools, browser tools, or another MCP when
the proxy is unavailable or denies access. Report the unavailable tool or inaccessible
document instead. This also applies to documents inside this repo.

Use `files__read_text_file` with the absolute document `path`; no registration is
required. Edits use `files__edit_file` with the same `path` and the
`expected_sha256` returned by the most recent read. Preserve privacy tokens.

Source code, configuration and tests may be inspected for development. Do not
print document contents, vault contents or keys while debugging. Use synthetic
fixtures to verify masking. These instructions guide tool use; they do not
provide OS-level isolation.
