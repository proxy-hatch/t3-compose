# `t3c config` commands

This specification describes the `t3compose.config` command module implemented by CF-1. Shared CLI behavior (invocation, global `--text` and `--local-dir` options, output encoding and exit codes) is defined in [`docs/cli.md`](../cli.md). The configuration and quota-state schema and Python API are defined in [`docs/configuration.md`](../configuration.md).

## `t3c config check`

Validate the version-2 personal routing configuration and per-account quota state file, and print the configuration digests, rule version, risk-fact floors, round tiers and notices.

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c [--text] [--local-dir PATH] config check [--config PATH] [--state PATH]
```

### Options

| Option | Default | Description |
|---|---|---|
| `--config PATH` | `<args.local_dir>/agent-routing.json` | Path to the version-2 routing configuration JSON file. |
| `--state PATH` | `<args.local_dir>/agent-routing-state.json` | Path to the quota state JSON file. Always validated, including when the default file is selected; a missing file is invalid config. |

Handlers derive default paths exclusively from `args.local_dir`. Both files are read with `t3compose.jsonio.read_json_with_digest` (capped at 16 KiB per file). `jev.keyFile.path` is validated syntactically as an absolute path string and is never opened or read.

### Output and exit codes

| Exit code | Enum | Condition | Stdout | Stderr |
|---|---|---|---|---|
| `0` | `ExitCode.SUCCESS` | Both the selected config and quota state are valid. | One JSON object with `digest`, `policyDigest`, `stateDigest`, `ruleVersion`, `riskFacts`, `roundTiers` and `notices` (or `key: <json>` lines with `--text`). | Empty. |
| `3` | `ExitCode.INVALID_CONFIG` | Config or state file cannot be read, exceeds 16 KiB, contains invalid JSON, is version 1, or fails any schema rule. | One JSON object (`Outcome(data, ExitCode.INVALID_CONFIG)`) with `valid: false`, `errors` (`[{"path": ..., "rule": ...}]`) and optional `missingSections`. | Empty. |
| `2` | `ExitCode.INVALID_INPUT` | Invalid CLI flags or unexpected helper failure. | Empty. | Sanitized public diagnostic. |

Exit `0` returns `{digest, policyDigest, stateDigest, ruleVersion, riskFacts, roundTiers, notices}`. Exit `3` returns `{valid: false, errors: [{"path": "<schema.path>", "rule": "<rule>"}]}` (plus `missingSections` when `version == 1`), never raw input values, secret paths from inside config or backend exception details. Quota-state errors use paths prefixed with `state.` (such as `state.activeProviderInstanceId` or `state.quotaObservations.<instance>[0].status`).
