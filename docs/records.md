# Record format and schema contract

> **Status: RC-1b (part 2 of 3).** Implements the durable store and preserves
> RC-1a's record layout, `work.json`
> schema, event envelope schema, the 21 event-kind data schemas, explicit
> unknown rules and the pure validation API in `t3compose.schema`. RC-1 is
> complete only after RC-1c merges: RC-1a implements pure schema validation;
> RC-1b adds durable persistence, dispatch/routing history enforcement and
> `work new|show|list` plus `event add`; RC-1c adds checkpoint
> computation/verification, CD-4 review/finding/lead history, recovery and
> `work snapshot|reconcile|repair`.

## Storage layout and identities

Records live under `<local_dir>/state/records/` (default `.local/state/records/`):

```text
records/<identity>/work/<WORK-ID>/work.json
records/<identity>/work/<WORK-ID>/events/<seq>.json
```

- **Repository identity (`<identity>`):** Either normalized remote
  `<host>/<owner>/<name>` (such as `github.com/proxy-hatch/t3-compose`) or
  synthetic `synthetic/<name>` (such as `synthetic/router-pilot`). Each segment
  matches `^[A-Za-z0-9][A-Za-z0-9._-]*$` with no `.` or `..` segments; `<host>`
  contains `.` or equals `localhost` (`synthetic` is reserved for the two-part
  form).
- **Work ID (`<WORK-ID>`):** Matches `^[A-Za-z0-9][A-Za-z0-9._-]*$`.
- **Writer (`writer_id`):** `manager@<identity>` or `lead:<WORK-ID>@<identity>`.
- **Global index:** The set of `<WORK-ID>` directories under `records/<identity>/work/`.

## Design-to-field mapping

| Design record item | Schema location |
|---|---|
| Work ID, Linear issue, scope, design refs | `work.json`: `workId`, `linearIssue` (`null` when none), `scope`, `designRefs`; updated by `scope` event |
| Repository identity, target branch, review base, owned paths, dependencies | `work.json`: `repository`, `targetBranch`, `reviewBase`, `ownedPaths`, `dependencies` |
| Acceptance criteria and required checks | `work.json`: `acceptance` (`id`, `text`), `requiredChecks` (`id`, `command`, `kind`); updated by `scope` event |
| Single coordinator writer and transfer | `work.json`: `writer`; event envelope `writer`; `writer-transfer` (`from`, `to`, `reconciliationRef`, `checkpoint`) |
| Routing input, Jev classification, manager judgment, route selection | `routing-input`, `routing-classification`, `routing-judgment`, `routing-selection` |
| Dispatch intent ID, T3 task ID, selected role/model, three commits, worktree path | `dispatch-intent` (`clientRequestId`, `role`, `round`, `selection`, `provisionedCommit`, `startCommit`, `reviewBase`, `dispatchingCheckout`, `isolation`, `briefPath`, `briefDigest`), `dispatch-confirmed` (`clientRequestId`, `taskId`, `childThread`, `worktreePath`) |
| Result commits, T3 status evidence, branch observed, claims, verified facts, questions | `result` (`taskId`, `t3Status`, `worktreePath`, `resultCommit`, `parentCommit`, `branchObserved`, `workerClaims`, `verifiedFacts`, `questions`) |
| Check outcomes/sources and criterion evidence | `check` (`checkId`, `command`, `commit`, `outcome`, `source`, `evidencePath`), `criterion-evidence` (`criterionId`, `commit`, `source`, `evidence`) |
| Manager-recorded Feature lead assignment | `lead-assignment` (`thread`, `assignedBy`, `date`, `reason`) |
| Review sequence reference, reviewer provenance, base/head, evidence path+digest, findings, verdict | `review` (`reviewer`, `base`, `head`, `verdict`, `findings`, `evidence` required for `lead` and optional for `task`) |
| Finding responses and dispositions | `finding-disposition` (`reviewRef`, `findingId`, `response`, `disposition`, `by`, `evidence`, optional `commit`/`reason`) |
| External operation intent and confirmed result | `op-intent` (`opId`, `operation`, `target`, `commit`, `expectedPriorHead`), `op-result` (`intentRef`, `opId`, `operation`, `target`, `commit`, `outcome`, `confirmation`, optional `statusSummary`) |
| Owner decisions, next action, reconciliation, restart and pilot handoff | `decision`, `next-action`, `reconciliation`, `restart`, `handoff` |

## Common primitives

Validators reject `bool` where `int` is required, reject unknown keys on fixed
objects, require `jsonio.canonical_json` compatibility (finite numbers, UTF-8
without surrogates or NUL, integers `<= 4300` digits), and cap open JSON
mappings at nesting depth 8 and 16,384 canonical UTF-8 bytes; nested explicit
unknowns are allowed in open mappings. Diagnostics index arbitrary keys without echoing them.

- `text`: nonempty UTF-8 `str` (`len(s.strip()) > 0`, no `\x00`).
- `id_token`: `^[A-Za-z0-9][A-Za-z0-9._:-]*$`.
- `fact_id`: `^[a-z0-9]+(-[a-z0-9]+)*$`.
- `commit_sha`: `^[0-9a-f]{40}$`.
- `sha256_digest`: `^sha256:[0-9a-f]{64}$`.
- `pos_int` / `nonneg_int`: `int` (never `bool`) `>= 1` / `>= 0`.
- `utc_timestamp` / `utc_date`: ASCII-digit, exact valid `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` / `YYYY-MM-DD`.
- `unknown_obj`: exact `{"unknown": <text>}`; allowed only where documented, never to omit required keys or replace provenance/identity.
- `selection_obj`: direct `{"providerInstanceId": <text>, "model": <text>, "options": {<text>: <str|bool>}, "roles"?: [<role>, ...]}` (unique roles from `worker|reviewer|feature-lead`) or pool `{"providerPool": <text>, "model": <text>, "options": {<text>: <str|bool>}, "roles"?: ["worker"]}` (disjoint keys).
- `checkpoint_obj`: `{"lastSeq": <nonneg_int>, "digest": <sha256_digest>}`.

## `work.json` and event envelope schemas

`work.json` requires exact keys:

| Field | Type / Rule |
|---|---|
| `workId` | `work_id` (`^[A-Za-z0-9][A-Za-z0-9._-]*$`) |
| `linearIssue` | `text \| null` |
| `repository` | `repo_identity` (`<host>/<owner>/<name>` or `synthetic/<name>`) |
| `targetBranch` | Git branch syntax (`text`, no forbidden characters, control codes, reflog syntax or invalid components) |
| `reviewBase` | `commit_sha` |
| `scope` | `text \| nonempty list[text]` |
| `designRefs`, `dependencies` | `list[text]` (may be empty) |
| `ownedPaths` | `nonempty list[relative_path]` (no leading `/`, `\` or `.`/`..` segments) |
| `acceptance` | `nonempty list[{"id": <id_token>, "text": <text>}]` (unique `id`) |
| `requiredChecks` | `nonempty list[{"id": <id_token>, "command": <text \| nonempty list[text]>, "kind": "fast" \| "heavy"}]` (unique `id`) |
| `writer` | `manager@<repository>` or `lead:<workId>@<repository>` matching `repository` and `workId` |

Every `events/<seq>.json` envelope requires exact keys:
`schemaVersion` (`1`), `seq` (`pos_int`), `kind` (in `EVENT_KINDS`), `at`
(`utc_timestamp`), `writer` (`writer_id`), `helperCommit` (`commit_sha`), and
`data` (`dict` matching `kind`). For `restart`, `handoff` and `writer-transfer`,
`data.checkpoint.lastSeq < seq`; for after-phase `restart`, `data.beforeRef < seq`.

## Event `data` schemas (all 21 kinds)

1. **`routing-input`** (also standalone `{task, context}` via `validate_routing_input`):
   - `task`: exact keys `intent` (`text`), `acceptance` (`nonempty list[text | {"id": <id_token>, "text": <text>}]`), `interfaces` (`list[text]`), `scope` (`text | nonempty list[text]`), `riskFacts` (`list[fact_id]`), `dependencies` (`list[text]`).
   - `context`: `null` or nonempty JSON `dict` (if `priorClassificationRef` is present and non-null, `pos_int`).
   - Canonical UTF-8 size of `{"task": ..., "context": ...}` `<= 16384` bytes.
   - `inputDigest`: `sha256_digest` equal to the canonical digest of `{"task": ..., "context": ...}`.
   - `policyDigest`: `sha256_digest`.
2. **`routing-classification`**:
   - `inputRef` (`pos_int`), `inputDigest` (`sha256_digest`), `policyDigest` (`sha256_digest`), `ruleVersion` (`text`), `criteria` (`{"domain": {"SOFTWARE_ENGINEERING": <text>, "OTHER": <text>, "MIXED_OR_UNCLEAR": <text>, "instructions"?: <text>}, "difficulty": {"EASY": <text>, "MEDIUM": <text>, "CHALLENGING": <text>, "INSUFFICIENT_CONTEXT": <text>, "instructions"?: <text>}}`), `criteriaDigest` (`sha256_digest`), `riskFacts` (`list[fact_id]`), `configuredModel` (`text`), `snapshotModel` (`text | unknown_obj`), `responseId` (`text | unknown_obj`), `answers` (`unknown_obj` or `{"domain": <choice_ans>, "difficulty": <choice_ans>}` where each `<choice_ans>` has `type: "choice"`, valid `choice`, finite `probabilities` in `[0,1]` for exact options summing to `1.0 +- 0.01`, and finite `confidence` in `[0,1]`), `outcome` (`"accepted" | "retry-with-context" | "manager-judgment"`), `reason` (`text`).
   - When `outcome == "accepted"`, `snapshotModel`, `responseId` and `answers` must be concrete, `domain.choice != "MIXED_OR_UNCLEAR"`, and `difficulty.choice != "INSUFFICIENT_CONTEXT"`.
3. **`routing-judgment`**:
   - `inputRef` (`pos_int`), `inputDigest` (`sha256_digest`), `policyDigest` (`sha256_digest`), `domain` (`"SOFTWARE_ENGINEERING" | "OTHER"`), `difficulty` (`"EASY" | "MEDIUM" | "CHALLENGING"`), `reason` (`text`), `basis` (`"jev-failure" | "low-confidence" | "mixed-or-unclear" | "manual"`).
4. **`routing-selection`**:
   - `inputRef` (`pos_int`), `inputDigest` (`sha256_digest`), `policyDigest` (`sha256_digest`), `classificationRef` (`pos_int | null`, null only for `role == "text-helper"`), `role` (`"worker" | "reviewer" | "feature-lead" | "text-helper"`), `round` (`"first" | "corrections"`), `reviewScope` (`"code" | "document" | "corrections-delta" | null`, non-null iff `role == "reviewer"`), `tier` (`"easy" | "medium" | "challenging"`, never `"easy"` for `reviewer` or `feature-lead`), `floorsApplied` (`list[fact_id]`), `escalation` (`bool`), `selection` (`selection_obj`, never pool for `reviewer` or `feature-lead`), `chosenPoolAccount` (`text | null`, non-null iff pool `selection`), `skippedPoolAccounts` (`list[{"providerInstanceId": <text>, "reason": <text>}]`), `availabilityReason` (`text`), `fallbackReason` (`text | null`), `configDigest` (`sha256_digest`), `catalogReadAt` (`utc_timestamp`), `catalogDigest` (`sha256_digest`), `quotaStateDigest` (`sha256_digest`).
5. **`dispatch-intent`**:
   - `role` (`worker|reviewer|feature-lead|text-helper`), `round` (`pos_int`), `clientRequestId` (`work_id`), `briefPath` (`text`), `briefDigest` (`sha256_digest`), `selection` (`selection_obj`), `provisionedCommit` (`commit_sha`), `startCommit` (`commit_sha`), `reviewBase` (`commit_sha`), `dispatchingCheckout` (`text`), `isolation` (`"worktree" | "shared"`).
6. **`dispatch-confirmed`**:
   - `clientRequestId` (`work_id`), `taskId` (opaque nonempty `text`), `childThread` (`text`), `worktreePath` (`text | unknown_obj`).
7. **`result`**:
   - `taskId` (opaque nonempty `text`), `t3Status` (`{"state": "completed"|"failed"|"cancelled"|"interrupted", "summary": <text>, "turnId"?: <text>}`), `worktreePath` (`text`), `resultCommit` (`commit_sha`), `parentCommit` (`commit_sha`), `branchObserved` (`branch_name | unknown_obj`), `workerClaims` (JSON `dict`), `verifiedFacts` (JSON `dict`), `questions` (`list[text]`).
8. **`check`**:
   - `checkId` (`id_token`), `command` (`text | nonempty list[text]`), `commit` (`commit_sha`), `outcome` (`"pass" | "fail" | "refused" | "blocked"`), `source` (`"manager" | "feature-lead" | "reviewer" | "assigned-session" | "worker"`), `evidencePath` (`text`).
9. **`criterion-evidence`**:
   - `criterionId` (`id_token`), `commit` (`commit_sha`), `source` (`"manager" | "feature-lead" | "reviewer" | "assigned-session" | "worker"`), `evidence` (`text | nonempty JSON dict`).
10. **`lead-assignment`**:
    - `thread` (`text`), `assignedBy` (`manager@<identity>` only), `date` (`utc_date`), `reason` (`text`).
11. **`review`**:
    - `reviewer` (`{"kind": "task", "taskId": <text>}` or `{"kind": "lead", "thread": <text>}` with no `taskId`), `base` (`commit_sha`), `head` (`commit_sha`), `verdict` (`"approve" | "approve-after-fixes" | "changes-required"`), `findings` (`list[{"id": <id_token>, "severity": "blocking"|"important"|"minor", "summary": <text>, "location"?: <text>}]` with unique `id`), optional `evidence` (`{"path": <text>, "digest": <sha256_digest>} | null`, required non-null when `reviewer.kind == "lead"`).
12. **`finding-disposition`**:
    - `reviewRef` (`pos_int` review event sequence), `findingId` (`id_token`), `response` (`text`), `disposition` (`"fixed" | "resolved-outside-tree" | "declined" | "withdrawn" | "disputed" | "rejected-by-second-review" | "owner-decided"`), `by` (`text`), `evidence` (`text | nonempty JSON dict`), optional `commit` (`commit_sha | null`, required when `disposition == "fixed"`), optional `reason` (`text | null`, required when `disposition == "declined"`).
13. **`op-intent`**:
    - `opId` (`id_token`), `operation` (`"push" | "pull-request" | "other"`), `target` (`text`), `commit` (`commit_sha`), `expectedPriorHead` (`commit_sha | "absent" | null`, non-null when `operation == "push"`).
14. **`op-result`**:
    - `intentRef` (`pos_int`), `opId` (`id_token`), `operation` (`"push" | "pull-request" | "other"`), `target` (`text`), `commit` (`commit_sha`), `outcome` (`"succeeded" | "no-op" | "failed" | "refused" | "unknown"`), `confirmation` (`text`), optional `statusSummary` (`{"childStarted": <bool>, "childStatus"?: <0..255|null>, "revocation"?: "ok"|"failed"|null, "refusal"?: <text|null>} | null`; `childStarted == false` requires `refusal` and null `childStatus`; `childStarted == true` forbids `refusal`).
15. **`decision`**:
    - `question` (`text`), `answer` (`text | unknown_obj`), `decidedBy` (`text | unknown_obj`), `date` (`utc_date | unknown_obj`). When `answer` is resolved `text`, `decidedBy` and `date` must be concrete.
16. **`scope`**:
    - `decisionRef` (`pos_int | text`), optional `scope`, `acceptance`, `requiredChecks` (same shapes as `work.json`; at least one must be non-null).
17. **`next-action`**:
    - `action` (`text | unknown_obj`).
18. **`reconciliation`**:
    - `item` (`{"kind": "dispatch"|"operation"|"lead"|"worktree"|"temp-file"|"takeover", "ref": <pos_int|text>}`), `t3Facts` (`JSON dict | unknown_obj`), `gitFacts` (`JSON dict | unknown_obj`), `outcome` (`"completed" | "stopped-and-preserved" | "not-started" | "in-flight" | "unknown-liveness" | "orphaned-temp-repaired"`), optional `notes` (`text | null`).
19. **`restart`**:
    - `phase` (`"before" | "after"`), `checkpoint` (`checkpoint_obj`), optional `beforeRef` (`pos_int | null`; forbidden/null for `"before"`, required `> checkpoint.lastSeq` for `"after"`).
20. **`handoff`**:
    - `pointers` (`{"skill": <text>, "repository": <repo_identity>, "recordsPath": <text>, "routingPolicyPath": <text>, "takeoverDumpPath"?: <text|null>}`), `holdEndCondition` (`text`), `pilotCriteria` (`nonempty list[text]`), `nextAction` (`text | unknown_obj`), `checkpoint` (`checkpoint_obj`).
21. **`writer-transfer`**:
    - `from` (`writer_id`), `to` (`writer_id` distinct from `from` on the same repository), `reconciliationRef` (`pos_int <= checkpoint.lastSeq`), `checkpoint` (`checkpoint_obj`).

## Public Python API (`t3compose.schema`) and RC-1b/1c boundary

- `SCHEMA_VERSION = 1`, `MAX_ROUTING_INPUT_BYTES = 16384`, `EVENT_KINDS`, `EVENT_SCHEMAS`.
- `SchemaError(path, rule)`: `ValueError` subclass with `.path` and `.rule`; never echoes raw input values or secrets.
- `validate_work(value) -> None`, `validate_event(value) -> None`, `validate_event_data(kind, data, path="event.data") -> None`, `validate_routing_input(value, path="routingInput") -> str`.
- **RC-1b/RC-1c boundary:** Durable APIs and implemented history are specified
  below. Review/finding/lead history, checkpoint verification and recovery await c.

## Durable store API and history

RC-1b implements part 2 of 3. The Engineering manager's revised boundary of
**2026-10-28** assigns CD-4 history enforcement to RC-1c: review event-sequence
references, finding identities across rounds, disposition joins, author and
current-lead provenance/currency. The store refuses `review`,
`finding-disposition`, `lead-assignment`, `restart`, `handoff`, `writer-transfer`
on append AND load with a sanitized RC-1c error. These shapes remain validated
by `schema`. Check sources, including Feature lead and Worker, remain recorded;
EV-1 decides verification after c. All downstream gates still wait for c.

Public functions in `t3compose.records` (all arguments are Python values):

- `normalize_repository(repo) -> str`: identity, HTTPS/SSH origin or checkout path.
- `create_work(local_dir, repo, work_id, value, *, writer) -> dict`.
- `load_work(local_dir, repo, work_id) -> dict`.
- `list_work(local_dir, repo) -> {works, damaged}`; caller checks `damaged`.
- `append_event(local_dir, repo, work_id, kind, data, *, writer, checkout=None)`
  returns the stored envelope. No retry after exclusive collision.
- `record_routing_input(local_dir, repo, work_id, *, task, context, policy_digest,
  writer) -> {inputRef, inputDigest, policyDigest}` computes the digest using
  `schema.validate_routing_input` and appends the canonical input.
- `RecordError` is a sanitized `ValueError`; schema failures use `SchemaError`.
  Filesystem/process failures become sanitized `RecordError` at the store edge.

Create/load return `{work, events, effectiveWork}`. `work` is immutable;
`effectiveWork` applies successive non-null scope/acceptance/requiredChecks
replacements. Every load/append validates schema and the complete contiguous
prefix starting at 1. Filenames are unpadded decimal sequence plus `.json`;
nonmatching envelopes, gaps, aliases and unknown files fail that work. Recognized
helper temporaries are ignored, never removed by b. Work list isolates damaged
work. Each path component below the selected state root is opened without
following symlinks; files must be regular, private, owned by the current user.
A new work directory is reserved exclusively; its events directory is durable
before `work.json` is published. Creation also syncs reused ancestors, since
a previous creator may have failed before syncing them. Incomplete creation lacks usable metadata and
cannot be silently adopted by another create. No index file exists.

History rules:

- Every envelope uses the work's writer. Request identity must match stored
  repository/work/writer. Transfers remain refused until prefix verification.
- Dispatch request IDs are unique. Exact payload retries return their existing
  intent; stored duplicate intents are corruption. Worker and Feature lead
  intents block another writer until a completed result or proven preservation.
  Confirmation binds a unique native task/thread and never clears the block.
- Every intent's review base equals work.reviewBase. Worker round 1 starts there;
  subsequent Worker rounds increment by one and start at the latest Worker
  result commit, or the preceding Worker start when no result exists. A Reviewer
  uses `reviewBase` as reviewed base and `startCommit` as reviewed head;
  `provisionedCommit` always records the actual dispatching checkout HEAD.
  HEAD is checked on new append, not re-read while replaying historical events.
  Optional checkout verification also checks the start commit object.
- Results join a confirmation by task ID, require completed status evidence and
  matching known worktree path, and carry full result/parent commits. A task has
  one completed result. Interrupted/failed/cancelled result shapes are supported
  by schema but refused by this store, and never clear duplicate-writer blocks.
- Dispatch reconciliation `item.ref` names an intent sequence or request ID.
  Only `stopped-and-preserved` clears: `t3Facts` requires matching
  `clientRequestId`, `state: "stopped"`, `childrenReconciled: true`, nonempty
  `evidence`, and matching `taskId` when confirmed. `gitFacts` requires
  `commitsPreserved: true`, `uncommittedPreserved: true`, nonempty `evidence`
  and `worktreePath` (matching the confirmation when known). These are recorded
  coordinator observations, not a live T3 query. Unknown/other outcomes do not
  clear. Unsupported preservation claims are refused, never trusted by label.
- Classifications/judgments/selections join existing routing input with exact
  input/policy digests; selection's classification/judgment must name the same
  input and digests. Retry context references an existing classification.
  Classification criteria digest and input risk facts must agree. No routing
  policy choice or network call occurs here.
- Operation IDs are unique; results join their intent's ID/operation/target/commit.
  Integer scope decision references name a resolved prior decision; text refs
  name external decisions. Check/criterion IDs must exist in effective scope.

## Atomic JSON APIs and temporary ownership

`jsonio.atomic_replace(path, value, *, dir_fd=None)` and
`jsonio.atomic_create(path, value, *, dir_fd=None)` write canonical UTF-8 JSON
and return None. With `dir_fd`, path is one basename in that open directory;
otherwise its parent must exist. Create uses an exclusive hard link and raises
`FileExistsError` on collision; replace uses rename. Both use a same-directory
0600 regular temporary, file fsync before publication, directory fsync afterward,
then temporary cleanup and another directory fsync. An error after publication
may leave the complete new file; callers must inspect before retrying.

Reserved temporary names are `.t3c-tmp-<positive PID>-<32 lowercase hex>.tmp`.
Creation briefly serializes a private umask override and restores the prior
mask even if open fails, so mode is 0600 from first visibility. The creator
holds an exclusive advisory `flock` on the inode through writing,
publication and cleanup. Future c repair must require this exact name, regular
file, current UID, mode 0600, link count 1 or 2, proof the named PID is dead
(`kill(pid, 0)` yields ESRCH), AND a nonblocking exclusive inode lock, then
recheck path/inode identity before unlinking. A live/reused PID, permission error,
symlink, unknown name or lock conflict is never an orphan. Dead-PID evidence
closes the create-before-inode-lock window. Unrelated files are never removed;
this reserved namespace is a cooperative protocol, not protection from hostile
same-user filesystem edits. b implements no repair or crash-recovery command.
