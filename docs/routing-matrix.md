# Model routing matrix

Status: decided by the owner on 2026-11-01. His source file stays untracked in
the owner's checkout; the binding private record of the decision and the
accepted clarifications are recorded in `.local/`. This file is the public
contract: model names only, with the exact selection identifiers held in the
owner's private config, also recorded in `.local/`. The design's
[Routing and personal configuration](superpowers/specs/2026-10-27-t3-compose-design.md#routing-and-personal-configuration)
section and [plan decision 20](superpowers/plans/2026-10-27-t3-compose-implementation.md#decisions)
reference it, and Tasks MX-1 to MX-4 implement it in `t3c`.

## Global rules

The owner's global rules, 2026-11-01:

1. Run quotas to 100% (reactive failover only): never estimate or pre-check
   quota percentages. Always dispatch the cell's primary. Switch to the quota
   fallback only when the provider returns a usage-limit, rate-limit or
   quota-exhausted error.
2. Gemini models have small quotas: Gemini 3.8 Flash and Gemini 4 Argon have
   small 5-hour and weekly quotas and will exhaust frequently. When one hits
   a usage limit, fail over at once to the next target in the cell.
3. Task escalation versus quota failover: quota failover is horizontal — the
   same task at the same difficulty, the next available target. Capability
   escalation is vertical, triggered only when a Worker or Reviewer fails two
   attempts on the same issue or a review deadlocks.

Accepted clarifications: quota failover walks the cell's ordered
quota-fallback list once. Vertical escalation is per issue. After two failed
repairs, the next-stage Worker continues the work. After the same finding
remains unresolved for two Corrections rounds, the next Corrections round uses
the next stage. When a Worker disputes a finding and the reviewer raises it
again, a next-stage reviewer decides. After two non-quota reviewer failures,
the next-stage reviewer takes over. When a second issue needs stage 3, or merge
or post-merge verification fails twice, the manager replaces the lead at the
next stage; the replacement resumes from the Task log and records. When stage 3
fails, the item goes to the manager, then the owner. Environment blocks, quota
refusals and spec questions are not escalations. Every escalation is recorded.

## The matrix

The owner's table of 2026-11-01 with model names only, and with the
placements changed for the [GLM budget](#glm-budget) and the
[Terra placements](#terra-placements):

| Role / lane | Stage 1 | Stage 1 quota fallback | Stage 2 | Stage 2 quota fallback | Stage 3 | Stage 3 quota fallback |
|---|---|---|---|---|---|---|
| Feature lead | Claude Sonnet 5.5 (high) | GPT-6.1 Sol (high) → Gemini 4 Argon (high) | Claude Opus 5.5 (medium) | GPT-6 Astra (high) | Claude Opus 5.5 (xhigh) | none |
| Worker, backend-logic | Gemini 3.8 Flash (medium) | GLM-5.3-Flash (low) → GPT-6.1 Sol (high) | GPT-6.1 Sol (high) | Claude Sonnet 5.5 (high) → GLM 5.3 (high) | Claude Sonnet 5.5 (xhigh) | Claude Opus 5.5 (high) |
| Worker, ui-frontend | Gemini 3.8 Flash (medium) | GLM-5.3-Flash (low) → Claude Sonnet 5.5 (high) | Claude Sonnet 5.5 (high) | GPT-6.1 Sol (high) → GLM 5.3 (high) | Claude Sonnet 5.5 (xhigh) | Claude Opus 5.5 (high) |
| Worker, docs (optional) | GPT-6.1 Terra (medium) | Claude Sonnet 5.5 (high) → GLM-5.3-Flash (low) | GPT-6.1 Terra (xhigh) | Claude Sonnet 5.5 (xhigh) | Claude Opus 5.5 (high) | none |
| Reviewer, non-critical | Gemini 4 Argon (high) | GPT-6.1 Sol (high) → GLM 5.3 (high) | GPT-6.1 Sol (high) | Claude Sonnet 5.5 (high) → GLM 5.3 (high) | Claude Opus 5.5 (high) | none |
| Reviewer, core | pre-pass Gemini 3.8 Flash (high), then GPT-6.1 Sol (high) | Claude Sonnet 5.5 (high) → Gemini 4 Argon (high) → GLM 5.3 (high) | Claude Opus 5.5 (high) | GPT-6 Astra (high) | GPT-6 Astra (high) | none |

The backend Worker's stage-3 "Claude Sonnet 5.5 (xhigh)" primary has one quota
fallback: Claude Opus 5.5 (high).

Starting stage: standard scope starts at stage 1; complex scope starts at
stage 2. It applies to the lead, the Workers and the Reviewers, and every new
round (first, Corrections, re-review) starts at the Task's starting stage.

Helpers have no lane and no stage. Each walks its primary and then its quota
fallback, like a matrix cell, under every quota rule below:

- the verifier, for the manager's GUI/OS end-to-end gate: GPT-6.1 Terra (high),
  then GPT-6 Astra (high);
- the text helper: GPT-6.1 Terra (medium), then Gemini 3.8 Flash (high).

The manager row: the Engineering manager's primary is Claude Opus 5.5 (medium)
for project-management work; its quota fallback is GPT-6 Astra, medium for
project management and high for the final GUI/OS verification gate. The owner
starts manager threads. `t3c` records the manager and never selects it, and a
manager selection never resolves to the Pi harness. When Opus refuses, the
owner switches the manager thread to Astra with the harness's model control,
which keeps the conversation and still delivers child completions; a new
manager thread on Astra, resuming from the index, the global log and the
records, is the alternative.

## GLM budget

Decision 20's budget amendment, 2026-11-01. The owner said GLM credits were
burning too fast and set a target of about $20 a month for the metered key.
The Engineering manager moved GLM out of the primary, lead, pre-pass and
helper positions; the owner may adjust any of these placements. The table
above holds the result.

- GLM is a last-resort quota fallback only: never a primary, a lead, a
  pre-pass or a helper.
- Its thinking is low (GLM-5.3-Flash) or high (GLM 5.3), never max.
- Every cell's primary is a subscription model, so GLM is never first. In the
  two stage-1 Worker cells the primary is the small-quota agy pool, so a
  subscription target follows GLM there: Sol (high) in the backend cell and
  Sonnet (high) in the ui-frontend cell. Where GLM is the last fallback, a GLM
  refusal, for example at its credit cap, leaves the cell dry: the command
  exits 5 and the work waits for the earliest remembered refusal to expire.
- The core pre-pass is Gemini 3.8 Flash (high) from the agy pool. It is
  skipped if no pool account can serve.
- The search helper is not used: leads search the repository themselves. The
  schema keeps its entry until MX-3b removes it.
- Cells changed from the owner's table: the Feature lead's stage 1; the
  Workers' stages 1 and 2; the non-critical Reviewer's stages 1 and 2; and the
  core Reviewer's stage 1. The ui-frontend Worker's stage 3 also changes, to
  Claude Sonnet 5.5 (xhigh) with Claude Opus 5.5 (high) as its quota fallback,
  so that escalating from stage 2 raises capability, as it does for the backend
  Worker. The other stage 3 cells, the core Reviewer's stage 2 and the lead's
  stages 2 and 3 are unchanged.
- Metered use: GLM runs through the Pi harness on a per-token key. The config
  may place a Pi target in any matrix or helper position except the manager.
  Every metered use records `billing: per-token`, and a lead lists its GLM uses
  in its merge line.
- A credit cap on the GLM key, recommended to the owner, is the hard
  guarantee. At the cap the provider answers 402, a credits error, which is a
  quota refusal. Placement only lowers the rate.

## Terra placements

Owner-approved, 2026-11-02, after the vendor said that GPT-6.1 Terra is
probably better than Astra and Sol at technical communication, and similar on
vision and computer use. Terra is first only where the output is judged mainly
as communication, or by vision and computer use:

- GUI and OS verification at the final gate (vision and computer use): the
  verifier drives the built app and judges what is on screen.
- Bounded text generation (technical communication): summaries, PR text,
  release notes. This is the text helper.
- Tasks whose deliverable is mainly technical writing (technical
  communication): design docs, specs, the CLI reference, guides, templates.
  This is the docs lane.

Unchanged: code Workers (backend and UI), reviews of record, leads and the
pre-pass. Docs reviews stay correctness-first; a Terra docs pre-pass is a
possible later experiment.

| Cell | Primary | Quota fallback |
|---|---|---|
| `helpers.verifier` | GPT-6.1 Terra (high) | GPT-6 Astra (high) |
| `helpers.text` | GPT-6.1 Terra (medium) | Gemini 3.8 Flash (high) |
| `worker` / `docs`, stage 1 | GPT-6.1 Terra (medium) | Claude Sonnet 5.5 (high) → GLM-5.3-Flash (low) |
| `worker` / `docs`, stage 2 | GPT-6.1 Terra (xhigh) | Claude Sonnet 5.5 (xhigh) |
| `worker` / `docs`, stage 3 | Claude Opus 5.5 (high) | none |

Caveats, accepted: the claim is the vendor's own and hedged; Terra shares
Codex's usage limit with Sol and Astra, so every fallback after Terra crosses
provider; and the outcome is measured on the next docs Tasks, by Worker rounds
and review findings.

### The docs lane

- Jev's lane question gains `DOCS`: "the deliverable is mainly technical
  writing (docs, specs, guides, templates, messages) with little or no code".
  It is offered only when the configuration's `classification.lane.criteria`
  lists `DOCS`. A confident `MIXED` answer (code and docs) stays backend-logic,
  and so does an uncertain lane. A lead may record the lane itself with
  `route judge --lane DOCS`.
- Only Workers use the lane: a `DOCS` decision selects the `worker` / `docs`
  cell at the Task's stage. The lead, the Reviewers and the pre-pass are routed
  by criticality as before.
- The `docs` lane and the `DOCS` criterion are optional in the configuration.
  When the lane is absent, a `DOCS` decision routes to the backend-logic cell
  and the selection records `laneFallback: "docs-to-backend-logic"`, so a
  configuration without the lane keeps working. An escalation chain runs in the
  lane that was used.
- Helpers walk their quota fallbacks like any cell. A remembered refusal of the
  primary selects the first fallback, and a helper whose targets have all
  refused is a dry cell: the command exits 5.

## Classification

Jev answers three closed-choice questions from the Task's context:
lane (`BACKEND_LOGIC`, `UI_FRONTEND`, `MIXED`, and `DOCS` when the
configuration lists it; a `MIXED` answer maps to backend-logic and a `DOCS`
answer to the docs lane), criticality (`CORE`: logic, stateful code, an API,
security or a shared contract; `NON_CRITICAL`: UI, CRUD, tests only or wording)
and scope (`STANDARD`, `COMPLEX`, `INSUFFICIENT_CONTEXT`). Each required answer's
confidence must reach the minimum confidence, 0.6. An uncertain answer allows
one retry with more context. If an answer is still uncertain, the stronger
option applies to it alone: only an uncertain answer takes its fixed default
(scope `COMPLEX`, criticality `CORE`, lane `BACKEND_LOGIC`); a confident answer
is kept. The judgment records which answers took their default. A Jev API error
or a malformed answer applies the same defaults, recorded with the error. A
lead may instead record its own judgment, with a reason, in either direction.

Risk floors are deterministic and applied after Jev: credentials-or-guardrail
and recovery-or-concurrency make the scope `COMPLEX` and the criticality
`CORE`; shared-interface makes the criticality `CORE`; document-change has no
effect. A floor only raises.

Work outside software engineering is not routed by `t3c`; the manager handles
it by hand.

## Quota refusals

Classify a failed child by its own response, using the harness's label only
when the child gave none.

- Quota refusal: a usage limit, an exhausted quota, a credits error (402), or
  a rate limit whose retry time is over 5 minutes or is missing. Record the
  refusal and go to the next target at once.
- Transient error: a rate limit with a retry time of 5 minutes or less, "at
  capacity", or a 5xx error. Retry the same target once after the stated wait
  (1 minute when none is stated), then treat it as unavailable for this
  dispatch only. A transient error is never recorded as quota.
- Environment block: the target is unavailable for this dispatch. It is not
  quota.
- A failure inside the work is not a failover; it follows the escalation
  protocol.

Refusal memory lasts 30 minutes, keyed by provider instance and model, or by
pool account and model. Every dispatcher skips a remembered key until 30
minutes after the refusal was observed, then tries it again. A reported reset
time is recorded but never used for skipping, because it can be wrong.

The pool walk: start at the active account and go in pool order, wrapping
around once. Skip an account that does not offer the model, or that has a live
refusal for it. The active account's refusal moves the active pointer to the
next account. Walking accounts is not a fallback.

When no target in a cell can serve, `t3c route select` exits 5 with the
earliest time a remembered refusal expires; the dispatcher waits and tries
again. A dry cell never escalates to another stage. At stage 3 the item waits,
and the manager reports to the owner.

Role blocks: a driver kind that failed a capability probe for a role is
unavailable for that role, with the reason and evidence recorded. The Feature
lead's Argon fallback applies only while no role block covers it.

## Worker failure and escalation protocol

The owner's Worker prompt, revised 2026-11-01. The budget is the first
implementation plus 2 repair attempts against the brief's validation
commands. When the second repair attempt fails, do not try a third: commit
the work in progress on the branch with the message
`wip(escalation): <short summary>`, never leaving it uncommitted, and return
only this block:

```text
<ESCALATION_REQUEST>
- Status: ESCALATE_TO_NEXT_STAGE
- Model and options: <as assigned in the brief>
- Branch: <branch>
- WIP commit: <full SHA>
- Review base: <full SHA>
- Modified files: <paths>
- What was attempted (at most 150 words):
  1. Attempt 1: <approach, and why it failed>
  2. Attempt 2: <approach, and why it failed>
- Failing command and output: <command>, then the last 15 to 30 lines
- Suspected root cause: <one or two sentences>
</ESCALATION_REQUEST>
```

An environment block is not a repair attempt: report it as blocked, with the
exact error. A spec question is not an attempt either: report it as a question.

## Lead protocol

The owner's lead prompt, revised 2026-11-01.

On a quota refusal:

1. Record the refusal with `t3c quota record`.
2. Clear the refused dispatch with a stopped-and-preserved `reconciliation`.
3. Dispatch the cell's next target with the same brief and a new
   `clientRequestId`. Start at the round's previous start commit, or at the
   latest completed result's commit.
4. The refused child's commits, if any, stay on its branch. The new brief
   may name that branch for read-only reference.

On an `<ESCALATION_REQUEST>`:

1. Dispatch a new `delegate_task` on the next stage's primary.
2. The brief is the original brief, plus the escalation block, plus a start
   commit equal to the WIP commit. That is the existing move rule: T3 cannot
   reopen another child's worktree.
3. Instruct the new Worker: "Inspect `git diff <review base>..HEAD`,
   evaluate the previous Worker's attempts in the escalation block, either
   fix the root cause or revert the broken changes, and get the validation
   passing."
4. Never pass the failed Worker's transcript.

Record every escalation: `t3c route escalate` once MX-3b ships, and the log
until then.

## Core review and the final head

At stage 1, a core review runs in this order:

1. The pre-pass (Gemini 3.8 Flash (high) from the agy pool) reviews the head.
2. If it reports blocking findings, a Corrections round runs at the Task's
   starting stage.
3. The review of record reviews the corrected head. It never sees the
   pre-pass findings.

The pre-pass has no fallback. If it is refused or unavailable, for example
when no pool account can serve, it is skipped. The reason goes in the selection
command's output (and, for a quota refusal, in the quota state), and no
selection record is written, because a selection always names its chosen
target. The pre-pass is never the review of record.

The final head: on a core Task, the review of record, at the Task's reviewer
stage, reviews the final head independently, and the lead checks the result
and merges. On a non-critical Task, the lead's own final-head review stands,
as decided on 2026-10-28.
