# Routing commands

Use the [shared CLI contract](../cli.md) for invocation, global options, output,
errors and registration. `--repo REPO` accepts the records API's repository
identity, origin URL or checkout path; `--work ID` selects an existing work.
Each command reloads the config under the selected local directory. There is
no writer option: appends use `load_work(...).effectiveWork.writer` and the
[records store](../records.md) validates that writer and the complete history.

```text
t3c route input --repo REPO --work ID --input TASK.json
t3c route judge --repo REPO --work ID --input-ref SEQ --policy-digest DIGEST \
  --domain SOFTWARE_ENGINEERING|OTHER --difficulty EASY|MEDIUM|CHALLENGING --reason TEXT
t3c route select --repo REPO --work ID --role worker|reviewer|feature-lead|text-helper \
  --input-ref SEQ --policy-digest DIGEST --catalog FILE --catalog-read-at UTC \
  [--round first|corrections] [--review-scope code|document|corrections-delta] [--escalate]
```

`input` reads a task object with exactly `intent`, `acceptance`, `interfaces`,
`scope`, `riskFacts`, `dependencies`. It records canonical `{task, context:null}`
through the common 16 KiB input recorder and returns
`{inputRef, inputDigest, policyDigest}`. The supplied JSON file is bounded to
1 MiB before canonical validation. `judge` records basis `manual` and returns
the appended `routing-judgment` envelope. Domain and difficulty use the exact
uppercase choices above. Reason must be nonempty text.

Both `judge` and `select` require the latest input sequence and its current
policy digest. Changed input, criteria, instructions, floors or confidence
threshold require a new input and classification or judgment. Selection uses
the latest decision matching that input, its digest and current policy; an
unaccepted or low-confidence classification needs judgment. A fixed text helper
needs the current bounded input but no classification. Unknown declared facts
block tier calculation. Model preferences and round tiers reload next command
without invalidating an otherwise current decision.

First-round Workers take the maximum difficulty and declared fact floors.
Corrections use `roundTiers.corrections` in place of first-round floors; use
this round only for enumerated findings. `--escalate` is Worker-only, default
false: take the maximum of the configured round base and one tier above the
highest prior Worker selection of that same round. No such history, or a
prior challenging tier, requires judgment. `--round` defaults to `first`.

Reviewer `--review-scope` defaults to `code`: code review takes at least medium
and follows the highest prior Worker tier, including escalation. `document`
and `corrections-delta` use `roundTiers.documentReview` and
`roundTiers.correctionsReview`. Those overrides apply no first-round floors.
Feature leads take the maximum of the current difficulty/floor tier and the
Task's highest prior Worker tier, at least medium. Worker history includes
escalation and survives fresh inputs on the same Task, for leads and code review.
Non-Reviewer scope is ignored and stored as null. The fixed text helper uses
`textHelper`, recorded at easy with no applied floors or decision reference.

`--catalog` is a saved capabilities object with `providers[]`; no service is
called. Capture time must be UTC ending in `Z`, not future, and at most 15
minutes old (inclusive). Needed provider fields are `providerInstanceId`,
`driverKind`, strict boolean `canRunChildTask` and
`canRunCrossProviderChildTask`, string-list `constraints`, and `models[]`.
Each model has `id` and optional `options[]` with `id` and `values` (strings or
booleans). Duplicate IDs, missing or malformed needed fields fail closed.
Extra metadata is ignored. Disabled/unsupported entries can have valid shape.
Availability requires both flags true, no constraints, exact model and requested
option values including their types. Omitted options are empty; model defaults
are not substituted for requested options.

Only subscription drivers `claudeAgent`, `codex`, `antigravity` are authorized
in this stage. Every other driver, including `pi`, is unavailable for every
role. A master catalog entry resolving to any other driver makes config
invalid. Antigravity never serves Reviewers. Configured role limits apply to
preferences and fallback targets; medium pool selections serve Workers only.

Before a pool walk, a missing active account, false capability or constraints
requires judgment without walking. An active account outside the selected pool
also requires judgment. Otherwise start at active and wrap once in configured
order. Skip unavailable accounts, missing model/options and confirmed exhausted
accounts; unknown quota is usable. Every skip records its reason. Missing
models/options are never exhaustion evidence. The same walk serves `easyWorker`
and the fixed helper. Selection changes neither quota evidence nor active account.

A failed preference may use its one same-tier configured fallback. Role limits
and catalog availability are checked again. A pool target walks normally but
cannot follow another chain. No applicable fallback or an unavailable target
requires reporting; direct fallback retains all pool skips and has null chosen
account. Pool records retain the pool selection and `chosenPoolAccount`.

Successful `select` appends only a `routing-selection` and returns its data plus
`selectionRef`, resolved `providerInstanceId`, `model`, `options`. Stored data
includes input/policy/decision references, role/round/scope/tier/floors/escalation,
selection/account/skips, availability/fallback reasons, exact config/catalog/
quota-state digests and catalog read time. No billing field is written.

Exits: 0 recorded; 2 invalid input/records/options; 3 invalid config/quota times
or non-subscription master; 4 current input/decision, escalation or catalog/pool
preflight needs judgment; 5 no eligible selection after at most one fallback.
Exits 4/5 return `reason`, plus `skippedPoolAccounts` for availability failures
and `fallbackReason` when a tried fallback fails. Reasons are bounded to 512
characters. Failures never append a fabricated selection. Exits 2/3 have a
sanitized message only.

Reusable `routing.evaluate_quota(observations, now)` accepts validated account
observations and an aware UTC datetime; it returns `unknown`, `exhausted` or
`available` without mutation. Latest observation time wins, with the last list
entry breaking ties. Reset strictly before now clears exhaustion to unknown;
later available evidence clears it immediately. Invalid/future observation times
or reset before observation raise `ValueError` (selection exits 3). Quota applies
across all models on an account. Quota commands, mutations and metered billing
are deferred to RT-1b.
