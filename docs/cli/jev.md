# Jev classification

Use the [common invocation](../cli.md) with global options before `route`:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B <root>/bin/t3c --local-dir <local> route classify \
  --repo synthetic/example --work TASK-1 --input TASK.json
```

`--repo`, `--work` and `--input` are required. `--context EXTRA.json` is
optional and only valid for an eligible retry. `--decline-retry` is an optional
flag to skip retry offers or decline an eligible context retry in favor of fixed
defaults; using `--context` and `--decline-retry` together is invalid (exit 2).
`--repo` uses the records contract: normalized identity, HTTPS/SSH origin, or
existing checkout path; use `./` or an absolute path for an ambiguous relative
checkout. Origin reads are local. The work must already exist; its current
`effectiveWork.writer` owns new events. This command does not create work or
select a route.

TASK.json has exactly `intent` (text), `acceptance` (nonempty list of text or
`{id,text}` items), `interfaces` (list of text), `scope` (text or nonempty
list of text), `riskFacts` (configured fact IDs) and `dependencies` (list of
text). Unknown keys fail. Each file is read at most 16 KiB plus one byte;
the canonical `{task,context}` must fit 16 KiB, including retry metadata.
Malformed JSON, duplicate keys, nonfinite numbers and invalid UTF-8 fail.

| Exit | Result |
| --- | --- |
| 0 | Accepted or defaulted classification, or the completion of an incomplete decision: full classification data plus `classificationRef`, and `judgment` with `judgmentRef` whenever a judgment is written. |
| 4 | `outcome: retry-with-context` or refused attempt (bare re-run, ineligible context/decline, or a context re-run of an incomplete decision) with a public `reason`. Always includes `inputRef`, `inputDigest`, `policyDigest`. |
| 2 | Invalid input, identifier screen, history or record write, or a work that another classification holds; empty stdout, safe stderr. |
| 3 | Invalid config or credential setup; empty stdout, safe stderr. |

Config-derived classification metadata is validated against the record schema
before input persistence or HTTP. A mismatch exits 3 with no new events or call;
the recorder still supplies the actual input reference and digests.

Classification data follows [records](../records.md): input/policy provenance,
`configVersion: 3`, rule version, flattened lane/criticality/scope criteria with
instructions and their canonical SHA-256, risk facts, configured alias, response
snapshot model/ID, all three answers, outcome and reason. HTTP/parse failures
retain unknown response fields with fixed public reasons. No raw response, headers
or backend exception is persisted. A failed record append never returns an
accepted result.

## Three questions and per-answer acceptance

Jev is asked the three version-3 routing questions from the configuration's
`classification` section in a single request:
1. `lane`: `BACKEND_LOGIC`, `UI_FRONTEND`, or `MIXED`, and `DOCS` when the configuration's
   `classification.lane.criteria` lists it (the key is optional).
2. `criticality`: `CORE` or `NON_CRITICAL`.
3. `scope`: `STANDARD`, `COMPLEX`, or `INSUFFICIENT_CONTEXT`.

Jev is offered exactly the criteria the configuration lists, and the record keeps
them. An answer must use them: a `choice` outside the listed criteria, or
`probabilities` whose keys are not exactly the listed criteria, is an invalid Jev
response and takes the Jev-failure path below. A configuration without `DOCS`
therefore never records a `DOCS` answer.

Each answer is accepted on its own when its `confidence` is at least
`jev.minConfidence` and its `choice` is not `INSUFFICIENT_CONTEXT`. Only `scope`
can be `INSUFFICIENT_CONTEXT`; a confident `MIXED` or `DOCS` lane is accepted. An answer is
uncertain when its confidence is below `minConfidence` or its choice is
`INSUFFICIENT_CONTEXT`.

Fixed defaults apply per question when uncertain answers are resolved:
- `lane`: `BACKEND_LOGIC`
- `criticality`: `CORE`
- `scope`: `COMPLEX`

When defaults are applied, confident answers are kept. The classification answers
always retain Jev's original choices (a confident `MIXED` lane stays `MIXED`).
A judgment maps a confident `MIXED` lane to `BACKEND_LOGIC`. A confident `DOCS` lane is
kept as `DOCS`, and an uncertain lane still takes `BACKEND_LOGIC`.

## Judgment records

Version-3 `routing-judgment` records reference the same input
(`inputRef`, `inputDigest`, `policyDigest`) and are written in exactly these cases:
1. **Per-answer defaults applied** (after a used retry, a declined retry, or no-offer):
   - Basis: `default-insufficient-context` when the scope answer defaulted with choice `INSUFFICIENT_CONTEXT`, else `default-low-confidence`.
   - `defaulted`: list of defaulted question names in `lane`, `criticality`, `scope` order.
   - Values: kept confident answers and fixed defaults (confident `MIXED` lane mapped to `BACKEND_LOGIC` and not listed in `defaulted`; a confident `DOCS` lane kept as `DOCS`).
   - Reason: fixed public text naming what defaulted (and noting a confident `MIXED` mapping when lane was confident `MIXED`).
2. **Jev failure** (transport fault, HTTP error, redirect, or malformed response):
   - Basis: `default-jev-failure`.
   - `defaulted`: `["lane", "criticality", "scope"]`.
   - Values: all three at their fixed defaults (`BACKEND_LOGIC`, `CORE`, `COMPLEX`).
   - Reason: public failure reason (`Jev request failed` or `invalid Jev response`) naming fixed defaults applied.
3. **Confident `MIXED` lane with no defaulted answers**:
   - Classification outcome is `accepted` and retains `MIXED`.
   - Judgment basis: `manual` (the only schema-valid basis for an empty defaulted list).
   - `defaulted`: `[]`.
   - Values: `lane: "BACKEND_LOGIC"`, kept confident criticality and scope.
   - Reason: `"confident MIXED lane mapped to BACKEND_LOGIC"`.

No judgment is written for a plain accepted classification (no `MIXED`, no defaults; a
confident `DOCS` lane is plain):
the classification record is the decision.

## Context and retry budget

A first request has no context. When any answer is uncertain on a first request
(and `maxContextRetries` >= 1 and `--decline-retry` was not passed), the outcome is
`retry-with-context` (exit 4), offering both next options:
- Supply a nonempty JSON object containing its positive `classificationRef` as
  `priorClassificationRef`, plus relevant additional information with `--context EXTRA.json`:

```json
{"priorClassificationRef":2,"details":"The parser is pure and accepts signed decimal integers only."}
```

- Or rerun with `--decline-retry` to apply fixed defaults immediately without a second request.

The reference in `--context` must identify the latest classification for the identical
canonical task and policy. Extra information must contain a nonempty value
beyond the reference; the helper cannot judge its semantic relevance.
With `maxContextRetries: 1`, only one such retry is allowed. With zero, no
retry is offered and defaults apply at once. Every persisted matching input
consumes an attempt, even when its classification is missing; incomplete history
requires reconciliation. A classification whose judgment append failed (exit 2) is
different: its decision is incomplete, and a re-run without `--context` writes the
missing judgment from the stored classification, makes no request and exits 0.
Until then a reader must treat a `defaulted` classification, and an `accepted` one
with a `MIXED` lane, as having no decision. Changing only context, omitting it, or
pointing to an older result cannot restart the budget. A context without matching
history is invalid (exit 2).

### Declining the retry

The `--decline-retry` CLI flag controls declining the retry:
- On a fresh first request (no matching input): proceeds with the request; if any answer is uncertain, skips the retry offer and applies defaults immediately (classification outcome `defaulted` + judgment, exit 0).
- On a re-run with a matching input whose latest classification outcome is `retry-with-context` and no judgment yet references that input: does not POST; writes only a `routing-judgment` referencing that classification's input with per-answer defaults, returning the prior classification data with `outcome: "defaulted"` and `"judgment"`, exit 0.
- Decline with no matching input behaves as a fresh call. Decline when the latest matching classification is `accepted` or `defaulted`, or a judgment already references the input, is refused (exit 4, `"identical input already classified/decided; reuse its evidence"`), unless the decision is incomplete (below).
- A bare re-run (no `--context`, no `--decline-retry`) with a matching input is refused (exit 4), naming both options when outcome is `retry-with-context` or saying to reuse evidence when `accepted` or `defaulted`, unless the decision is incomplete (below).
- **Incomplete decision.** A `defaulted` classification, and an `accepted` one with a `MIXED` lane, are followed by their judgment in the same invocation. If that append failed (exit 2), a bare or `--decline-retry` re-run writes the missing judgment from the stored classification, makes no request and exits 0, returning the classification data and the judgment. A `--context` re-run is refused (exit 4, `"decision incomplete; rerun without --context to write its judgment"`).
- `--decline-retry` with `maxContextRetries: 0` is harmless (defaults apply at once).

## Request, validation and privacy

The adapter makes one POST to `https://openrouter.ai/api/alpha/decisions`.
Its per-request stdlib opener verifies TLS and refuses redirects. It uses the
configured timeout, reads at most 256 KiB plus one byte and closes responses.
It never automatically retries. Timeout, non-2xx, redirect, oversized or
malformed replies produce a recorded defaulted classification and a
`default-jev-failure` judgment, exiting 0.

The body is `{model,state,questions}`; state is the original `{task,context}`.
Each question has `type: choice` and the configured instructions/criteria. The
body is canonical JSON, so its members, including the three question names, go
out in sorted order; their order carries no meaning.
Invocations for one work are serialized: the command holds an exclusive advisory
lock on the work's directory from the history read to the last append. A second
invocation waits up to the request timeout plus 10 seconds, then exits 2 (`another
classification holds this work; retry later`). Other commands do not take the lock.
Answers require all three question names, exact choice-answer fields and option
keys, a configured choice, non-boolean finite probabilities/confidence in
`[0,1]`, and probabilities summing within 0.01 of 1. Snapshot model and ID
must be 1–256 ASCII identifier characters (letters/digits first, then letters,
digits or `._:/~+-`). Unrelated service fields are never copied into records.

`OPENROUTER_API_KEY` takes precedence when present; an empty/invalid value
fails rather than falling back. Otherwise the adapter alone reads the configured
key-file JSON field (regular file, 16 KiB cap). Keys must be 1–4096 ASCII letters,
digits or `._~+/-`; only the Authorization header receives the key. Credential
bytes or the configured key-file path in request/record text or returned service
data block persistence of that data. Exception diagnostics are fixed public text. The identifier loader/matcher
screens wire JSON and decoded request text before input persistence; a missing,
invalid or matching list blocks the request. Matches report only list line numbers.

The config is loaded once per invocation via `load_matrix_config(local=local)`
(a version-2 config exits 3). The input is persisted before HTTP,
then the classification references it and the policy actually used. A policy
edit during HTTP makes that record stale for route selection, which must check
the current policy; no cached config or new digest substitutes for the old one.

## EM-only L2b check

After review and merge, the Engineering manager runs exactly:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B <root>/live/jev_contract.py --local-dir <local>
```

`--local-dir <local>` points to a private copy holding a version-3 configuration
and state; the default is the helper root's `.local`. Prerequisites are HS-1's
stable Python/Git/grep plus HTTPS to OpenRouter and the existing configured
credential, only for L2b.
The script creates a unique labeled work under `synthetic/jev-contract`, uses
the same adapter for one synthetic call, and never retries or selects models.
It prints `LIVE synthetic`, the work ID and safe result with snapshot/ID/answers;
the records retain that evidence. Exits follow the table above. Each deliberate
run creates a new work without overwriting prior evidence. Offline tests run
the actual script with injected HTTP and a fake key; they are not L2b passes.
