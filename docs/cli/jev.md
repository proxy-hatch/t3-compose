# Jev classification

Use the [common invocation](../cli.md) with global options before `route`:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B <root>/bin/t3c --local-dir <local> route classify \
  --repo synthetic/example --work TASK-1 --input TASK.json
```

`--repo`, `--work` and `--input` are required. `--context EXTRA.json` is
optional and only valid for an eligible retry. `--repo` uses the records
contract: normalized identity, HTTPS/SSH origin, or existing checkout path;
use `./` or an absolute path for an ambiguous relative checkout. Origin reads
are local. The work must already exist; its current `effectiveWork.writer`
owns both new events. This command does not create work or select a route.

TASK.json has exactly `intent` (text), `acceptance` (nonempty list of text or
`{id,text}` items), `interfaces` (list of text), `scope` (text or nonempty
list of text), `riskFacts` (configured fact IDs) and `dependencies` (list of
text). Unknown keys fail. Each file is read at most 16 KiB plus one byte;
the canonical `{task,context}` must fit 16 KiB, including retry metadata.
Malformed JSON, duplicate keys, nonfinite numbers and invalid UTF-8 fail.

| Exit | Result |
| --- | --- |
| 0 | Accepted classification: full classification data plus `classificationRef`. |
| 4 | Same fields with `outcome: retry-with-context` or `manager-judgment` and a public `reason`. Always includes `inputRef`, `inputDigest`, `policyDigest`. |
| 2 | Invalid input, identifier screen, history or record write; empty stdout, safe stderr. |
| 3 | Invalid config or credential setup; empty stdout, safe stderr. |

Config-derived classification metadata is validated against the record schema
before input persistence or HTTP. A mismatch exits 3 with no new events or call;
the recorder still supplies the actual input reference and digests.

Classification data follows [records](../records.md): input/policy provenance,
rule version, flattened domain/difficulty criteria with instructions and their
canonical SHA-256, risk facts, configured alias, response snapshot model/ID,
both answers, outcome and reason. HTTP/parse failures retain unknown response
fields with fixed public reasons. No raw response, headers or backend exception
is persisted. A failed record append never returns an accepted result.

## Context and retry budget

A first request has no context. When its outcome is `retry-with-context`,
supply a nonempty JSON object containing its positive `classificationRef` as
`priorClassificationRef`, plus relevant additional information, for example:

```json
{"priorClassificationRef":2,"details":"The parser is pure and accepts signed decimal integers only."}
```

The reference must identify the latest classification for the identical
canonical task and policy. Extra information must contain a nonempty value
beyond the reference; the helper cannot judge its semantic relevance.
With `maxContextRetries: 1`, only one such retry is allowed. With zero, no
retry is offered. Every persisted matching input consumes an attempt, even
when its classification is missing; incomplete history requires reconciliation.
Changing only context, omitting it, or pointing to an older result cannot
restart the budget. A context without matching history is invalid (exit 2).
Other ineligible attempts return exit 4 before HTTP or a new append, referencing
the latest classification but overriding the returned outcome/reason to explain
the refusal; the historical event remains unchanged. This also refuses repeated
identical accepted inputs: reuse their evidence. Changed task requirements or
policy allow a fresh context-free request. A new snapshot behind the same alias
does not reset the budget or change the policy digest.

## Request, validation and privacy

The adapter makes one POST to `https://openrouter.ai/api/alpha/decisions`.
Its per-request stdlib opener verifies TLS and refuses redirects. It uses the
configured timeout, reads at most 256 KiB plus one byte and closes responses.
It never automatically retries. Timeout, non-2xx, redirect, oversized or
malformed replies produce a recorded manager-judgment result.

The body is `{model,state,questions}`; state is the original `{task,context}`.
Each question has `type: choice` and the configured instructions/criteria.
Answers require both question names, exact choice-answer fields and option
keys, a configured choice, non-boolean finite probabilities/confidence in
`[0,1]`, and probabilities summing within 0.01 of 1. Snapshot model and ID
must be 1–256 ASCII identifier characters (letters/digits first, then letters,
digits or `._:/~+-`). Unrelated service fields are never copied into records.
Mixed/unclear domains go directly to judgment. Insufficient context or either
confidence below `minConfidence` offers the bounded retry, then judgment.
Acceptance compares confidence, independently of the chosen probability.

`OPENROUTER_API_KEY` takes precedence when present; an empty/invalid value
fails rather than falling back. Otherwise the adapter alone reads the configured
key-file JSON field (regular file, 16 KiB cap). Keys must be 1–4096 ASCII letters,
digits or `._~+/-`; only the Authorization header receives the key. Credential
bytes or the configured key-file path in request/record text or returned service
data block persistence of that data. Exception diagnostics are fixed public text. The identifier loader/matcher
screens wire JSON and decoded request text before input persistence; a missing,
invalid or matching list blocks the request. Matches report only list line numbers.

The config is loaded once per invocation. The input is persisted before HTTP,
then the classification references it and the policy actually used. A policy
edit during HTTP makes that record stale for RT-1 selection, which must check
the current policy; no cached config or new digest substitutes for the old one.

## EM-only L2 check

After review and merge, the Engineering manager runs exactly:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B <root>/live/jev_contract.py
```

Optional `--local-dir <local>` selects explicit config/state; the default is the
helper root's `.local`. Prerequisites are HS-1's stable Python/Git/grep plus
HTTPS to OpenRouter and the existing configured credential, only for L2.
The script creates a unique labeled work under `synthetic/jev-contract`, uses
the same adapter for one synthetic call, and never retries or selects models.
It prints `LIVE synthetic`, the work ID and safe result with snapshot/ID/answers;
the records retain that evidence. Exits follow the table above. Each deliberate
run creates a new work without overwriting prior evidence. Offline tests run
the actual script with injected HTTP and a fake key; they are not L2 passes.
