# Synthetic router pilot

This is RP-1's runner specification. It supplies observations for Shawn to tune
the confidence threshold, classification criteria and floors under the CD-8
matrix. Fourteen authored examples do not certify accuracy; the runner changes
no configuration. Only the Engineering manager runs live L8 after review.
Offline tests inject HTTP and a synthetic catalog; those passes are offline
evidence only.

## Invocation and prerequisites

Run from the helper repository root on stable CPython 3.15, through uv's global
pin. While the live repository `.local/` configuration remains version 2 during
the MX-1 to MX-3a migration, the Engineering manager provides a private
version-3 configuration directory via `--local-dir <private-v3-state>`:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B <root>/live/router_pilot.py \
  --local-dir <private-v3-state> \
  --examples pilot/router-examples.json \
  --catalog <fresh-catalog-file> --catalog-read-at <UTC-capture-time>
```

For a private JSON report, optionally append `> <private-report-file>`.
Without `--local-dir`, `paths.local_dir()` selects the helper root's `.local/`.
Relative examples/catalog paths resolve from the current directory. All
required arguments parse before config/state I/O; `--help` requires no state.
Abbreviated/unknown options are refused safely.

The [Jev prerequisites](cli/jev.md) apply: validated version-3 config, private
identifier list, and the existing configured key (environment first, configured
file otherwise). Only L8 needs HTTPS access to OpenRouter and that existing
key. The runner adds no machine prerequisite, capture command, credential
mechanism or agent harness. Never put a key in arguments. Supply the saved
catalog using the [routing catalog contract](cli/routing.md): a valid UTC
timestamp ending in `Z`, neither future nor more than 24 hours old at
invocation. Freshness is checked only at invocation: later expiry does not
stop this classification-only run. The catalog is metadata, not a dispatch
permission; the runner never captures catalogs or selects executable targets.

The default dataset expects **14 Jev HTTP calls**, one per example, with
`decline=True` (`--decline-retry`). It retries nothing, dispatches no child,
and never calls `route select`.

## Public examples format

`pilot/router-examples.json` is version 2. Exact document keys are `version`
(integer 2), `label` (exactly `synthetic`) and `examples` (12–32 entries).
The shipped file has exactly 14 software engineering tasks. Each entry has:

- `id`: a unique public identifier matching `[a-z][a-z0-9-]{0,63}`.
- `label`: exactly `synthetic`, declaring fictional task text.
- `task`: the original six Jev fields `intent`, `acceptance`, `interfaces`,
  `scope`, `riskFacts`, `dependencies`, validated by the shared schema with
  null context and the canonical 16 KiB cap.
- `expected`: exactly `lane`, `criticality`, `scope`, `floors`.
  - `lane`: `BACKEND_LOGIC`, `UI_FRONTEND` or `MIXED`.
  - `criticality`: `CORE` or `NON_CRITICAL`.
  - `scope`: `STANDARD` or `COMPLEX`.
  - `floors`: unique configured risk-fact IDs matching `task.riskFacts`.

The file is capped at 512 KiB. Duplicate keys, unknown fields, wrong types,
duplicate IDs/facts/floors and unconfigured facts fail before work or HTTP.
Coverage includes all canonical CD-8 risk facts: `credentials-or-guardrail`,
`recovery-or-concurrency`, `shared-interface` and `document-change`.
Both lanes, both criticalities, both scopes, and at least one technical document
are covered. Gold labels express author expectations independently of current
floor values.

## Records, report and exits

Each invocation has a UUID run prefix and unique per-example work IDs under the
hardcoded `synthetic/router-pilot` repository, written as
`manager@synthetic/router-pilot`. Exclusive work creation preserves prior runs.
Work scope records the example ID and raw examples SHA-256. Jev records
canonical routing input before HTTP, its classification, and any judgment.
No `route select` or selection events occur.

Stdout is one JSON object, labeled `synthetic`, with `version`, `repository`,
`runId`, raw `examplesDigest`, `catalogDigest`/`catalogReadAt`, `complete`,
`failure`, `results` and `summary`. Every example result has its ID/label,
expected labels, work ID, event `recordRefs`, `classification`, `judgment`,
`derived`, `outcome` and sanitized `reason`.

The run pins its initial routing-policy digest. A change before an example or
during its call interrupts the run and preserves partial evidence without deriving
from mismatched floors. Only successful, schema-valid completed version-3 decisions
are derived; defaulted and confident MIXED classifications require their judgment.
Missing answers never create local defaults.

Local derivation follows CD-8 §3:
- Effective decisions take returned judgments (or accepted answers).
- Confident `MIXED` maps to `BACKEND_LOGIC` -> `backend-logic`.
- Configured risk floors only raise:
  - `credentials-or-guardrail` and `recovery-or-concurrency` raise scope to
    `COMPLEX` and criticality to `CORE`.
  - `shared-interface` raises criticality to `CORE`.
  - `document-change` has no raising effect.
- Scope `STANDARD` sets starting stage 1; `COMPLEX` sets starting stage 2.
- Criticality chooses `core` or `non-critical` review class.

Summary comparisons evaluate scope and criticality independently:
- `mistakenCheapRoutes`: expected `COMPLEX` but derived `STANDARD` (scope);
  expected `CORE` but derived `NON_CRITICAL` (criticality).
- `unnecessaryExpensiveRoutes`: expected `STANDARD` but derived `COMPLEX` (scope);
  expected `NON_CRITICAL` but derived `CORE` (criticality).
- `matchingRoutes`: derived matches gold for that question. Floors correcting
  raw answers do not count as mistakes when the final derived class matches gold.
- `laneMismatches`: concrete derived lane differs from gold after normalizing
  confident `MIXED` to `backend-logic`.
- `defaultedAnswers`: reported by question (`lane`, `criticality`, `scope`)
  with counts and example IDs, distinguishing uncertainty (`default-low-confidence`,
  `default-insufficient-context`) from service errors (`default-jev-failure`).

Exit **0 means run completion**, including uncertainty, defaults and mismatches;
it says nothing about routing accuracy or a live pass. Exit **2** means invalid
arguments/examples/catalog or interrupted setup; **3** means invalid config.
Interruption emits partial JSON with `complete: false`, preserving durable
records without retry. Real task inputs remain held until formal pilot lifts.
