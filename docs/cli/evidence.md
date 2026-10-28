# Evidence comparison

`t3c evidence check --repo REPO --work ID --head SHA [--pr-ready]` compares
recorded coordinator assertions for one Task. Use the interpreter invocation
and global options in [the CLI contract](../cli.md). Global `--local-dir PATH`
and `--text` precede `evidence`. The handler uses only `args.local_dir`.
`REPO` has [RC-1's meaning](records.md): normalized identity, origin URL or
checkout path, not `pr verify`'s `OWNER/NAME`. Git/path handling belongs to RC-1.
`SHA` is exactly 40 lowercase hexadecimal characters, as in the record schema.

## API, output and exits

`t3compose.evidence.check(local_dir, repo, work_id, head, *, pr_ready=False)`
returns the following dictionary; `pr_ready` must be a Python boolean. It loads
freshly through `records.load_work` on every call. Invalid arguments or records
raise sanitized `cli.CommandError` (exit 2). PB-1 calls this API with
`pr_ready=False` before publishing and proceeds only when `verified` is true.
There is no success cache, verified event, second store or external query.
Import and registration perform no state or process I/O.

The exact JSON shape, on both exit 0 and exit 1, is:

```json
{"verified":true,"head":"1111111111111111111111111111111111111111","prReady":false,"gaps":[]}
```

`verified` means all requested comparisons passed; `prReady` echoes the requested
mode, not a second readiness verdict. `head` echoes only the validated SHA.
Each gap has exactly `code` (below), `id` (check/criterion/finding ID or null),
`reviewRef` (review event sequence for findings, otherwise null), and `eventRef`
(the selected event sequence or null when absent). No evidence contents, native
IDs, thread names, operation targets or exception values are returned.

Exit 0 has `verified: true` and no gaps. Exit 1 has `verified: false` and one or
more gaps, returned through `cli.Outcome(data, cli.ExitCode.CHECK_FAILED)`.
Both produce one JSON value and empty stderr. Exit 2 has empty stdout and a
sanitized stderr diagnostic for invalid SHA/options, missing work, malformed
records, invalid schema, provenance/history failure or local I/O failure.
Global `--text` retains the exit code and renders this same dictionary in
insertion order: `verified`, `head`, `prReady`, `gaps`, one `key: <JSON-value>`
line per field, using the common CLI escaping rules. Handlers never print/exit.

Gaps are ordered by implementation head, required checks in effective list
order, latest review, findings in review-event and finding-list order,
criteria in effective list order, then push and pull request. One gap per
item is emitted, using the first applicable reason in each row's order:

| Item | Ordered gap codes | ID / references |
|---|---|---|
| Implementation | `head-missing`, `head-stale` | null ID; latest Worker result or null |
| Required check | `check-missing`, `check-stale`, `check-command`, `check-source`, `check-outcome`, `check-evidence` | check ID; selected check or null |
| Final review | `review-missing`, `review-stale`, `review-source`, `review-verdict` | null ID; latest review or null |
| Finding | `finding-missing`, `finding-unresolved` | finding ID, originating reviewRef; latest disposition or null |
| Criterion | `criterion-missing`, `criterion-stale`, `criterion-source`, `criterion-evidence` | criterion ID; selected evidence or null |
| Push | `push-missing`, `push-unresolved`, `push-stale`, `push-outcome`, `push-status`, `push-confirmation` | null ID; intent when unresolved, otherwise latest result |
| Pull request | `pr-missing`, `pr-unresolved`, `pr-stale`, `pr-outcome`, `pr-status`, `pr-confirmation` | same as push |

## Head, requirements and sources

The current implementation head is the last `result` joined through native
confirmation to a **Worker** intent, not the last result of any role. RC-1
requires completed native status and matching worktree provenance. A late
Reviewer completion cannot move this head backwards. The original review base
alone, even with an approved base review, has `head-missing` until an explicit
completed Worker result exists. An explicit no-change Worker result may name
the base. No Git ancestry or artifact existence check is added here.

Requirements come from `effectiveWork`, including scope replacements. For each
check/criterion ID, select the latest applicable event at the checked head from
a permitted source. A check's exact command value (string/list and all tokens)
must also match the effective command. Later untrusted or other-head assertions,
or checks of another command, cannot erase applicable evidence. Within that
context, the latest outcome/evidence wins: a failure or unknown evidence cannot
hide behind an earlier pass. When no applicable event exists, the latest fresh
event supplies the stale/command/source diagnostic; no fresh event means missing.
A scope change invalidates prior evidence for changed or reintroduced definitions;
unchanged definitions retain their evidence.

Check outcome must be `pass`; `fail`, `refused`, `blocked` cannot count. Invalid
outcome labels are schema errors, exit 2. Checks need a nonempty evidence path;
criteria need concrete evidence at exactly the requested head. Trusted sources
are `manager`, `feature-lead`, `reviewer`, `assigned-session`; `worker` never
counts. Worker `workerClaims`/`verifiedFacts` and result commits supply no
check or criterion evidence. For `feature-lead` sources, associate the event
with the lead thread in force at its sequence, and require that thread to be
current now. No assignment or a different current thread cannot supply coverage;
reaffirming the same thread does not invalidate its evidence. This binds the
source label without inventing a separate check thread field. Other labels are
coordinator assertions; EV-1 adds no external identity authentication.

## Reviews and findings

Only the latest review event is considered for final-head clearance. It must
cover exactly immutable `work.reviewBase` and the requested head, with verdict
`approve` or `approve-after-fixes`. A later stale or changes-required review
cannot be hidden behind an earlier approval. With a lead assigned it must be
that current thread's own review, current both at the event and now. RC-1
requires path/digest and forbids a fabricated lead task ID. A task review cannot
substitute. Without a lead, RC-1's independently joined Reviewer task is required.

Every finding of every historical review still needs a disposition, keyed by
`(review event sequence, finding ID)`. Use the latest disposition for that pair.
Blocking/important findings cannot be missing or disputed; RC-1 refuses their
decline. Minor findings need a disposition but may be reasonedly declined or
remain disputed; no stronger minor policy is imposed. All dispositions require
concrete evidence, as enforced by RC-1: fixed includes a full commit; outside-tree
resolution includes evidence; withdrawal is by that review's author (including
a historical lead); decline is minor-only with a reason; second-review rejection
names a later distinct independent task review at the same base/head; owner-decided
names an existing resolved decision with `decidedBy` exactly literal `owner`.
Other decision authors remain valid general records. The recorder quotes the
owner's words in `answer` and identifies where/when they were given. These are
recorded provenance obligations, not authentication. EV-1 never bypasses loading.

Evidence descriptors are assertions: RC-1 does not open their paths. EV-1 uses
`records.concrete` to reject nested unknowns, nulls and empty evidence, never
opens artifacts or requires historical paths to survive a move. Nonempty text
is a recorded assertion, not a language to parse for proof or success.

## PR-ready convention for PB-1

`--pr-ready` adds two comparisons under CD-6: exact-head confirmed push and
exact-head passing PR verification, represented by `push` and `pull-request`
operations. PB-1 writes `succeeded`/`no-op` PR results **only after `pr verify`
passes that exact head**; failed/skipped verification records `failed`/`refused`.
It writes successful/no-op push results **only after `ls-remote` confirms the
head**; an actual push also needs `childStarted: true`, `childStatus: 0`.
EV-1 trusts this typed convention and parses no confirmation text.

Select the latest intent of each operation, then its latest joined result.
Any unresolved intent of that operation, including a later reconciliation
reopening an older one, blocks it using RC-1's `uncertain_items`; a late result
of an older attempt cannot replace the latest intent. Missing result is
unresolved. Resolved failures or pre-child refusals of older attempts do not
revoke a newer confirmed result. If facts dispute the current external state,
record a current operation or explicit reconciliation; reopened uncertainty
still blocks. Both selected results must name the exact checked head and outcome
`succeeded` or `no-op`. `succeeded` requires `statusSummary` with child started
and status zero. A no-op without a child may omit `statusSummary`; if present,
it too must show started/zero. Null summary, refusal, nonzero or true/null status
never counts. Revocation failure alone does not change a confirmed child-zero
outcome. `confirmation` must be concrete nonempty text under RC-1's schema;
EV-1 neither guesses success from that text nor reconstructs it through GitHub.

Manager delivery readiness neither grants nor blocks Task verification. This
command runs no check, publication, routing, live lookup or general readiness
framework. A newly recorded implementation head invalidates prior verification
until its own current checks, review and criteria exist. Retain artifact bytes
and verify them before recording descriptors; this command compares assertions.
