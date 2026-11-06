# Record format and schema contract

> **Status: RC-1c (part 3 of 3).** Adds checkpoints, recovery and CD-4
> history to the durable store. RC-1 dependents and Stage 2 remain gated until
> this Task merges after lead review. These commands record and validate facts;
> they do not perform EV-1 acceptance or query T3.

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
| Routing input, Jev classification, manager judgment, route selection | `routing-input`, `routing-classification`, `routing-judgment`, `routing-selection` (version-2 and version-3 routing shapes) |
| Vertical escalation | `routing-escalation` (role, lane, stages, reason, bounded evidence, WIP commit) |
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

## Event `data` schemas (all 22 kinds)

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
   - Optional `billing`: exactly `"per-token"` or absent. Null and other values
     are invalid. Existing subscription records without it remain valid.
     Routing sets it for every final metered Worker selection and reports it
     in `route select` output. Schema validation adds no instance/model naming
     heuristic; driver eligibility is enforced by routing against the catalog.

   **Version-3 routing shapes (items 2–4):** The shapes above remain the
   version-2 contract. Records already written keep their shapes and validation
   results. For these three kinds only, absence of `configVersion` selects the
   version-2 validator; presence requires integer `3` (never `bool`), otherwise
   `SchemaError(<path>.configVersion, "unsupported configVersion")`. Version 3
   requires every field below except optional `billing`; unknown keys fail.
   The envelope and `SCHEMA_VERSION = 1` stay unchanged.

   - **Classification:** `configVersion: 3`, `inputRef` (`pos_int`),
     `inputDigest`, `policyDigest`, `criteriaDigest` (`sha256_digest`),
     `ruleVersion`, `configuredModel`, `reason` (`text`), `riskFacts`
     (`list[fact_id]`), `snapshotModel`, `responseId` (`text | unknown_obj`).
     `criteria` has exact questions `lane` (`BACKEND_LOGIC|UI_FRONTEND|MIXED`,
     and optionally `DOCS`), `criticality` (`CORE|NON_CRITICAL`), `scope`
     (`STANDARD|COMPLEX|INSUFFICIENT_CONTEXT`); each question maps every option
     to `text` and permits optional `instructions: text`. `answers` is
     `unknown_obj` or those three questions with the same `choice_ans` rules
     above (exact option probabilities, sum within 0.01, confidence in [0,1]),
     where the options are the criteria the record lists: a `choice` outside
     them, or probabilities whose keys are not exactly them, are invalid. A
     record written before `DOCS` existed lists three lane options and stays
     valid. `outcome` is `accepted|retry-with-context|defaulted`. Accepted
     requires concrete snapshot, response ID and answers, with scope other than
     `INSUFFICIENT_CONTEXT`; `MIXED` is accepted and maps to backend-logic, and
     `DOCS` is accepted and keeps its own lane.
   - **Judgment:** `configVersion: 3`, `inputRef` (`pos_int`), `inputDigest`,
     `policyDigest` (`sha256_digest`), `lane` (`BACKEND_LOGIC|UI_FRONTEND|DOCS`),
     `criticality` (`CORE|NON_CRITICAL`), `scope` (`STANDARD|COMPLEX`),
     `reason` (`text`), `basis`
     (`manual|default-low-confidence|default-insufficient-context|default-jev-failure`).
     Required `defaulted` is a list of question names (`lane|criticality|scope`),
     without repeats. Each listed answer equals its fixed default:
     lane `BACKEND_LOGIC`, criticality `CORE`, scope `COMPLEX`; unlisted
     answers retain any valid confident value. A confident `MIXED` lane is
     recorded as `BACKEND_LOGIC`, since `MIXED` maps to backend-logic, and is
     not listed. A confident `DOCS` lane is kept as `DOCS`; an uncertain lane is
     listed and takes `BACKEND_LOGIC`. Manual requires an empty list;
     default-jev-failure requires all three names; default-low-confidence and
     default-insufficient-context require at least one name.
   - **Selection:** `configVersion: 3`, `inputRef` (`pos_int`),
     `inputDigest`, `policyDigest`, `configDigest`, `catalogDigest`,
     `quotaStateDigest` (`sha256_digest`), `catalogReadAt` (`utc_timestamp`),
     `classificationRef` (`pos_int|null`), `role`
     (`feature-lead|worker|reviewer|verifier|search|text`), `lane`
     (`default|backend-logic|ui-frontend|docs|non-critical|core|null`), `stage`
     (`1|2|3|null`, never bool), `pass` (`pre|record|null`), `round`
     (`first|corrections|re-review|null`), `criticality` (`CORE|NON_CRITICAL|null`),
     `scope` (`STANDARD|COMPLEX|null`), `floorsApplied` (`list[fact_id]`),
     `selection` (`v3_selection`), `chosenPoolAccount` (`text|null`),
     `chosenIndex` (`nonneg_int`), `targetsTried` (nonempty list of exact
     `{index: nonneg_int, selection: v3_selection, account: text|null, reason: text}`).
     `lane` is the cell's matrix lane, not the classification's lane; `stage`
     is the integer form of the config's stage key `"1"` to `"3"`; `criticality`
     and `scope` are the values after risk floors. In `targetsTried`, index 0
     is the cell's primary and index k its k-th quota fallback; a pool target
     appears once per account walked, and a target skipped as a whole has a
     null account.
     Optional `billing` is exactly `"per-token"` or absent; it marks every
     metered selection, whatever its role. Optional `laneFallback` is exactly
     `"docs-to-backend-logic"` or absent; it marks a Worker selection of a `DOCS`
     decision made in the `backend-logic` lane because the configuration has no
     `docs` lane, and no other selection may carry it.
     A `v3_selection` has exactly one of `providerInstanceId` and `providerPool`
     (`text`), `model: text`, and `options` (string or boolean values);
     no `roles`; pool options must be `{}`.
     Matrix roles require non-null classification reference, lane, stage, round,
     criticality and scope. Lanes are `feature-lead: default`,
     `worker: backend-logic|ui-frontend|docs`, `reviewer: non-critical|core`.
     Complex scope needs stage 2 or 3; corrections is Worker-only, re-review
     Reviewer-only. Reviewers require pre or record pass, and core lane iff CORE
     criticality; every other role has null pass. Helpers require all seven
     classification/matrix fields null and empty floors.
     The chosen account is non-null iff the selection is a pool.
     The first tried index is 0, and each later index equals the previous one
     or the next; repeated indices have equal selections; direct targets have
     null account. The last target matches chosenIndex, selection and
     chosenPoolAccount exactly. Pre-pass requires chosenIndex and every tried
     index zero. A selection always names its chosen target, so a refused or
     unavailable pre-pass, which has no fallback and is skipped, writes no
     `routing-selection`; version-3 routing reports the skip and its reason in
     its command output instead.
     Existing `records.py` joins apply unchanged to these version-3 shapes.

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

22. **`routing-escalation`** (no `configVersion`):
    - Exact keys: `role` (`feature-lead|worker|reviewer`), `lane` (valid for
      that role as above), `fromStage` (`1|2`), `toStage` (`2|3`), `reason`
      (`worker-hard-stop|finding-unresolved|review-deadlock|reviewer-failure|lead-stalled`),
      `evidence` (`text`, at most 8,192 UTF-8 bytes), `wipCommit` (`commit_sha|null`).
    - Stages are integers, never bool; toStage equals fromStage + 1.
      Worker-hard-stop and finding-unresolved require Worker; review-deadlock
      and reviewer-failure require Reviewer; lead-stalled requires Feature lead.
      Worker-hard-stop requires a non-null WIP commit.

## Public Python API (`t3compose.schema`)

- `SCHEMA_VERSION = 1`, `MAX_ROUTING_INPUT_BYTES = 16384`, `EVENT_KINDS`, `EVENT_SCHEMAS`.
- `ROUTING_V3_SCHEMAS`: read-only mapping of the three version-3 specs, with
  the same required/optional/post structure as `EVENT_SCHEMAS` (whose routing
  entries remain version 2). `MAX_ESCALATION_EVIDENCE_BYTES = 8192`.
  Both event validators apply the `configVersion` dispatch described above.
- `SchemaError(path, rule)`: `ValueError` subclass with `.path` and `.rule`; never echoes raw input values or secrets.
- `validate_work(value) -> None`, `validate_event(value) -> None`, `validate_event_data(kind, data, path="event.data") -> None`, `validate_routing_input(value, path="routingInput") -> str`.
- Durable APIs and cross-event validation are specified below.

## Durable store API and history

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
replacements and writer transfers. Every load/append validates schema and the complete contiguous
prefix starting at 1. Filenames are unpadded decimal sequence plus `.json`;
nonmatching envelopes, gaps, aliases and unknown files fail that work. Recognized
helper temporaries are ignored by history loading. Work list isolates damaged
work. Each path component below the selected state root is opened without
following symlinks; files must be regular, private, owned by the current user.
A new work directory is reserved exclusively; its events directory is durable
before `work.json` is published. Creation also syncs reused ancestors, since
a previous creator may have failed before syncing them. Incomplete creation lacks usable metadata and
cannot be silently adopted by another create. No index file exists.

History rules:

- Every envelope uses the writer effective at that sequence. Request identity
  must match stored repository/work and the current writer, including retries.
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
  Any later non-preservation dispatch observation revokes clearance, including
  after a result; replacement writers and transfers remain blocked until fresh
  valid preservation or a completed result resolves it. Both intent sequence and
  request-ID references identify the same dispatch.
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
publication and cleanup. Repair requires this exact name, regular
file, current UID, mode 0600, link count 1 or 2, proof the named PID is dead
(`kill(pid, 0)` yields ESRCH), AND a nonblocking exclusive inode lock, then
recheck path/inode identity before unlinking. A live/reused PID, permission error,
symlink, unknown name or lock conflict is never an orphan. Dead-PID evidence
closes the create-before-inode-lock window. Unrelated files are never removed;
this reserved namespace is a cooperative protocol, not protection from hostile
same-user filesystem edits. Recovery scans both the work root and its events directory.

## Checkpoints and coordinator history

`snapshot(local_dir, repo, work_id, *, through_seq=None)` returns exactly
`{lastSeq, digest}`. The digest is `sha256:` plus SHA-256 of
`jsonio.canonical_json({"work": work, "events": events[:lastSeq]})`, including
complete immutable metadata and complete ordered event envelopes. The default
is the loaded event count; an explicit integer must be between zero and that
count. Full history is validated even when requesting an earlier prefix. No
snapshot/index file is written. JSON formatting is not part of this digest.

Handoff, before-restart and transfer require the immediate pre-event prefix;
after-restart requires the same checkpoint as its referenced prior before event.
All checkpoints are recomputed against their own prefix, never the latest digest.
The before reference must name a before-phase restart, and may be consumed once.
Handoff's repository must match the work. A changed saved prefix refuses replay
and append; preserve the files and reconcile against the saved evidence.

`effectiveWork.writer` follows validated transfers; `work.writer` never changes.
The latest `lead-assignment` in `events` identifies the lead (none initially).
Only `manager@<repository>`, while current writer, can assign; `assignedBy` must
match. Assignment does not transfer ownership. Transfers are only between that
manager and `lead:<workId>@<repository>`; transfer to a lead requires assignment.
To replace an active lead, reconcile and transfer back to the manager, append
assignment, then reconcile and transfer to the lead. No nested lead assignment.

Transfer's `reconciliationRef` must name a `stopped-and-preserved` event in the
current writer tenure, with `item: {kind: "takeover", ref: <old writer>}`.
`t3Facts` requires `writer: <old writer>`, `state: "stopped"`,
`childrenReconciled: true`, and nonempty `evidence`; when the old writer is a
known lead it also requires its current `thread`. `gitFacts` requires
`commitsPreserved: true`, `uncommittedPreserved: true`, nonempty `evidence` and
`worktreePath`. These use dispatch clearance's existing observation convention.
Every recorded dispatch, including Reviewers, must separately have a completed
result or valid stopped-and-preserved clearance. A new dispatch after the writer
reconciliation invalidates that proof, as does any later reconciliation of the
same writer. Reusing proof from an older tenure fails.
These are coordinator attestations, not automatic liveness discovery or authority
for another thread to manage children. Preserve unknown outcomes until reconciled.

## CD-4 review and finding history

A task review must join an actual reviewer-role intent, confirmation and completed
result by task ID: base equals work/intent review base; head equals the reviewer's
start and result commit; known confirmation/result worktree paths match. A Worker
result cannot stand in for a Reviewer. Both review forms must name the work base
and a head already recorded as a Worker result (or the original base). A lead
review must name the lead current at that event and include its path/digest.
Historical valid reviews remain valid when assignments change. EV-1 must compare
that historical assignment with the current assignment again at verification.

Review evidence is a recorded descriptor, not read from disk during replay;
paths and SHA-256 digests follow the schema. This keeps historical records usable
when artifacts move; callers retain the artifacts and verify their bytes before
recording. Checks retain distinct source labels, including `feature-lead` and
`worker`; storing a check never makes a Worker claim verified.

Findings key on `(review event sequence, finding ID)`. `by` identifies a review
author as `task:<native taskId>` or `lead:<thread>` (prefixes are literal, even
when the native ID already contains a colon). Withdrawal requires that exact
review author, including historical leads. Declining requires minor severity
and a reason. Fixed requires its full commit SHA and evidence. Evidence must be
concrete nonempty text or a concrete nonempty mapping, without nested unknowns.
For `rejected-by-second-review`, use `{reviewRef: <later task review sequence>}`
from a distinct independent Reviewer covering the same base/head; for
`owner-decided`, use `{decisionRef: <resolved decision sequence>}` whose
`decidedBy` is exactly `"owner"`, the reserved value for Shawn. The recorder
quotes Shawn's words in `answer` and identifies where and when he gave them.
This is recorded provenance, not authentication; writers must follow this rule.
Other authors remain valid for general decisions but cannot supply owner
disposition authority. Other
mappings/text record the artifact or explanation; these are assertions, not EV-1
acceptance. No disposition overwrites an earlier attempt or review.

## Reconciliation and repair API

`reconcile(local_dir, repo, work_id=None, *, repair=False)` returns
`{works, damaged}`. Each work report has `workId`, `uncertain`, `temporaries`.
Uncertain entries have `kind`, `ref` (intent sequence), `intent`, `confirmation`
(null if absent), and `observations` (related reconciliation/result envelopes).
Dispatch entries remain until completed result or valid preservation, never just
confirmation. Operation entries remain without a concrete result; `unknown`
outcomes and a started child with unknown status remain uncertain regardless of
an outcome label. Retained intents, confirmations and observations allow later
unknown/in-flight reconciliations to reopen resolved items, by sequence or ID;
a subsequent concrete operation result clears its uncertainty again. Operation
reconciliations with unknown native/artifact facts also reopen the item.
Results retain confirmation evidence for caller inspection.
For a new strict publication attempt, expected preparation failures after its
actual intent and before the token runner record `refused` without a fabricated
`statusSummary`, child status or token/revocation facts. Invalid pre-intent input
creates no operation. If result persistence fails, the helper reports a sanitized
exit 2 and preserves the actual uncertain intent and retained files; it does not
claim a recorded refusal. Recovery and post-token status semantics are unchanged.
The helper never polls T3/Git to resolve an uncertain item. Callers inspect native
request/task/thread/worktree references and artifacts, then append the facts.

Each temporary entry has a work-relative `path` and `state`: `orphan`, `retained`,
`removed`, or `error`. Only exact reserved names are inspected; other names are
never removed. Retained means at least one ownership/liveness/lock predicate
could not be proven. Missing files during concurrent cleanup are omitted. Repair
uses the same proof as reconciliation, pins directories and files, takes the
nonblocking inode lock, rechecks path/inode and metadata, unlinks only that name,
and fsyncs the directory. It never unlinks the event even with a second hard link.
A removal followed by failed fsync is reported as error; inspect and retry.

Damaged entries use the list command's sanitized `workId`/`error` shape. A damaged
work still receives a temporary scan when its directories can be safely pinned,
including a partially created work with no metadata/events. Repair cannot complete
creation, adopt it, or remove its reservation; preserve artifacts for manual
reconciliation. Other valid work remains usable. An unsafe directory is not
traversed. Reports are observations of a cooperative filesystem, not a transaction
across all works or protection from hostile same-user mutation.

## Artifact provenance source-record recipe

The internal [privacy provenance loader](cli/privacy.md#internal-artifact-provenance-foundation)
uses existing `decision` events, not a new approval service. Only the owner may
approve an opaque inventory. A manager/coordinator may record a faithful actual
owner communication and independently supplied verification; implementation
assignment, prior publication, pilot launch and public-product-text resolutions
approve no opaque inventory. No real receipt or approval is installed by this
foundation. Tests use explicitly synthetic authorizations only.

Prepare a private manifest, independently observed baseline/inventory evidence
and counts/digests using the documented helper, without exposing patterns. Pick
a new stable receiptId and one dedicated authorityWorkId for its lifecycle.
Record the actual owner communication's locator, source date and exact quote in
the source snapshot. The source and structured verification bind the inventory
context; the full approval context additionally binds both evidence digests.
Use the exact closed schemas and hashing order documented in privacy.md.
The recorder's append timestamp remains its actual current timestamp, even when
the source decision date is earlier. Never invent a source or label a manager's
answer as the owner's.

The approval event must have exactly this decision data:

```text
question: privacy-artifact/v1 approve <receiptId> <fullContextDigest>
answer: <exact source.quote>
decidedBy: owner
date: <exact source.date>
```

`approvalSeq` references that exact event in the selected repository's current
authority work stream. A bare owner label, generic approval, unrelated decision,
wrong answer/date/context, or unattached evidence cannot supply authority. Finish
the receipt and pin its raw digest in the private policy. Digest changes require
new bound evidence and a new actual decision; hashes alone are not approval.

Scoped revocation uses `privacy-artifact/v1 revoke <receiptId> <fullContextDigest>`
in the same stream, with a concrete resolved owner answer/date. Any observed
revoke for that receiptId is terminal, including a revoke naming an older context.
Renewal needs a new receiptId; changing evidence or appending another approval
cannot resurrect the old identity. Other-context approvals for the same identity
are refused as ambiguous. Malformed/unsupported artifact questions, unresolved
or nonowner scoped decisions fail closed, rather than being ignored. This
namespace is reserved for the documented approve/revoke grammar. Non-artifact
questions confer no artifact authority.

Every load reads current validated history through `records.load_work` and saves
the observed prefix count/checkpoint, canonical work/events digests, input-file
digests and UTC observation window. It accepts no caller-supplied detached
history as current state. Records can be appended after enumeration; there is
no atomic revocation guarantee extending into later use or token minting.
Future publication gates must read again. The coordinator must preserve the
source/evidence and respect the single authoritative stream rather than copying
old decisions into a replacement stream. These cooperative local records prove
bindings under that workflow, not who actually spoke or verifier independence.
The returned inputs do not complete a scan or grant publication permission.


### Normalizing an existing owner decision

An owner decision can already exist elsewhere: in another record stream, an
earlier work or a communication. Nothing copies or rewrites it. A coordinator
brings it in by **appending** one current `decision` event, with the exact
approval grammar above, to the one authority stream the receipt names
(`authorityWorkId`). A second stream would hide a revocation recorded in the
first, which is why decisions are never copied into a replacement stream:

- `question`: `privacy-artifact/v1 approve <receiptId> <fullContextDigest>`;
- `answer`: the exact quote of the original authority, unchanged;
- `decidedBy`: `owner`, only because the original words are the owner's own;
- `date`: the original source date, not today's. The event's `at` timestamp
  records the real append time.

The source snapshot's `sourceRef` locates the **original** authority, and its
`quote` and `date` equal the original's. Source evidence is not itself a receipt:
a quote, a locator or a copied decision confers nothing until a receipt binds it
to the exact inventory context and the approval event above exists in the stream.
Never edit or relabel a historical event, move an old decision under a new date,
or ask the owner to approve again what the owner already decided: record what
exists faithfully, and ask only when no authority exists. A coordinator's own
judgment, a prior publication and an implementation assignment are not owner
decisions.

### Preparation artifacts and the helper manifest

Preparation work can produce an inventory in a richer or different schema than
the closed helper manifest. Keep it as made: preserve its immutable bytes and
record their digest; never edit it into the helper's shape. Derive a **separate**
helper manifest in the closed schema (`schemaVersion`, `repository`,
`objectFormat`, `baseline`, and `entries` of exactly `path`, `mode`, `blobOid`,
`size`). The independent verification (`result: verified`) attests that the two
inventories hold exactly the same tuples, in both directions with no extra or
missing entry, and that they match the Git objects at the baseline. Both digests
are bound: the preparation artifact's SHA-256 is recorded with its locator in the
verification's `sourceRef`, and the helper manifest's raw digest is its
`inventoryDigest`, which the receipt, the full context and the approval event pin.
The derivation is mechanical: it neither expands nor replaces what the owner
approved. An entry the owner did not approve cannot be added; a changed inventory
needs a new receiptId and a new decision.

### Work epochs and required checks

A raw `privacy check` of a selection with approved-unscanned bytes exits 1 and
stays failed, so a work whose required check is that command cannot pass it.
Adopting an approved inventory changes the policy check, not the raw outcome:

- Keep every earlier `check` event as written. Never append a pass for the raw
  command, relabel an old event or record raw exit 1 as success.
- Require the exact policy check separately. Either start a new work epoch (a
  new work identifier from the current base) whose required checks name the exact
  `t3c privacy verify ...` invocation, or replace an existing work's required
  checks only with a `scope` event whose `decisionRef` names a resolved decision
  for that change. Evidence counts a check event only when it follows the
  replacement of a changed requirement, so earlier outcomes are never
  reinterpreted and the requirement cannot be edited silently.
- Record the verify check with the command text exactly as required, the final
  head, a real source and the log of that invocation. Exit 0 is a pass, exit 1 a
  fail and exit 2 refused or blocked. Keep the raw observation in its own log:
  it states what was and was not screened. The verify summary holds counts only;
  keep private bindings and logs out of public records.
- A blocked epoch and its evidence stay as they are.

The explicit inherited-artifact scanner now consumes this cooperative provenance
and validates actual baseline/BASE tuples and every selected opaque occurrence's
direct parents. Its versioned [raw coverage](cli/privacy.md#coverage-schema-artifact-coveragev1)
binds the observed checkpoint, work/events digests, source/verification and input
snapshots. Approved-unscanned bodies produce raw nonpass, never a passing raw
check. Do not record raw exit 1 as successful required-check evidence. The
[policy evaluator](cli/privacy.md#policy-evaluation-and-privacy-verify) reruns the
scanner and observes current authority again at its end; publication must observe
again at its gate. Saving coverage does not authorize an inventory or establish
authentication, verifier independence or atomic nonrevocation. No new event kind
or actual owner receipt is installed by this capability.

## Public-text standing-authority recipe

The [public-text policy](cli/privacy.md#public-text-dispositions-public-textv2)
consumes a standing owner decision that text already public by design may be
republished. It uses existing `decision` events and asks the owner for nothing new;
it never approves an opaque inventory, a credential or an unrelated match. Source
evidence, a locator, a coordinator's judgment, prior publication and an
implementation assignment are not authority. Tests use explicitly synthetic
decisions only. No real rule is installed by the capability.

1. **Normalize the decision.** Append one `decision` to the one dedicated authority
   stream the rule names (`authorityWorkId`): `question`
   `privacy-public-text/v1 approve <ruleId> <ruleContext>`; `answer` the exact quote of
   the original words; `decidedBy` `owner`, only because they are the owner's; `date`
   the original date (the event's `at` records the real append time). The rule
   document holds `source` (the original locator, date and quote), `scope` and the
   `fields` the owner's words cover. Compute `ruleContext` as `privacy_dispositions.digest`
   of the rule without `approvalSeq`, record the event, then set `approvalSeq` to its
   sequence. Never copy a decision into a second stream, edit or backdate one, or ask
   the owner to decide again what exists.
2. **Attest prior publication.** An independent observer records, with a concrete
   locator, that the pinned full SHA-1 `publicCommit` was actually public. A local
   object, a caller's SHA and a moving ref such as `origin/main` are not evidence.
3. **Review occurrences.** Evaluate privately in process: each unresolved verdict names
   its input by digest, aligned with `raw.findings`. For each input, take the exact
   occurrences (row, offset, length, literal digest, public blob and its content
   digest) from the internal literal-proof seam over the same captured list and
   `publicCommit`. Digests are secret-equivalent, so keep every file and log private.
   An independent reviewer examines each occurrence in its exact context and records
   its class. Only product text the owner's rule covers is
   `public-product-text`; record credentials, tokens, private paths, unrelated and
   ambiguous matches as exclusions, which stay unresolved. A pattern row, a path, a
   label or a flag never grants scope. The review is for one selection: the private
   result names its `selectionContext` and, per finding, the `context`; the
   dispositions bind the first and hold an entry per place. A new commit, another
   order or endpoint, a changed text, an unknown literal or a changed list needs a new
   review. That is fresh scope evidence under the same owner decision, never a new
   approval, and the join is by exact selection, place, input and occurrence.
4. **Revoke** with `privacy-public-text/v1 revoke <ruleId> <ruleContext>` in the same
   stream, a concrete resolved owner answer and date. It is terminal for the rule.
5. **Required checks.** As for [artifact work](#work-epochs-and-required-checks), the
   policy check is the exact `t3c privacy verify ...` with the same `--privacy-policy`
   in a new work epoch (or a `scope` event with a resolved decision); the raw
   `privacy check` stays failed evidence, never relabeled. A publication gate
   evaluates its own selection and metadata again and requires `policy_satisfied`.

The records are cooperative: they bind a decision, evidence and occurrences under this
workflow, not who spoke or who verified, and they give no atomic revocation. Observation
is a window; see the policy's [limits](cli/privacy.md#public-text-dispositions-public-textv2).

### Canonical verification checks

The [publisher helpers](cli/publish.md#canonical-verification-helpers)
use an exact token array for both `requiredChecks.command` and `check.command`,
including absolute normalized local/repository/policy paths, immutable work
`reviewBase..head`, intended branch and, for PRs, ordered title/body file labels.
A shell string, matching check ID, changed label or saved summary cannot replace
that array. Publishers construct it from captured invocation arguments.

The join loads actual current validated records and uses `effectiveWork`, scope
replacement sequence and current assignment provenance. It explicitly rechecks
selection's diagnostic fallback: exact head and command, trusted source, pass
and concrete evidence must all hold. The whole evidence comparison remains
binding; a privacy pass cannot clear another missing check, criterion or review.
Current schema validation already refuses empty or non-string check evidence.

Use the supported new-work or resolved-decision/scope migration above and record
a fresh applicable verification check after a changed requirement. Keep failed
raw checks and older passes unchanged; neither becomes verification evidence
through relabeling. Execute that exact command under the controlled publication
locale (`LC_ALL=C`) before recording a passing check; public-text policy evidence
must bind the same locale. A check row or private projection alone is no permission.


### Private policy observation boundary

The [observation projection](cli/publish.md#private-observation-projection)
binds a current process-private evaluation to captured selection/metadata and a
fresh identifier observation. Artifact-only v1, public-only v2 and combined v2
retain their distinct input shapes. A successful policy verdict preserves raw
exit 1 when findings or approved-unscanned bytes remain; it never repairs failed
raw check history. Neither this projection nor its stable policy binding is
check execution evidence, owner approval, a receipt or publication permission.

Its closed 23-field private dictionary contains only bounded counts/status/time,
immutable endpoints and selected digests; it cannot contain whole private facts,
findings, proofs, matched paths or owner quotes. Six-field authority observation
hashes include their nested times. Stable policy bindings exclude volatile
observation metadata, so unrelated allowed authority appends do not require a
new policy binding. The fresh list observation follows the evaluation window.
The [immutable writer](cli/publish.md#immutable-observation-writer)
independently validates that closed output and its canonical UTF-8 byte bound
before creating a private observation. Its only storage is the existing work's
`publication-policy/<op_id>.json`, under an owned 0700 parent with a new exclusive
0600 regular single-link file. It neither appends a record event nor installs
an actual receipt. Existing entries and unsafe ancestors refuse without repair.
Complete writes, file sync, exact readback, descriptor/name checks, file close and
directory sync precede success. Failed cleanup can retain partial bytes; late
close errors can retain complete bytes. Neither retained state is reused.
Complete fresh evaluation/authority/list and ordinary publication gates remain
required before any token mint. Cooperative provenance is not authentication;
the writer alone grants no policy permission or current revocation verdict.

### Separate canonical range and publication evidence

Explicit policy publication distinguishes the canonical CHECK
range (`reviewBase..head`, without PR texts for push) from the actual ordered
remote-excluded push set. Prepare separate public-text dispositions and pinned
policies when those contexts differ; exact selection and finding-place equality
remain required. Neither a subset nor a superset substitutes for the selection.
Artifact-only evidence may serve both selections when all its bindings apply.
PR preparation instead includes its ordered title/body metadata in the range.

Execute the actual canonical `privacy verify` token array before recording fresh
applicable check evidence. Synthetic check rows alone do not demonstrate command
execution. A successful policy command can retain `rawExit: 1`; keep failed raw
history and use the supported new-work/scope migration above. Independently
rerun the actual publication-set evaluator. Push supplies `--privacy-policy FILE`
and, for a different CHECK context, `--privacy-check-policy FILE`; the latter
requires the former. PR supplies one policy plus its actual ordered text files.
Absent flags remains strict. The [current-context seal](cli/publish.md#current-policy-context-seal)
observes BOTH contexts after ALL proofs, and again after storage. Latest ordinary
publication gates follow; later changes remain outside this sequential window.

Reuse the [work epoch recipe](#work-epochs-and-required-checks) and preparation
recipes above: preserve the original rich inventory and decision source, derive a
separate closed manifest with independently attested exact tuple equality BOTH
ways, and cite the original decision in a current normalized owner source. Never
rewrite, backdate or request the same approval again. Exact public-product scope
and prior publication remain necessary and insufficient for unrelated secrets.

### Lifecycle intent relation

The private [lifecycle composition](cli/publish.md#policy-lifecycle-composition)
requires a current actual intent supplied by its caller after preparation.
Publication and canonical CHECK observations use that intent's `opId` and
`opId-review`, respectively; PR has only the former. The suffix denotes storage
for the same intent/result, never a second event. Helpers append no result and
invent no child status. A caller must record the actual refused or uncertain
outcome when safe, retaining partial observations if storage failed. Retry needs
fresh preparation and a real new intent; old observations are neither overwritten
nor accepted as evidence of permission. Active publication handles these outcomes
through its single actual terminal-result boundary. An uncertain append is not
retried; storage failure blocks mint without inventing child/status facts.
