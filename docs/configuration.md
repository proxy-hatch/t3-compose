# Routing configuration and quota state

Personal routing configuration and agy quota state live under the Git-ignored `.local/` directory in the t3-compose root:
- `.local/agent-routing.json` — canonical version-2 personal routing configuration.
- `.local/agent-routing-state.json` — canonical per-account agy quota state.

The owner changes routing values, model preferences, account order, thresholds and fallback chains, or the Engineering manager changes them at the owner's explicit instruction. Code validates and applies the configured values without hiding changeable routing preferences in constants or prompts.

The helper loads and validates the configuration from disk on every call without caching across commands or calls. A valid change applies to the next dispatch only, without restarting the manager, while running children keep their recorded selection. Invalid configuration exits `3` (`ExitCode.INVALID_CONFIG`) and blocks new routing rather than falling back to a cached or default value.

Synthetic, public examples live at [`docs/examples/agent-routing.example.json`](examples/agent-routing.example.json) and [`docs/examples/agent-routing-state.example.json`](examples/agent-routing-state.example.json). Command options, output and exit codes are specified in [`docs/cli/config.md`](cli/config.md).

## Python API (`t3compose.config`)

Downstream modules (`jev`, `routing`, `quota`, `records`) consume:
- `load_config(path=None, *, local=None) -> LoadedConfig`: reads and validates the routing config in a single bounded 16 KiB read via `t3compose.jsonio.read_json_with_digest`. Resolves `<local or local_dir()>/agent-routing.json` when `path` is `None`. Performs no caching and never opens `jev.keyFile.path`.
- `LoadedConfig`: frozen dataclass with deeply read-only `data` (`MappingProxyType` and `tuple`), exact file byte `digest` (`sha256:<hex>`), canonical `policy_digest` (`sha256:<hex>`), and read-only `notices` tuple.
- `load_quota_state(path=None, *, local=None, config=None) -> LoadedQuotaState`: reads and validates the quota state file in a single bounded 16 KiB read. When `config` is supplied, verifies that `activeProviderInstanceId` and every key in `quotaObservations` belong to a configured provider pool.
- `LoadedQuotaState`: frozen dataclass with deeply read-only `data` and exact file byte `digest` (`sha256:<hex>`).
- `routing_policy_projection(config_data) -> dict`, `compute_policy_digest(config_data) -> str`, and `ConfigValidationError` (carrying read-only `.errors` and `.missing_sections`).

## Routing-policy digest projection

Every `routing-input`, `routing-classification`, `routing-judgment` and `routing-selection` record stores two distinct digests:
1. **Config byte digest (`digest`)**: `sha256:<hex>` of the exact bytes read from `.local/agent-routing.json`.
2. **Routing-policy digest (`policy_digest`)**: `sha256:<hex>` of `t3compose.jsonio.canonical_json(routing_policy_projection(data))` covering the exact fields that govern task classification and risk-floor freshness:
   - **Included**: `classification.ruleVersion`, `classification.domain.{instructions,criteria}`, `classification.difficulty.{instructions,criteria}`, `riskFacts` (every fact ID, `description` and `floor`), and `jev.{provider,model,minConfidence,maxContextRetries,uncertainResult}`.
   - **Excluded**: `master`, `modelPreferences`, `easyWorker`, `textHelper`, `providerPools`, `roundTiers`, `jev.timeoutSeconds`, `jev.keyFile`, `availabilityFallback`, and JSON whitespace or key order.

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
