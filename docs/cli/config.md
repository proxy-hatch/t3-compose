# `t3c config` commands

This specification describes the `t3compose.config` command module implemented by CF-1. Shared CLI behavior (invocation, global `--text` and `--local-dir` options, output encoding and exit codes) is defined in [`docs/cli.md`](../cli.md). The configuration and quota-state schema and Python API are defined in [`docs/configuration.md`](../configuration.md).

## `t3c config check`

Validate the version-2 or version-3 personal routing configuration and per-account quota state file, and print the configuration digests, rule version, risk-fact floors, round tiers and notices.

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c [--text] [--local-dir PATH] config check [--config PATH] [--state PATH]
```

### Options

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `<args.local_dir>/agent-routing.json` | Path to the version-2 or version-3 routing configuration JSON file. |
| `--state PATH` | `<args.local_dir>/agent-routing-state.json` | Path to the quota state JSON file. Always validated, including when the default file is selected; a missing file is invalid config. |

Handlers derive default paths exclusively from `args.local_dir`. Both files are read with `t3compose.jsonio.read_json_with_digest` (capped at 16 KiB per file). `jev.keyFile.path` is validated syntactically as an absolute path string and is never opened or read.

### Output and exit codes

| Exit code | Enum | Condition | Stdout | Stderr |
|---|---|---|---|---|
| `0` | `ExitCode.SUCCESS` | Both the selected config and quota state are valid. | Version 2: one JSON object with `digest`, `policyDigest`, `stateDigest`, `ruleVersion`, `riskFacts`, `roundTiers` and `notices`; version 3: the object below (or `key: <json>` lines with `--text`). | Empty. |
| `3` | `ExitCode.INVALID_CONFIG` | Config or state file cannot be read, exceeds 16 KiB, contains invalid JSON, is version 1, or fails any schema rule. | One JSON object (`Outcome(data, ExitCode.INVALID_CONFIG)`) with `valid: false`, `errors` (`[{"path": ..., "rule": ...}]`) and optional `missingSections`. | Empty. |
| `2` | `ExitCode.INVALID_INPUT` | Invalid CLI flags or unexpected helper failure. | Empty. | Sanitized public diagnostic. |

Exit `0` returns `{digest, policyDigest, stateDigest, ruleVersion, riskFacts, roundTiers, notices}`. Exit `3` returns `{valid: false, errors: [{"path": "<schema.path>", "rule": "<rule>"}]}` (plus `missingSections` when `version == 1`), never raw input values, secret paths from inside config or backend exception details. Quota-state errors use paths prefixed with `state.` (such as `state.activeProviderInstanceId` or `state.quotaObservations.<instance>[0].status`).

Version 3 success (exit `0`) returns:

```json
{"version": 3, "digest": "sha256:…", "policyDigest": "sha256:…", "stateDigest": "sha256:…", "ruleVersion": "…", "riskFacts": {"example-fact": {"scope": "COMPLEX", "criticality": "CORE"}, "document-change": {}}, "notices": []}
```

Each fact contains only its configured `scope` and `criticality` floors, or `{}`
when it raises nothing. There is no `roundTiers` field. Version-3 failures use
exit `3` and `{valid: false, errors: [...]}` without `missingSections`.
Version-1 and version-2 results stay unchanged. The state is checked against
the loaded configuration's pools for both supported versions.

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
Callers map this exception to exit `3`, as version-2 callers do.

During the compatibility window, `load_config` serves version 2 and refuses
integer version 3 with exactly `version (expected-version-2)` before validating
other fields. MX-2 and MX-3a migrate `jev.py`, `routing.py` and `quota.py`;
MX-3b removes version 2. `config check` uses a version-dispatching internal load.
