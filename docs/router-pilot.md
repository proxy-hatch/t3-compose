# Synthetic router pilot

This is RP-1's runner specification. It supplies observations for Shawn to tune
the confidence threshold, classification criteria and floors. Fourteen authored
examples do not certify accuracy; the runner changes no configuration. Only the
Engineering manager runs live L8 after review and L1. Offline tests inject HTTP
and a synthetic catalog; those passes are offline evidence only.

## Invocation and prerequisites

Run from the helper repository root on stable CPython 3.15, through uv's global
pin. This is the exact L8 invocation, with placeholders for an already saved,
fresh catalog and its actual capture timestamp:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B <root>/live/router_pilot.py \
  --examples pilot/router-examples.json \
  --catalog <fresh-catalog-file> --catalog-read-at <UTC-capture-time>
```

For a private JSON report, optionally append `> <private-report-file>`.
`--local-dir LOCAL` explicitly selects helper state (required in tests).
Without it, `paths.local_dir()` selects the helper root's `.local/`, independent
of the current directory. Relative examples/catalog paths resolve from the
current directory. All three required arguments parse before config/state I/O;
`--help` requires no state. Abbreviated/unknown options are refused safely.

The [Jev prerequisites](cli/jev.md) apply: validated config, private identifier
list, and the existing configured key (environment first, configured file
otherwise). Only L8 needs HTTPS access to OpenRouter and that existing key.
The runner adds no machine prerequisite, capture command, credential mechanism
or agent harness. Never put a key in arguments. Supply the saved catalog using
the [routing catalog contract](cli/routing.md): a valid UTC timestamp ending
in `Z`, neither future nor more than 15 minutes old at each selection. The
runner validates it before any HTTP; the real selector rechecks freshness and
policy after classification. Expiry during the run remains a route refusal;
the runner never refreshes the timestamp or captures another catalog.

The default dataset expects **14 Jev HTTP calls**, one per example, when setup
and storage remain valid. Mixed, low-confidence, insufficient-context, API-error
and malformed answers retain their recorded outcomes. Even when Jev offers
`retry-with-context`, this pilot supplies no context, retries nothing and
inserts no manual judgment. It launches no child and dispatches no selection.

## Public examples format

`pilot/router-examples.json` is version 1. Exact document keys are `version`
(integer 1), `label` (exactly `synthetic`) and `examples` (12–32 entries).
The shipped file has exactly 14. Each entry has exactly:

- `id`: a unique public identifier matching `[a-z][a-z0-9-]{0,63}`.
- `label`: exactly `synthetic`, declaring fictional task text.
- `task`: the original six Jev fields `intent`, `acceptance`, `interfaces`,
  `scope`, `riskFacts`, `dependencies`, validated by the shared schema with
  null context and the canonical 16 KiB cap.
- `expected`: exactly `domain`, `tier`, `floors`. Domain is
  `SOFTWARE_ENGINEERING`, `OTHER` or `MIXED_OR_UNCLEAR`. Tier is `easy`, `medium`
  or `challenging`; only mixed/unclear has null tier, meaning expected manager
  judgment. Floors are unique configured risk-fact IDs, matching the task's
  declared facts as a set. They label facts, rather than model choices.

The whole file is capped at 512 KiB. Duplicate JSON keys, unknown/missing fields,
wrong types, duplicate IDs/facts/floors and unknown facts fail before work or
HTTP. Coverage must include `credentials-or-guardrails`,
`recovery-or-concurrency`, `shared-interface` and `technical-documentation`.
Gold labels express author expectations independently of current floor values.
Floor application remains the selector's responsibility. Floors describe what
changes; a security design note gets the documentation fact, not a credential
implementation fact. Examples contain no real task text or private identifiers.
The task alone enters Jev state; labels and expectations never enter state or
questions. Questions remain the configured JV-1 questions without pilot edits.

## Records, report and exits

Each invocation has a UUID run prefix and unique per-example work IDs under the
hardcoded `synthetic/router-pilot` repository, written as
`manager@synthetic/router-pilot`. Exclusive work creation preserves prior runs.
Work scope records the example ID and exact raw examples-file SHA-256 from
`read_json_with_digest`. Jev records canonical routing input before HTTP and
then its classification. Only accepted results enter the real `routing.command`
with Worker, first round, actual input reference/policy digest, catalog and
capture time. Configuration and quota are validated up front without opening
credentials; only JV-1 looks up credentials. No quota or config is mutated.

Stdout is one JSON object, labeled `synthetic`, with `version`, `repository`,
`runId`, raw `examplesDigest`, initial `catalogDigest`/`catalogReadAt`, `complete`,
`failure`, `results` and `summary`. Every example has its ID/label, expected
labels, work ID, event `recordRefs`, `classification`, `selection`,
`routeExitCode`, `outcome` and sanitized `reason`. Classification retains the
answers (choice, probabilities, confidence), input/policy/criteria digests,
rule version, declared facts, configured Jev model, snapshot, response ID and
classification reference. Failed answers keep JV-1's explicit unknown object.
Selection is null unless actually recorded; otherwise it includes final tier,
applied floors, input/classification/selection references and input/policy/
config/catalog/quota digests and capture time. Full execution-model/account
details remain in private routing records; stdout omits them and raw backend
errors. The work ID and references join those records to the exact dataset.

Summary counts use selected **final tiers**, never raw classified difficulty:

- `mistakenCheapRoutes` / `unnecessaryExpensiveRoutes`: lower / higher final
  tier than gold, only with matching domain and a non-null gold tier.
- `matchingRoutes`: equal final tier under those same comparison conditions;
  it is a tier comparison, not an accuracy certification.
- `domainMismatches`: any concrete observed domain differing from gold,
  including an uncertain answer; no tier error is inferred for these rows.
- `unexpectedSelections`: an actual selection when gold expected judgment.
  Gold never suppresses that route, even if Jev accepted the wrong domain.
- `floorMismatches`: selected floor-ID set differs from gold; independent of
  the other counts. Raw difficulty and applied floors show policy corrections.
- `selected`: all confirmed selections, including domain mismatches/unexpected
  selections. `unselected` counts by outcome: `retry-with-context`,
  `manager-judgment`, `route-refused`, `incomplete` or `not-run`.

Each row also has `domainMismatch`, `unexpectedSelection`, `floorMismatch` and
`tierComparison` (`same`, `cheap`, `expensive`, or null). Null means unobserved
or ineligible for comparison. Uncertainty, missing routes, availability/catalog
refusals and wrong domains never count as expensive or cheap routes. Route
refusals retain selector exit 4/5, with bounded reasons distinguishing catalog
expiry/invalidity, changed policy and other unavailability/judgment.

Exit **0 means run completion**, including uncertainty, refusals and mismatches;
it says nothing about routing accuracy or a live pass. Exit **2** means invalid
arguments/examples/catalog or interrupted input/storage/setup; **3** means
invalid config/quota/credential setup. Preflight refusal prints only a fixed
stderr diagnostic. After run creation, interruption emits a partial JSON report
with `complete: false`, failure stage and unattempted rows, then stops. Durable
evidence is preserved; available event references are recovered without retries.
An interrupted write can leave a durable selection reference while its row
remains incomplete and excluded from selected/tier counts until reconciliation.
If storage is unreadable, use the reported work ID to reconcile before rerunning.

Real task inputs may join router pilots only after the formal helper-backed
pilot lifts the hold, recorded with each decision. This runner stays synthetic
only. The Engineering manager reports L8 observations to Shawn; this Task tunes
no thresholds, criteria or floor values and claims no live L8 pass.
