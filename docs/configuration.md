# Routing configuration and quota state

Personal routing configuration and quota state live under the Git-ignored `.local/` directory in the t3-compose root:

- `.local/agent-routing.json` — canonical version-3 personal routing configuration.
- `.local/agent-routing-state.json` — canonical quota state, including legacy observations.

The owner changes routing values, model preferences, account order and thresholds, or the Engineering manager changes them at the owner's explicit instruction. Code validates the configured values before new routing. Each load reads disk without caching; a valid change applies to the next dispatch while running children keep their recorded selection. Invalid configuration exits `3` (`ExitCode.INVALID_CONFIG`) and blocks new routing.

Public synthetic examples are the [version-3 config](examples/agent-routing.v3.example.json) and [quota state](examples/agent-routing-state.example.json). The [old version-2 example](examples/agent-routing.example.json) remains byte-identical as a rejected historical fixture for config, quota, Jev and routing tests. It is not a usable configuration. See the [command contract](cli/config.md) for output and exit codes.

## Migration to version 3

Version 2 and version 1 cannot load. Start from the version-3 example and transfer the intended policy into `manager`, the role/lane/stage `matrix`, `helpers`, classification questions and risk floors. Remove the obsolete tier and chain fields (`master`, `modelPreferences`, `easyWorker`, `textHelper`, `roundTiers`, `availabilityFallback`), and `jev.uncertainResult`. Configure `quota` and `escalation`; retain the intended pool membership and Jev key-file reference. This is a deliberate policy migration, not an automatic conversion.

Remove `helpers.search` and every `roleBlocks` entry with role `search`. Helpers are exactly `verifier` and `text`; leads search themselves. Historical routing records, including search selections and version-2 shapes, remain readable. Existing quota observations remain readable in their list form.

Any object whose version is not integer `3` (including a missing version, boolean, string or unknown version) fails with `version (expected-version-3)`. The loader exception includes this stable guidance, and `config check` includes it in the JSON `migration` field:

```text
Migrate routing configuration to version 3; see docs/configuration.md.
```

Non-objects fail with `$ (expected-object)`. No obsolete missing-version-2-section diagnostics are emitted.

## Python API (`t3compose.config`)

- `load_matrix_config(path=None, *, local=None) -> LoadedConfig`: version-3 loader; resolves `<local or local_dir()>/agent-routing.json` unless `path` is given. Performs one bounded 16 KiB byte read, validates without caching, and never opens `jev.keyFile.path`.
- `load_config(path=None, *, local=None)`: compatibility alias of `load_matrix_config`, with the same version-3-only behavior, limits and return value.
- `validate_config_data(raw)`: validates version 3 and returns deeply read-only data and an empty notices tuple. Invalid versions fail before field validation.
- `MATRIX_LANES`: read-only mapping of `feature-lead` to `("default",)`, `worker` to `("backend-logic", "ui-frontend")`, and `reviewer` to `("non-critical", "core")`. `MATRIX_OPTIONAL_LANES` maps `worker` to `("docs",)`, a lane the config may omit, and `OPTIONAL_CRITERIA` maps `lane` to `("DOCS",)`, a lane criterion the config may omit. `MATRIX_STAGES` is `("1", "2", "3")`; `HELPER_NAMES` is `("verifier", "text")`; `ROLE_BLOCK_ROLES` contains those three matrix roles and two helpers; `DRIVER_KINDS` is `("claudeAgent", "codex", "antigravity", "pi")`.
- `LoadedConfig`: frozen dataclass with deeply read-only `data` (`MappingProxyType` and `tuple`), exact file byte `digest`, canonical `policy_digest` (both `sha256:<hex>`), and empty `notices` tuple.
- `load_quota_state(path=None, *, local=None, config=None) -> LoadedQuotaState`: bounded 16 KiB read with no caching. Accepts a loaded config or its data. With config supplied, checks the active pointer and observation keys against pool membership. Refusals receive shape validation only, without membership or timestamp evaluation.
- `LoadedQuotaState`: frozen dataclass with deeply read-only `data` and exact file byte `digest`.
- `routing_policy_projection(config_data) -> dict`, `compute_policy_digest(config_data) -> str`, and `ConfigValidationError` with read-only `.errors` and sanitized `.to_payload()` output.

Read failures use `config (unreadable-file)`, `config (size-limit-exceeded)` or `config (invalid-json)`. State errors use `state` prefixes. Errors never echo file paths or input values from the configuration.

## Routing-policy digest projection

The byte digest hashes the exact bytes read, including whitespace. The policy digest hashes canonical UTF-8 JSON from `routing_policy_projection(data)`:

- Included: the whole `classification` and `riskFacts`, and `jev.{provider,model,minConfidence,maxContextRetries}`.
- Excluded: `manager`, `matrix`, `helpers`, `providerPools`, `roleBlocks`, `quota`, `escalation`, `jev.timeoutSeconds`, `jev.keyFile`, whitespace and key order.
- The optional `DOCS` lane criterion is part of `classification`, so adding or removing it changes the policy digest and asks for a new routing input. The optional worker `docs` lane is part of `matrix`, so adding or removing it does not.

Selections can change the byte digest without changing classification freshness. Historical record schemas retain their original shapes; this loader does not rewrite records.

## Config schema (version 3)

The [version-3 example](examples/agent-routing.v3.example.json) uses synthetic
IDs. It uses one cell per line and fits the 16 KiB read cap. Validation collects errors with dotted paths and `[i]` list
indices, without echoing input values. Missing fields use `missing-key`, extra
fields use `unknown-key`, and wrong object shapes use `expected-object`.

The top-level object is closed and requires `version` (integer `3`), `manager`,
`matrix`, `helpers`, `providerPools`, `roleBlocks`, `quota`, `classification`,
`riskFacts`, `escalation` and `jev`. All version-2-only fields are unknown.

- `providerPools` is validated before selections: non-empty object, non-empty pool names, exact `exhaust-in-order` mode, and non-empty lists of unique non-empty IDs, with no member in two pools.
- A selection requires `model` (non-empty string), `options`, and exactly one of `providerInstanceId` or `providerPool`. Both targets produce `disjoint-selection-keys`; neither produces `missing-selection-target`. No other field, including `roles`, is allowed. Non-empty strings use `invalid-string` on failure. Options require an object, non-empty string keys (`invalid-option-key`), and string or boolean values (`invalid-option-value`). A pool must exist (`unknown-provider-pool`) and have exactly `{}` options (`pool-options-must-be-empty`). A direct ID must be non-empty and name no pool member (`direct-selection-names-pool-member`).
- A cell requires `primary` (selection) and `quotaFallback` (list of selections, possibly empty; otherwise `expected-list`). Fallback identity is target kind, target name, model and canonical options. A fallback equal to its primary uses `fallback-equals-primary`; a repeat uses `duplicate-fallback` at the later list entry. Different effort options give different identities.
- A cell may have `prePass` (selection) only in reviewer cells, in either lane at any stage. It can equal the primary or a fallback. On all other cells it produces only `pre-pass-outside-reviewer-cell` at `.prePass`, without validating that selection. Other extra fields are unknown.
- `manager` is a cell whose primary and fallbacks must be direct (`expected-direct-selection` at `.providerPool`). Driver eligibility, including the prohibition on Pi managers, is a catalog check for MX-3a.
- `matrix` requires exactly the roles and lanes in `MATRIX_LANES`; every lane requires exactly stages `"1"`, `"2"`, `"3"`, each a cell. Missing and extra roles, lanes or stages use `missing-key` and `unknown-key` at their paths. The `worker` role also accepts the optional `docs` lane (`MATRIX_OPTIONAL_LANES`) with the same three stages; no other role accepts it. Without a `docs` lane, a `DOCS` decision routes to the `backend-logic` lane and its selection records `laneFallback` (see [the docs lane](routing-matrix.md#the-docs-lane)).
- `helpers` requires exactly `verifier`, `text`, each a cell without a pre-pass. A helper walks its `quotaFallback` list after its primary, like a matrix cell.
- `roleBlocks` is a possibly empty list (`expected-list` otherwise). Entries require exactly non-empty strings `driverKind`, `role`, `reason`, `evidence`. Driver kinds must be in `DRIVER_KINDS` (`invalid-driver-kind`); roles in `ROLE_BLOCK_ROLES` (`invalid-role-block-role`). Duplicate `(driverKind, role)` pairs produce `duplicate-role-block` at the later entry. Other driver kinds remain unavailable.
- `quota` requires exactly `refusalRetryMinutes`, integer 1..1440, never boolean (`invalid-refusal-retry-minutes`).
- `classification` requires exactly non-empty `ruleVersion`, `lane`, `criticality`, `scope`. Each question requires exactly non-empty `instructions` and a closed `criteria` object of non-empty strings. Lane criteria are `BACKEND_LOGIC`, `UI_FRONTEND`, `MIXED` and the optional `DOCS` (an empty text is `invalid-string`; Jev is offered `DOCS` only when the key is present); criticality criteria are `CORE`, `NON_CRITICAL`; scope criteria are `STANDARD`, `COMPLEX`, `INSUFFICIENT_CONTEXT`.
- `riskFacts` is a non-empty object (`expected-object` or `empty-object`). IDs match `^[a-z]+(?:-[a-z]+)*$` (`invalid-fact-id`). Each fact requires non-empty `description`, optionally `scope: "COMPLEX"` and `criticality: "CORE"`. Other floor values use `invalid-floor`. No floors is valid. The old `floor` key is unknown.
- `escalation` requires exactly `repairAttempts` and `correctionsRoundsPerFinding`, each integer 1..10, never boolean (`invalid-repair-attempts` and `invalid-corrections-rounds-per-finding`).
- `jev` requires exactly `provider` (`"openrouter"`), `model` (non-empty string), `minConfidence` (number `0 < x <= 1`, never boolean), `maxContextRetries` (integer `0` or `1`, never boolean), `timeoutSeconds` (integer `1..120`, never boolean), and `keyFile` (exactly `path`, an absolute path string, and `field`, a non-empty string). Invalid values use `invalid-jev-provider`, `invalid-string`, `invalid-min-confidence`, `invalid-max-context-retries`, `invalid-timeout-seconds`, or `expected-absolute-path` respectively. `uncertainResult` is unknown: uncertain defaults are fixed in code. The key file is never opened.

## Quota state schema and migration compatibility (L1)

`.local/agent-routing-state.json` requires `activeProviderInstanceId` (configured pool member), `quotaObservations` (mapping pool-member IDs to lists of `{observedAt, status: "exhausted" | "available", resetAt: string | null, evidence}` objects), and `note` (string). Observations are per account across all pool models; model rollout gaps are not recorded as quota exhaustion. If an earlier prose state file stored a single observation object under an instance key instead of a list, `t3c config check` refuses it with exit `3` (`state.quotaObservations.<instance> (expected-list)`). During post-merge step L1, wrap each legacy single observation object in a one-element list `[{...}]` with `observedAt`, `status`, `resetAt` and `evidence`.


The optional top-level `refusals` list defaults to none when absent. Every entry
has exactly `providerInstanceId`, `model`, `kind`, `observedAt`, `resetAt` and
`evidence`. Instance (direct provider or pool account), model, observation and
evidence are non-empty strings. `kind` is one of `usage-limit`, `quota-exhausted`,
`credits`, `rate-limit` (`REFUSAL_KINDS`); the exact keys are `REFUSAL_KEYS`.
`resetAt` is null or a non-empty string. No membership check applies to refusals;
they remain valid when pools change. Duplicate instance/model keys are valid.

Shape errors use `state.refusals (expected-list)`, `state.refusals[i]
(expected-object)`, or `state.refusals[i].<key>` with `unknown-key`, `missing-key`
or `invalid-string`. Invalid kind uses `invalid-refusal-kind`, invalid reset
uses `invalid-reset-at`. `config check` accepts valid shapes with version-3 config. It does not evaluate refusal times. The [quota commands](cli/quota.md)
check UTC timestamps and expiry, serialize writes with a state lock, and publish
atomically with mode 0600. Old observations stay readable and preserved but no
longer gate quota commands. The state retains its required keys unchanged.
