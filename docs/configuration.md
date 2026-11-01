# Routing configuration and quota state

Personal routing configuration and agy quota state live under the Git-ignored `.local/` directory in the t3-compose root:
- `.local/agent-routing.json` — canonical personal routing configuration, version 2 or 3 until MX-3b removes version 2.
- `.local/agent-routing-state.json` — canonical per-account agy quota state.

The owner changes routing values, model preferences, account order, thresholds and fallback chains, or the Engineering manager changes them at the owner's explicit instruction. Code validates and applies the configured values without hiding changeable routing preferences in constants or prompts.

The helper loads and validates the configuration from disk on every call without caching across commands or calls. A valid change applies to the next dispatch only, without restarting the manager, while running children keep their recorded selection. Invalid configuration exits `3` (`ExitCode.INVALID_CONFIG`) and blocks new routing rather than falling back to a cached or default value.

Synthetic, public examples live at [`docs/examples/agent-routing.example.json`](examples/agent-routing.example.json) and [`docs/examples/agent-routing-state.example.json`](examples/agent-routing-state.example.json). Command options, output and exit codes are specified in [`docs/cli/config.md`](cli/config.md).

## Python API (`t3compose.config`)

Downstream modules (`jev`, `routing`, `quota`, `records`) consume:
- `load_config(path=None, *, local=None) -> LoadedConfig`: version-2 accessor; refuses integer version 3 with exactly `version (expected-version-2)` before schema validation. Reads and validates the routing config in a single bounded 16 KiB read via `t3compose.jsonio.read_json_with_digest`. Resolves `<local or local_dir()>/agent-routing.json` when `path` is `None`. Performs no caching and never opens `jev.keyFile.path`.
- `load_matrix_config(path=None, *, local=None) -> LoadedConfig`: version-3 accessor with the same path resolution, single bounded read, no caching and no key-file access. Returns notices `()`. Non-objects produce `$ (expected-object)`; every version other than integer `3` produces only `version (expected-version-3)`.
- `validate_config_data(raw)`: dispatches integer version `3` to matrix validation, preserves the version-1 refusal and version-2 validation, and returns deeply read-only data and notices. Booleans never count as versions. Unknown versions retain `invalid-version`.
- `MATRIX_LANES`: read-only mapping of `feature-lead` to `("default",)`, `worker` to `("backend-logic", "ui-frontend")`, and `reviewer` to `("non-critical", "core")`. `MATRIX_STAGES` is `("1", "2", "3")`; `HELPER_NAMES` is `("verifier", "search", "text")`; `ROLE_BLOCK_ROLES` contains the three matrix roles followed by the three helpers; `DRIVER_KINDS` is `("claudeAgent", "codex", "antigravity", "pi")`.
- `LoadedConfig`: frozen dataclass with deeply read-only `data` (`MappingProxyType` and `tuple`), exact file byte `digest` (`sha256:<hex>`), canonical `policy_digest` (`sha256:<hex>`), and read-only `notices` tuple.
- `load_quota_state(path=None, *, local=None, config=None) -> LoadedQuotaState`: reads and validates the quota state file in a single bounded 16 KiB read. Accepts version-2 or version-3 `LoadedConfig` or data. When `config` is supplied, verifies that `activeProviderInstanceId` and every key in `quotaObservations` belong to a configured provider pool.
- `LoadedQuotaState`: frozen dataclass with deeply read-only `data` and exact file byte `digest` (`sha256:<hex>`).
- `routing_policy_projection(config_data) -> dict`, `compute_policy_digest(config_data) -> str`, and `ConfigValidationError` (carrying read-only `.errors` and `.missing_sections`).

## Routing-policy digest projection

Every `routing-input`, `routing-classification`, `routing-judgment` and `routing-selection` record stores two distinct digests:
1. **Config byte digest (`digest`)**: `sha256:<hex>` of the exact bytes read from `.local/agent-routing.json`.
2. **Routing-policy digest (`policy_digest`)**: `sha256:<hex>` of `t3compose.jsonio.canonical_json(routing_policy_projection(data))` covering the exact fields that govern task classification and risk-floor freshness:
   - **Included**: `classification.ruleVersion`, `classification.domain.{instructions,criteria}`, `classification.difficulty.{instructions,criteria}`, `riskFacts` (every fact ID, `description` and `floor`), and `jev.{provider,model,minConfidence,maxContextRetries,uncertainResult}`.
   - **Excluded**: `master`, `modelPreferences`, `easyWorker`, `textHelper`, `providerPools`, `roundTiers`, `jev.timeoutSeconds`, `jev.keyFile`, `availabilityFallback`, and JSON whitespace or key order.

For version 3 the projection includes the whole `classification` and `riskFacts`,
and only `jev.{provider,model,minConfidence,maxContextRetries}`. It excludes
`manager`, `matrix`, `helpers`, `providerPools`, `roleBlocks`, `quota`,
`escalation`, `jev.timeoutSeconds`, `jev.keyFile`, whitespace and key order.
The version-2 projection above stays unchanged.

## Config schema (version 3)

The [version-3 example](examples/agent-routing.v3.example.json) uses synthetic
IDs. It is 7,784 bytes on 82 lines, with one cell per line. The same 16 KiB
read cap applies. Validation collects errors with dotted paths and `[i]` list
indices, without echoing input values. Missing fields use `missing-key`, extra
fields use `unknown-key`, and wrong object shapes use `expected-object`.

The top-level object is closed and requires `version` (integer `3`), `manager`,
`matrix`, `helpers`, `providerPools`, `roleBlocks`, `quota`, `classification`,
`riskFacts`, `escalation` and `jev`. All version-2-only fields are unknown.

- `providerPools` follows the version-2 rules below and is validated before selections: non-empty object, non-empty pool names, exact `exhaust-in-order` mode, and non-empty lists of unique non-empty IDs, with no member in two pools.
- A selection requires `model` (non-empty string), `options`, and exactly one of `providerInstanceId` or `providerPool`. Both targets produce `disjoint-selection-keys`; neither produces `missing-selection-target`. No other field, including `roles`, is allowed. Non-empty strings use `invalid-string` on failure. Options require an object, non-empty string keys (`invalid-option-key`), and string or boolean values (`invalid-option-value`). A pool must exist (`unknown-provider-pool`) and have exactly `{}` options (`pool-options-must-be-empty`). A direct ID must be non-empty and name no pool member (`direct-selection-names-pool-member`).
- A cell requires `primary` (selection) and `quotaFallback` (list of selections, possibly empty; otherwise `expected-list`). Fallback identity is target kind, target name, model and canonical options. A fallback equal to its primary uses `fallback-equals-primary`; a repeat uses `duplicate-fallback` at the later list entry. Different effort options give different identities.
- A cell may have `prePass` (selection) only in reviewer cells, in either lane at any stage. It can equal the primary or a fallback. On all other cells it produces only `pre-pass-outside-reviewer-cell` at `.prePass`, without validating that selection. Other extra fields are unknown.
- `manager` is a cell whose primary and fallbacks must be direct (`expected-direct-selection` at `.providerPool`). Driver eligibility, including the prohibition on Pi managers, is a catalog check for MX-3a.
- `matrix` requires exactly the roles and lanes in `MATRIX_LANES`; every lane requires exactly stages `"1"`, `"2"`, `"3"`, each a cell. Missing and extra roles, lanes or stages use `missing-key` and `unknown-key` at their paths.
- `helpers` requires exactly `verifier`, `search`, `text`, each a cell without a pre-pass.
- `roleBlocks` is a possibly empty list (`expected-list` otherwise). Entries require exactly non-empty strings `driverKind`, `role`, `reason`, `evidence`. Driver kinds must be in `DRIVER_KINDS` (`invalid-driver-kind`); roles in `ROLE_BLOCK_ROLES` (`invalid-role-block-role`). Duplicate `(driverKind, role)` pairs produce `duplicate-role-block` at the later entry. Other driver kinds remain unavailable.
- `quota` requires exactly `refusalRetryMinutes`, integer 1..1440, never boolean (`invalid-refusal-retry-minutes`).
- `classification` requires exactly non-empty `ruleVersion`, `lane`, `criticality`, `scope`. Each question requires exactly non-empty `instructions` and a closed `criteria` object of non-empty strings. Lane criteria are `BACKEND_LOGIC`, `UI_FRONTEND`, `MIXED`; criticality criteria are `CORE`, `NON_CRITICAL`; scope criteria are `STANDARD`, `COMPLEX`, `INSUFFICIENT_CONTEXT`.
- `riskFacts` is a non-empty object (`expected-object` or `empty-object`). IDs match `^[a-z]+(?:-[a-z]+)*$` (`invalid-fact-id`). Each fact requires non-empty `description`, optionally `scope: "COMPLEX"` and `criticality: "CORE"`. Other floor values use `invalid-floor`. No floors is valid. The old `floor` key is unknown.
- `escalation` requires exactly `repairAttempts` and `correctionsRoundsPerFinding`, each integer 1..10, never boolean (`invalid-repair-attempts` and `invalid-corrections-rounds-per-finding`).
- `jev` follows the version-2 field rules below, except `uncertainResult` is unknown and not required. The uncertain defaults are fixed in code. Provider, confidence, retries, timeout and key-file rules retain their version-2 error names. The key file is never opened.

## Config schema (version 2)

Top-level object keys are closed and required:
- `version`: integer `2` (`bool` is rejected). Version `1` is refused with exit `3` and `missingSections` naming any absent or incomplete version-2 sections (`classification`, `riskFacts`, `roundTiers`, `jev`, `availabilityFallback` and their required nested keys).
- `master`: direct `Selection` for the Engineering manager (never a pool or pool member).
- `modelPreferences`: required domains `software-engineering` and `non-engineering`, each with `medium` (`MediumSelection`: direct `Selection` or `WorkerPoolSelection` with `"roles": ["worker"]`) and `challenging` (direct `Selection` only).
- `easyWorker` and `textHelper`: `PoolSelection` without `roles`.
- `providerPools`: non-empty mapping of `<pool-name>` to `{"selection": "exhaust-in-order", "providerInstanceIds": ["<id>", ...]}` with unique non-empty instance IDs within and across pools.
- `classification`: `ruleVersion`, `domain` (`instructions` and `criteria` keys `SOFTWARE_ENGINEERING`, `OTHER`, `MIXED_OR_UNCLEAR`), and `difficulty` (`instructions` and `criteria` keys `EASY`, `MEDIUM`, `CHALLENGING`, `INSUFFICIENT_CONTEXT`).
- `riskFacts`: non-empty mapping of `<fact-id>` (`^[a-z]+(?:-[a-z]+)*$`) to `{"description": "<text>", "floor": "medium" | "challenging"}` (never `easy`).
- `roundTiers`: `corrections` (`"easy" | "medium" | "challenging"`), `correctionsReview` (`"medium" | "challenging"`), and `documentReview` (`"medium" | "challenging"`). Reviewer tiers are never `easy`.
- `jev`: `provider` (`"openrouter"`), `model` (non-empty string), `minConfidence` (`0 < x <= 1`), `maxContextRetries` (`0` or `1`), `uncertainResult` (`"manager-judgment"`), `timeoutSeconds` (`1..120`), and `keyFile` (`{"path": "/...", "field": "<text>"}`, syntax-checked only and never opened during config validation).
- `availabilityFallback`: `roles` (`["worker", "reviewer", "feature-lead"]`), `manager` (`"owner-choice"`), `recordReason` (`true`), `reportReason` (`true`), and `chains` (`MediumChain` and `ChallengingChain` entries).
- Direct `Selection` (`providerInstanceId`) and `PoolSelection` (`providerPool`) are disjoint; `options` values must be strings or booleans. No direct `Selection` in any slot may name a `providerInstanceId` in any configured `providerPools.*.providerInstanceIds`.
- Direct selections accept optional `roles` (non-empty duplicate-free subset of `availabilityFallback.roles`). Pool selections in `modelPreferences.*.medium` or `medium` chains require exact `"roles": ["worker"]`; pool selections are forbidden at `challenging`.
- Every chain requires `tier` (`"medium" | "challenging"`), `from`, and single-element `to`. Each `(tier, from)` identity may appear at most once, and `to[0]` must differ from `from` regardless of `roles`. Missing reverse chains at the same tier emit a `missing-reverse-chain` notice without failing.

## Quota state schema and migration compatibility (L1)

`.local/agent-routing-state.json` requires `activeProviderInstanceId` (configured pool member), `quotaObservations` (mapping pool-member IDs to lists of `{observedAt, status: "exhausted" | "available", resetAt: string | null, evidence}` objects), and `note` (string). Observations are per account across all pool models; model rollout gaps are not recorded as quota exhaustion. If an earlier prose state file stored a single observation object under an instance key instead of a list, `t3c config check` refuses it with exit `3` (`state.quotaObservations.<instance> (expected-list)`). During post-merge step L1, wrap each legacy single observation object in a one-element list `[{...}]` with `observedAt`, `status`, `resetAt` and `evidence`.
