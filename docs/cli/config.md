# `t3c config` commands

This specification describes the `t3compose.config` command module implemented by CF-1. Shared CLI behavior (invocation, global `--text` and `--local-dir` options, output encoding and exit codes) is defined in [`docs/cli.md`](../cli.md). The configuration and quota-state schema and Python API are defined in [`docs/configuration.md`](../configuration.md).

## `t3c config check`

Validate the version-3 personal routing configuration and quota state file, and print the configuration digests, rule version, risk-fact floors, notices and version number. Older configs require migration to version 3.

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c [--text] [--local-dir PATH] config check [--config PATH] [--state PATH]
```

### Options

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `<args.local_dir>/agent-routing.json` | Path to the version-3 routing configuration JSON file. |
| `--state PATH` | `<args.local_dir>/agent-routing-state.json` | Path to the quota state JSON file. Always validated, including when the default file is selected; a missing file is invalid config. |

Handlers derive default paths exclusively from `args.local_dir`. Both files are read with `t3compose.jsonio.read_json_with_digest` (capped at 16 KiB per file). `jev.keyFile.path` is validated syntactically as an absolute path string and is never opened or read.

### Output and exit codes

| Exit code | Enum | Condition | Stdout | Stderr |
|---|---|---|---|---|
| `0` | `ExitCode.SUCCESS` | Both the selected config and quota state are valid. | The object below (or `key: <json>` lines with `--text`). | Empty. |
| `3` | `ExitCode.INVALID_CONFIG` | Config or state file cannot be read, exceeds 16 KiB, contains invalid JSON, has any version other than integer 3, or fails any schema rule. | One JSON object (`Outcome(data, ExitCode.INVALID_CONFIG)`) with `valid: false`, `errors` (`[{"path": ..., "rule": ...}]`) and a `migration` message for invalid versions. | Empty. |
| `2` | `ExitCode.INVALID_INPUT` | Invalid CLI flags or unexpected helper failure. | Empty. | Sanitized public diagnostic. |

Exit `3` returns `{valid: false, errors: [{"path": "<schema.path>", "rule": "<rule>"}]}`, never raw input values, secret paths from inside config or backend exception details. Invalid versions add `migration: "Migrate routing configuration to version 3; see docs/configuration.md."`. Quota-state errors use paths prefixed with `state.` (such as `state.activeProviderInstanceId` or `state.quotaObservations.<instance>[0].status`).

Version 3 success (exit `0`) returns:

```json
{"version": 3, "digest": "sha256:…", "policyDigest": "sha256:…", "stateDigest": "sha256:…", "ruleVersion": "…", "riskFacts": {"example-fact": {"scope": "COMPLEX", "criticality": "CORE"}, "document-change": {}}, "notices": []}
```

Each fact contains only its configured `scope` and `criticality` floors, or `{}`
when it raises nothing. There is no `roundTiers` field. Version-3 failures use
exit `3` and `{valid: false, errors: [...]}` without `missingSections`.
Version 1 and version 2 are rejected. The state is checked against the loaded
version-3 configuration's pools.

## Version-3 accessor for consumers

`load_matrix_config(path=None, *, local=None) -> LoadedConfig` resolves
`<local or local_dir()>/agent-routing.json` unless an explicit path is given.
It reads once with the 16 KiB cap, never caches and never opens `jev.keyFile.path`.
It returns deeply read-only `data`, the exact file-byte `digest`, canonical
`policy_digest` and empty `notices` tuple.

`ConfigValidationError` contains sanitized paths and rules. Read errors are
`config (unreadable-file)`, `config (size-limit-exceeded)` or
`config (invalid-json)`. A parsed non-object uses `$ (expected-object)`.
Any version except integer `3` (including `1`, `2`, `"3"`, `true` or missing)
uses exactly `version (expected-version-3)`. Version-3 schema errors are collected.
The exception also includes the migration guidance above. Callers map it to exit `3`.

`load_config(path=None, *, local=None)` is a compatibility alias of
`load_matrix_config`, with the same bounded, uncached version-3-only behavior.
To migrate, use the version-3 example and the [migration guide](../configuration.md#migration-to-version-3).
Remove `helpers.search` and every `roleBlocks` entry whose role is `search`.
Historical records and legacy quota observations remain readable.
