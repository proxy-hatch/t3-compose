# Routing commands

Use the [shared CLI contract](../cli.md) for invocation, global options, output,
errors and registration. `--repo REPO` accepts the records API's repository
identity, origin URL or checkout path; `--work ID` selects an existing work.
Each command reloads the config under the selected local directory. There is
no writer option: appends use `load_work(...).effectiveWork.writer` and the
[records store](../records.md) validates that writer and the complete history.

```text
t3c route input --repo REPO --work ID --input TASK.json
```

`input` reads a task object with exactly `intent`, `acceptance`, `interfaces`,
`scope`, `riskFacts`, `dependencies`. It records canonical `{task, context:null}`
through the common 16 KiB input recorder and returns
`{inputRef, inputDigest, policyDigest}`. The supplied JSON file is bounded to
1 MiB before canonical validation.

Exits: 0 recorded; 2 invalid input or records; 3 invalid config. Exits 2/3
have a sanitized message only and append nothing.

The version-2 `route judge` and `route select` commands are retired. Their
version-3 replacements, a manual judgment and a selection from the routing
matrix, arrive with MX-3a1.

## Saved catalog API

`routing.load_catalog(path, read_at, now)` reads a saved capabilities object
with `providers[]` from a file of at most 1 MiB; no service is called. Capture
time must be UTC ending in `Z`, not future, and at most 15 minutes old
(inclusive). `routing.parse_catalog(raw)` returns providers by ID, including
unavailable ones. Needed provider fields are `providerInstanceId`,
`driverKind`, strict boolean `canRunChildTask` and
`canRunCrossProviderChildTask`, string-list `constraints`, and `models[]`.
Each model has `id` and optional `options[]` with `id` and `values` (strings or
booleans). Duplicate IDs, missing or malformed needed fields fail closed with
`CatalogError`. Extra metadata is ignored. Disabled/unsupported entries can
have valid shape. `live/catalog_shape.py` checks a saved real catalog with the
same parser.

## Quota evaluation API

Reusable `routing.evaluate_quota(observations, now)` accepts validated account
observations and an aware UTC datetime; it returns `unknown`, `exhausted` or
`available` without mutation. Latest observation time wins, with the last list
entry breaking ties. Reset strictly before now clears exhaustion to unknown;
later available evidence clears it immediately. Invalid/future observation times
or reset before observation raise `ValueError`. Quota applies
across all models on an account. The [quota commands](quota.md) record evidence
and activate accounts explicitly.
