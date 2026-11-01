# Routing commands

Use the [shared CLI contract](../cli.md) for invocation, global options, output,
errors and registration. All three commands reload version-3 configuration with
`load_matrix_config(local=args.local_dir)`. Version 2 and invalid configuration
exit 3 with a sanitized message. No command contacts a provider.
`--repo REPO` accepts the records API's repository identity, origin URL or
checkout path; `--work ID` selects an existing work. Appends use
`load_work(...).effectiveWork.writer`; the [records store](../records.md)
validates the writer and complete history.

## Input and manual judgment

```text
t3c route input --repo REPO --work ID --input TASK.json
t3c route judge --repo REPO --work ID --input-ref SEQ --policy-digest DIGEST \
  --lane BACKEND_LOGIC|UI_FRONTEND --criticality CORE|NON_CRITICAL \
  --scope STANDARD|COMPLEX --reason TEXT
```

`input` reads a task object with exactly `intent`, `acceptance`, `interfaces`,
`scope`, `riskFacts`, `dependencies`. It records canonical
`{task, context: null}` through the common 16 KiB input recorder and returns
`{inputRef, inputDigest, policyDigest}`, using the current policy digest.
The supplied JSON file is bounded to 1 MiB before canonical validation.

For `judge` and `select`, the reference must be positive and the digest must
be `sha256:<64 hex>` (exit 2 otherwise). The latest `routing-input` must have
that sequence. Its policy digest and the argument must equal the current
configuration's policy digest; otherwise exit 4 asks for a current input.
`judge` appends and returns a version-3 `routing-judgment` event envelope:
`{configVersion: 3, inputRef, inputDigest, policyDigest, lane, criticality,
scope, reason, basis: "manual", defaulted: []}`.

## Matrix selection

```text
t3c route select --repo REPO --work ID --role worker|reviewer|feature-lead \
  --stage 1|2|3 [--round first|corrections|re-review] \
  --input-ref SEQ --policy-digest DIGEST --catalog FILE --catalog-read-at UTC
```

The default round is `first`. `corrections` is Worker-only; `re-review` is
Reviewer-only. An incompatible round exits 2.

Selection uses the latest matching version-3 judgment or classification
(`data.configVersion == 3`) with the current input's reference and digests.
Version-2 decisions and selections in the history are ignored. A judgment
supplies lane, criticality and scope. A classification must be `accepted`
and every answer's confidence must meet `jev.minConfidence`; otherwise exit 4
asks to record a judgment. `MIXED` maps to `BACKEND_LOGIC`.

Declared `task.riskFacts` apply the configured floors after the decision.
An unknown fact exits 4. A floor only raises scope to `COMPLEX` or criticality
to `CORE`. `floorsApplied` lists facts carrying floors in declared order,
without repeats; `document-change` has no floor. Starting stage is 2 for
`COMPLEX`, otherwise 1. A lower requested stage exits 4 with `startingStage`.
Higher stages are accepted. A dry cell never changes stages.

The cell is `matrix[role][lane][str(stage)]`. Worker lanes are `ui-frontend`
for `UI_FRONTEND` and `backend-logic` otherwise. Reviewer lanes are `core`
for `CORE` and `non-critical` otherwise. Feature leads use `default`.
`prePass` is ignored; a reviewer's `pass` is `"record"`.

Selection loads the quota state first (exit 3 on invalid state); the clock is
read once, after the state is loaded, and that aware UTC time is used for both
catalog freshness and refusal expiry. After loading the saved catalog and
validating refusal times (exit 3 on invalid times), every manager primary and
quota fallback present in the catalog must have driver kind `claudeAgent`,
`codex` or `antigravity`; otherwise exit 3. Missing manager providers are not
checked. Live refusal keys map instance/account and model to their latest expiry.
See [quota commands](quota.md) for refusal recording and active-pointer moves.

### Ordered target walk

Index 0 is the primary, followed by quota fallbacks at indexes 1 onward.
Each target is tried once. A direct target checks its instance and records
`{index, selection, account: null, reason}`. A pool target walks accounts in
configured order, starting at the active account if it belongs to this pool,
otherwise at the first account, wrapping once. It records an entry per
account until eligible. Pool walking is not a fallback; pool options are empty.

Checks run in this order; the first failure supplies the reason:

1. Catalog availability: `missing provider in catalog`,
   `canRunChildTask is false`, `canRunCrossProviderChildTask is false`,
   `constraints: <text>`, `model not offered: <model>`, or
   `option not offered: <key>`. Requested option values must match their
   advertised types and values. Omitted options are empty; defaults are not substituted.
2. Driver kind: subscription `claudeAgent`, `codex`, `antigravity`, and
   metered `pi` are allowed for every role and position. Otherwise:
   `driver kind not allowed: <kind>`.
3. Matching driver-kind/role block: `role block: <configured reason>`.
4. Live refusal for this instance/account and model:
   `live quota refusal until <expiresAt>`.

The first eligible entry has reason `selected`. All reasons are bounded
to 512 printable characters. Selection never changes state or its active pointer.

### Record and result

One version-3 `routing-selection` records `configVersion: 3`, `inputRef`,
`inputDigest`, `policyDigest`, `classificationRef` (the decision sequence),
`role`, cell `lane`, integer `stage`, `pass` (`record` for reviewers,
null otherwise), `round`, post-floor `criticality` and `scope`,
`floorsApplied`, the exact configured `selection`, `chosenPoolAccount`
(account or null), `targetsTried`, `chosenIndex`, `configDigest`,
`catalogReadAt`, `catalogDigest` and `quotaStateDigest`.
The walk starts at index 0 with no gaps and ends at the chosen entry.
Digests describe the exact loaded file bytes. A chosen `pi` provider,
including a pool account, adds `billing: "per-token"`; subscriptions omit it.

The result is that payload plus `selectionRef`, resolved
`providerInstanceId`, `model` and `options`. A failed durable append
returns no selection. Exhaustion writes nothing and exits 5 with
`{reason, retryAfter, targetsTried}`. `retryAfter` is the earliest expiry
among entries actually skipped for live refusal, or null if there were none.

## Exits

- 0: recorded.
- 2: invalid arguments, input or records, or append failure; sanitized message.
- 3: invalid config or quota state, including stored refusal times or a
  non-subscription manager found in the catalog; sanitized message.
- 4: current input, decision or catalog needed; one JSON object with `reason`,
  plus `startingStage` when requested below the starting stage.
- 5: no eligible target; one JSON object with `reason`, `retryAfter`,
  `targetsTried`.

Failures never append an event.

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
