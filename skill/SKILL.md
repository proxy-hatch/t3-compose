---
name: t3-compose-manager
description: Operate the t3-compose work cycle from an Engineering manager or Feature lead thread in T3. Route and dispatch Workers and Reviewers through the routing matrix, keep t3c records from the first dispatch, review the final head, then publish and merge through t3c. Use when running or resuming t3-compose build work, or the work of a repository it manages.
---

# t3-compose manager skill

The Engineering manager and each assigned Feature lead use this skill with the
`t3c` helper. It states the operating rules the build runs under. The specs in
the helper checkout hold the details: `<root>/docs/routing-matrix.md`,
`<root>/docs/records.md`, `<root>/docs/cli.md` and `<root>/docs/cli/*.md`. When
this skill and a spec disagree, the spec wins; report the gap.

## Invocation

- The owner pins the interpreter once with `uv python pin 3.15 --global`.
  Every session then resolves it with `PY="$(uv python find 3.15)"`.
- `t3c` below abbreviates `"$PY" -I -S <root>/bin/t3c`, where `<root>` is the
  helper checkout (the owner's t3-compose checkout). Global options
  `--local-dir PATH` and `--text` come before the command. Both `-I` and `-S`
  are required; the entry point exits 2 without them.
- Never take evidence on a pre-release interpreter. The entry point refuses
  alpha, beta and candidate builds, and `t3c version` must show a final 3.15.x.
- `SUITE` is, from a checkout's root:
  `"$PY" -I -S -B -m unittest discover -s tests -t . -v`. One module:
  `... discover -s tests -t . -p 'test_x.py'`. A run leaves no `__pycache__`.
- Output is one JSON value on stdout. A failed check can still print data:
  `evidence check`, `skill status` and `config check` print their report with
  exit 1 or 3. A message-only failure prints nothing on stdout. `publish run`
  alone passes a child's exit status through. Exit codes: 0 success, 1 check
  failed, 2 invalid input or helper failure, 3 invalid config, 4 manager
  judgment needed, 5 report to Shawn, 75 busy.
- Shared core files (`t3compose/cli.py`, `jsonio.py`, `paths.py`, `process.py`,
  `tests/support.py`) and the module list change only when a brief assigns the
  edit. Each module owns its own `docs/cli/<module>.md`.

## Hard rules

1. The manager makes no project edits. It never moves or writes the owner's
   checkout. The exceptions are its records under `.local/state/`, the quota
   state the routing policy assigns to it, notes of Shawn's dated decisions, a
   plain `git fetch`, a push of a reviewed commit, `git pull --ff-only` on a
   clean `main` after a merge only when T3 has not already fast-forwarded it,
   and what Shawn explicitly asks for.
2. Workers commit on their branch only. Workers and Reviewers never push, open,
   edit or merge pull requests, never post to GitHub and never run `gh`. Tests
   never start an agent harness (`claude`, `codex`, `agy` or `pi`).
3. In t3-compose every Task has an assigned Feature lead. That lead alone
   publishes its Task's pull request as the Worker App and, after its own
   exact-final-head review and passed required checks, squash-merges it (plan
   decision 19). The manager does not merge, and no lead merges another Task's
   pull request. A managed repository keeps its own merge rules.
4. Tracked files, commit messages, branch names and GitHub text never hold a
   private identifier: the owner's email, keys or tokens, paths under the
   owner's configuration directories, provider-instance or account names, T3 or
   session IDs, or rapid-ship's task list. Write "recorded in `.local/`"
   instead. Screen everything before it is published.
5. Never read, print or pass a secret in argv. Pi's OpenRouter key stays in
   Pi's own configuration (plan decision 9): the helper never reads, passes or
   logs it, and briefs, source, logs and argv never hold it. A Keychain prompt
   gets Allow, never Always Allow.
6. rapid-ship work waits for the pilot. t3-compose agents do none: plan
   decision 16's temporary private setup runs in a separate rapid-ship-bound
   manager thread, and the formal pilot keeps its `t3c` and handoff gates. For
   managed repositories, Feature leads stay off until the package enables them;
   this does not change the t3-compose build's assigned leads.
7. These rules are instructions, not a security boundary: every child has full
   access. Check results; do not assume them.

## Roles

- The Engineering manager owns priorities across Tasks, the interfaces Tasks
  share, the routing config and policy, and starting Tasks and their leads (it
  records each `lead-assignment`). At a Task's start it sets the expected
  integration tests and machine prerequisites. After a merge it reads the
  checkout's HEAD, checks `main` and the machine, and starts dependent Tasks.
  It adds no pre-merge approval and does not repeat a lead's final-head review.
  Verified, PR-ready, merged and delivery-ready stay distinct states.
- A Feature lead runs one Task: briefs, dispatches, verification, records, the
  final-head review coordination and check evidence, publication and its own
  Task's merge. It makes no project edits and creates no leads or threads. It
  tells Shawn in its own thread when the pull request is ready, and puts the
  machine prerequisites and integration evidence in the pull request body. It
  sends the manager one short message only for its merge, a blocker outside its
  Task, a change that touches another Task's files or a shared interface, a new
  machine prerequisite, a rule change Shawn gave (quote him), or a stage-3 cell
  with no target that can serve. It does not report ordinary usage-limit
  refusals, agy's included: it records them with `t3c quota record` and takes
  the cell's next target. It never reports readiness or progress to the
  manager.
- With no lead in a managed repository, the manager owns the Task's
  coordination, checks, independent final-review coordination and publication,
  under that repository's rules.
- A Worker edits project files and commits on its branch. A Reviewer reads an
  exact base and head and reports findings.
- Design work is commissioned through the matrix like any other work. A change
  to scope or to an owner decision goes to Shawn before dependent work.

## Start and restart

At the start of every session, and after any restart or clear:

1. Confirm the thread is bound to the repository it coordinates. Each managed
   repository has one manager thread bound to a checkout of its own T3 project:
   T3 creates a worktree child in that project's repository from the thread's
   checkout HEAD, so a thread bound elsewhere cannot provision it, and a
   thread's workspace cannot be changed after creation.
2. Run `t3c version`, `t3c config check`, `t3c work list --repo REPO` and
   `t3c work reconcile --repo REPO`.
3. For each open item, read the facts from T3 (`task_status`, the thread list)
   and from Git (branches, worktrees, commits). Record the outcome as a
   `reconciliation` event. Start nothing new until every item is reconciled.
4. Rebuild decisions from the records, not from memory.

What L7 showed (2026-11-01):

- The Harness's own `/clear`, sent with `t3_thread_send`, clears a Claude
  session's conversation and keeps the thread. The cleared thread then reads
  its child with `task_status` once and still receives the completion wakes.
  The final result can arrive under a continuation node ID, not the original
  `taskId`: accept completions for the same child thread under any ID. A
  clear for Codex, Pi or agy sessions is untested.
- A provider switch with `t3_thread_configure` does not clear anything: the
  conversation crosses the switch, and a completion started before it still
  arrives. Use it as an in-place model failover that keeps context, for
  example the manager moving to its quota fallback.
- A repeated `delegate_task` with the same `clientRequestId` returns the
  existing task and starts no second child. Resolve an intent that has no
  `taskId` from T3 first (the thread list, or a search for the task title).
  Reissue the same call, with the same `clientRequestId` and brief, only when
  T3 shows no such task. Otherwise report to Shawn.

## Records from the first dispatch

Every Task runs in t3c records from its first dispatch. Details are in
`<root>/docs/cli/records.md`.

1. Create the work: `t3c work new --repo REPO --work ID --file work.json
   --writer W`. `W` is the canonical manager writer, `manager@` plus the
   repository identity. A work's review base is immutable: a Task that moves
   onto a newer `main` opens a new work epoch (`<ID>-main-<short sha>`).
2. A Task with an assigned Feature lead starts with the manager's
   `lead-assignment` as its first event, from the launch message: the real date,
   the lead's thread, the Task line as the reason. A lead never invents it. A
   Task without a lead (a managed repository before Feature leads are enabled)
   has no `lead-assignment`: the records hold no lead until one is assigned, so
   its first event is the manager's own. The manager coordinates it, and an
   independent Reviewer reviews its final head. Never record an assignment that
   did not happen.
3. Append events with `t3c event add --repo REPO --work ID --kind KIND --file
   DATA.json --writer W`. For each dispatch: `dispatch-intent` before the
   call, `dispatch-confirmed` with the `taskId`, then the genuine `result`.
   Then `check`, `criterion-evidence`, `review`, `finding-disposition` and
   `reconciliation` as the work needs them. The publish commands write
   `op-intent` and `op-result`.
4. Never backdate or rewrite an event. Earlier manual notes stay as artifacts,
   and dated history stays as written: add a correction beside it.
   A reviewer's result needs its own confirmed reviewer dispatch with its exact
   base and head.
5. `t3c work show` returns the validated history. `t3c work snapshot` returns
   its digest. A damaged history stays as it is: reconcile, never renumber.

## Routing

`<root>/docs/routing-matrix.md` holds the matrix; `<root>/docs/cli/routing.md`
and `<root>/docs/cli/quota.md` hold the commands. Its rules:

- A Task has a lane (backend-logic, ui-frontend or docs), a criticality (core
  or non-critical) and a scope (standard or complex). Jev answers these through
  `t3c route classify`; an uncertain answer takes the fixed stronger default
  per answer; a lead may record its own judgment with `t3c route judge`. The
  risk facts a Task declares apply floors that only raise the class. The docs
  lane is for a Task whose deliverable is mainly technical writing (`DOCS`; a
  mixed Task stays backend-logic). It exists only where the config lists a
  worker `docs` lane: without it a `DOCS` decision runs in backend-logic and the
  selection records `laneFallback`.
- A standard Task starts at stage 1 and a complex Task at stage 2, for the
  lead, the Workers and the Reviewers. Every new round starts at that stage.
- Always dispatch the cell's primary. Never estimate or pre-check quota.
- Quota failover is horizontal. Classify a failed child by its own response,
  and use the harness's label only when the child gave none. A usage limit, an
  exhausted quota, a credits error, or a rate limit with a retry time over 5
  minutes or none is a quota refusal: record it and dispatch the next target
  of the same cell at once. A transient error (a rate limit of 5 minutes or
  less, "at capacity", a 5xx) gets one retry on the same target after the
  stated wait, or after one minute when none is stated; then that target is
  unavailable for this dispatch. It is never recorded as quota. An environment
  block makes a target unavailable for this dispatch and is not quota. A
  failure inside the work is not a failover.
- A recorded refusal keeps its provider instance or pool account and model out
  of use for 30 minutes. A reported reset time is recorded, never used.
- A pool walk starts at the active account and wraps once; an account that
  does not offer the model, or has a live refusal for it, is skipped. A refusal
  by the active account moves the active pointer to the next account. Walking
  accounts is not a fallback. When no target of a cell can serve,
  `route select` exits 5 with the earliest expiry: wait. A dry cell never
  escalates to another stage. At stage 3 the item waits and the lead tells the
  manager, who reports to Shawn.
- Role blocks make a driver kind unavailable for a role, with recorded reason
  and evidence. A target that fails a capability probe for a role is not used.
- GLM (metered, per token) is a last-resort quota fallback only. It is never a
  primary, a lead, a pre-pass or a helper, and its thinking is low or high,
  never max. Record `billing: per-token` for each use and list every GLM use
  in the merge line. The owner's target for the metered key is about $20 a
  month. A credit cap of that size on the key, recommended to the owner, is the
  hard guarantee; placement alone only lowers the rate. At the cap the provider
  answers 402, a quota refusal. Where GLM is a cell's last fallback, a GLM
  refusal leaves the cell dry: exit 5, and the work waits for the earliest
  remembered refusal to expire. Pi may never be the manager, and a driver kind
  other than `claudeAgent`, `codex`, `antigravity` and `pi` is unavailable. The
  owner is also advised that the key should allow no training or retention.
- A core Task's stage-1 review starts with the pre-pass: advisory, no
  fallback, skipped when unavailable (record the skip and its reason in the
  lead's log), never the review of record. Select it
  with `route select --role reviewer --stage 1 --pass pre`: only the cell's
  optional pre-pass target is tried. When none is configured or eligible, the
  command exits 0 with `{skipped: true, reason, retryAfter, targetsTried}` and
  writes nothing; go on to the review of record.
- Helpers: `route select --role verifier|text` needs a current input but no
  decision, takes no stage, round or pass, and walks the helper's primary and
  then its quota fallbacks, like a matrix cell.

To route one dispatch:

1. Start an escalation epoch, which is one issue or round, in exactly one of
   two ways.
   (a) `t3c route input --repo REPO --work ID --input TASK.json` records the
   input and returns `inputRef`, `inputDigest` and `policyDigest`; then record
   your own decision with `t3c route judge --repo REPO --work ID --input-ref SEQ
   --policy-digest DIGEST --lane L --criticality C --scope S --reason TEXT`.
   (b) `t3c route classify --repo REPO --work ID --input TASK.json` alone. It
   records its own input and Jev's decision, and returns the `inputRef` and
   `policyDigest`. Exit 4 offers one retry with `--context EXTRA.json`, or
   `--decline-retry` to take the fixed per-answer defaults.
   Never run `route input` and then `route classify` for the same task and
   policy: classify then exits 2 as an unclassified input and appends no second
   input. Use the epoch's `inputRef` and `policyDigest` for every later
   `route select` and `route escalate`. A changed policy needs a new input.
2. Use the thread's saved catalog. T3 allows `orchestrator_capabilities` once
   per thread, so capture it once, save the exact tool output to a file with its
   true UTC capture time, and reuse that file for the Task. Capture again only
   after a dispatch error that suggests a provider change: a missing provider
   or model, a disabled instance or a changed option. A file and its time
   always come from the same call; never relabel a file.
3. `t3c route select --repo REPO --work ID --role worker|reviewer|feature-lead
   --stage N [--round first|corrections|re-review] [--pass pre|record]
   --input-ref SEQ --policy-digest DIGEST --catalog FILE --catalog-read-at UTC`,
   where the time
   is the file's capture time and the catalog is at most 24 hours old. It walks
   the cell once, primary first, skips unavailable targets and live refusals,
   appends a `routing-selection` and returns the instance, model and options to
   dispatch. Exit 4 asks for a current input, decision or catalog; it reports
   `startingStage` for a stage below the start and `reachedStage` for a stage
   above the recorded escalation chain. Exit 5 means the cell is dry: the JSON
   gives `retryAfter`; wait and select again.
4. On a quota refusal record it with `t3c quota record --instance I --model M
   --kind usage-limit|quota-exhausted|credits|rate-limit --observed-at T
   [--reset-at T] --evidence TEXT`. A refusing active pool account moves the
   pool's active pointer. Then clear the refused dispatch as set out under
   Escalation, and run `route select` again.

A new issue or round starts a new epoch the same way, (a) with a `route judge`
reason such as "carries the Task's class into round N" or (b), and so restarts
at the starting stage. Escalations recorded after that
input, with the same role and lane, chained stage by stage from the starting
stage, allow a higher `--stage`; `route select` needs that chain above the
starting stage, and escalations recorded before the newest input do not count.

## Dispatch

1. Read the checkout's actual HEAD at every dispatch, even after T3
   fast-forwards it. Fetch with a plain `git fetch` before writing a brief
   when the base is not local. Keep the Task current with `main`: when `main`
   has gained merges since the review base, start the next round from the new
   `main` (a new work epoch) and carry the unmerged commits over, before taking
   final evidence.
2. A brief names three commits: the provisioned commit (the dispatching
   checkout's HEAD), the start commit and the review base. Its exact
   `Move-rule: three-case-v1` block tells a Worker to continue when HEAD equals
   the start commit; to move its own branch to the start commit only when HEAD
   equals the provisioned commit, the tree is clean, the start commit exists and
   the branch is its fresh own branch; and otherwise to stop and report, never
   fetching a missing start commit. A Reviewer that would need a move stops for
   the coordinator to reprovision.
3. Render each brief from `<root>/skill/templates/` with `t3c brief render
   --template worker|reviewer|reviewer-prepass|continuation --values
   VALUES.json --out FILE` and check it with `t3c brief check FILE`. The digest
   they print is the intent's `briefDigest`. The header holds `Stage` (1 to 3),
   a `Round` (Worker: implementation, corrections or continuation; Reviewer:
   earlier-review, corrections-review or final-review) and, for a Reviewer, a
   `Mode` (record or pre-pass). `PROVIDER` names the harness family (agy, pi,
   claude or codex), never an account. A brief for an agy child, and for a Pi
   child until Pi's instruction-file handling is verified (plan decision 9),
   carries every rule inline.
4. Record `dispatch-intent` with `--checkout PATH` (the dispatching checkout:
   the store checks that its HEAD equals the provisioned commit and that the
   start commit exists) and the instance, model and options `route select`
   returned as its `selection`. Then call `delegate_task` with
   `isolation: "worktree"`, `mode: "async"`, that same `target` and the
   `clientRequestId` `t3c-<work>-<role>-r<round>`, which the task title repeats.
5. Check the response against the brief at once: provider, model, options. Check
   that a new worktree on a new branch exists and that the owner's checkout is
   clean. Cancel a child that landed wrong within seconds with `task_cancel`.
   After three failed dispatches in a row, stop and tell the manager.
6. Record `dispatch-confirmed`. Do not poll a running child; its completion
   wakes the dispatching thread. Only the parent that created a child manages it.
7. On completion read the result with `task_status` and check it in the
   child's worktree: HEAD and parent, a clean status, a diff that names only
   the owned paths, and the expected hashes and message; `git diff --check` prints
   nothing and no `__pycache__` remains. A completed turn does not prove the
   brief was met. Record the `result`.
8. Exact-text rounds: prepare the edit in a scratch copy, give the expected
   SHA-256 and tree, and compare after the round.

## Escalation

- Escalation is vertical and per issue. A Worker stops after two failed
  repairs: it commits its work in progress as `wip(escalation): <summary>` and
  returns only an `<ESCALATION_REQUEST>` block (exact fields in
  `<root>/docs/cli/briefs.md`). An environment block and a spec question
  are not attempts. Triggers, with the `--reason` that records each: a Worker
  hard stop (`worker-hard-stop`); a finding still open after two Corrections
  rounds (`finding-unresolved`, a Worker); a review deadlock (`review-deadlock`,
  a Reviewer; a next-stage reviewer decides); two non-quota failures of a
  reviewer (`reviewer-failure`, a Reviewer); a stalled lead (`lead-stalled`, a
  Feature lead: a second issue needs stage 3, or a merge or post-merge check
  fails twice). Quota exhaustion never authorizes escalation. The manager
  replaces a stalled lead's thread at the next stage; the new lead resumes from
  the Task log and the records, so keep both current. When stage 3 fails the
  item goes to the manager, then to Shawn.
- Record a trigger before selecting the next stage: `t3c route escalate --repo
  REPO --work ID --input-ref SEQ --policy-digest DIGEST --role
  worker|reviewer|feature-lead --from-stage 1|2 --reason REASON --evidence-file
  FILE [--wip-commit SHA]`. `--from-stage` must be the stage the epoch has
  reached; the command derives the next stage, and there is no stage 4. The
  evidence is at most 8 KiB of text. For `worker-hard-stop` it holds the
  `<ESCALATION_REQUEST>` block, and `--wip-commit` is required and must match
  the block.
- On an `<ESCALATION_REQUEST>` the lead checks the returned block with `t3c
  brief check-escalation --result FILE`, confirms in the Worker's worktree (read
  only) that the WIP commit is the branch head with the subject
  `wip(escalation): ...` and a clean tree, records the escalation and selects
  the next stage. It then renders a continuation brief with `t3c brief render
  --template continuation --original-brief FILE --escalation FILE ...`. That
  brief embeds the original brief (the round's first Worker brief, never a
  continuation brief) byte for byte and the validated block, sets the start
  commit to the WIP commit, and tells the new Worker to inspect `git diff
  <review base>..HEAD`, judge the earlier attempts, fix the root cause or revert
  the broken changes, and get the validation passing. It never passes the
  failed Worker's transcript. The lead dispatches it on the next stage's
  primary.
- The step-by-step form of these rules is `<root>/skill/templates/lead-protocol.md`.
  The manager renders it into a lead's launch message with `t3c brief render
  --template lead-protocol --values VALUES.json --out FILE`; its one value,
  `LEAD_LOG`, names the log where the lead writes prose records. It is
  render-only: `t3c brief check` refuses it.
- On a quota refusal the lead continues in four steps:
  1. Record the refusal with `t3c quota record`.
  2. Clear the refused dispatch with a stopped-and-preserved `reconciliation`.
     The store allows no new Worker intent while a dispatch is unresolved.
  3. Select again and dispatch the cell's next target with the same brief and
     a new `clientRequestId` (a Worker's round number rises by one). Start at
     the round's previous start commit, or at the latest completed result's
     commit. The store refuses any other start commit.
  4. The refused child's commits, if any, stay on its branch. The new brief may
     name that branch for read-only reference.
  A refusal almost always comes at dispatch start, before any work, and Workers
  commit once, at the end.

## Reviews and the final head

- Every earlier-head and Corrections review is independent and is a new
  delegated task with the brief, prior findings, responses and unresolved
  objections; never message an old child. Reviewers edit and push nothing.
  Findings go to a Corrections round with exact text, at the Task's starting
  stage, then a scoped re-review.
- A core review at stage 1 runs in this order: the pre-pass (Mode `pre-pass`,
  findings `PP-<n>`); a Corrections round if it found blockers; the review of
  record (Mode `record`) on the corrected head. The review of record never sees
  the pre-pass findings, and `t3c brief check` refuses a review-of-record brief
  that carries a `PP-<n>` ID.
- The final head. On a core Task the review of record reviews the exact final
  head, and the lead then checks its result and records its own review last.
  On a non-critical Task the lead's own review stands. A Task with no recorded
  routing decision counts as core; never invent a decision to skip the
  independent review. An unled Task needs an independent final
  review. That review's brief has Round `final-review`. The lead never edits
  project files, so its review does not approve its own implementation.
- Evidence order for a core Task, at the exact final head: the required checks
  and a `criterion-evidence` event for each acceptance criterion; the
  independent `review` event (reviewer kind `task`); then the lead's `review`
  event (kind `lead`) at the same base and head. Before it reviews, the lead
  holds the requirements, design, interfaces, diff, callers, tests and evidence.
  The lead's review covers the final diff, the review's verdict, a disposition for every finding of every
  review and the required checks, and says so. It never claims more than the
  lead checked. A newer task `review` needs another lead check afterward.
- A task `review` names the confirmed native Reviewer task; a lead `review`
  names the assigned lead's thread, never an invented task ID. Retain each
  report and record its evidence path and SHA-256 digest. On a core Task the
  lead's report names the review of record by event sequence and native task ID.
- Record a `finding-disposition` for every finding, keyed by the review's event
  sequence and the finding ID. Only a minor finding may be declined, with a
  reason, proof and covering test. A fix that changes HEAD needs a new Worker
  result, fresh checks and a fresh exact-head review.
- `t3c evidence check --repo REPO --work ID --head SHA` must print
  `"verified":true` with no gaps before anything is published.
- A dispute that a reviewer raises again goes to a reviewer at the next stage.
  Unresolved disputes and product decisions go to Shawn.

## Required checks

- Workers run the fast checks their brief names. The lead runs the required
  checks on the final head: relative links; `t3c privacy check --repo-path P
  --range BASE..HEAD --branch NAME --text-file title=F --text-file body=F`,
  which must print `"ok":true` with no findings (a finding's `lines` are lines
  of the private identifier list, not of the checked text); and one SUITE.
- Every full SUITE run goes through the machine-wide lock, with this command:
  `"$PY" -I -S -c 'import fcntl,subprocess,sys; f=open("/tmp/t3c-suite.lock","a");
  fcntl.flock(f,fcntl.LOCK_EX); sys.exit(subprocess.call(sys.argv[1:],timeout=1500))'
  "$PY" -I -S -B -m unittest discover -s tests -t .`. It waits for the lock and
  gives up after 25 minutes. A Worker runs it once before reporting; the lead
  once on the final head; the manager once after the merge. Reviewers and
  docs-only Workers run no full SUITE. A module run (`-p 'test_x.py'`) takes no
  lock. A timing failure outside your files: re-run that module alone and keep
  both results. Never record a failed full run as a pass.
- A repository's own heavy required check runs through `t3c heavy run`, one at
  a time on the whole machine, in a clean linked worktree at the exact head,
  after the writer has finished. The lead runs it, or assigns one Session at a
  time; with no lead the manager does. `t3c heavy status` shows the lock. Keep
  the completion summary: `label`, `commit`, `log`, `childStatus`,
  `headUnchanged`, `treeUnchanged`, `startedAt` and `endedAt`. A pass needs
  `childStatus` 0 and both freshness flags true. Exit 1 keeps the failure data;
  exit 75 means busy and nothing was launched. The command appends no work
  event, so the coordinator records the `check` event and its evidence.
- Record each check as a `check` event whose `command` text equals the work's
  `requiredChecks` entry, at the head it covered.

## Publication and merge

On t3-compose the assigned lead publishes and merges through t3c. `publish
push` and `publish pr` grant no merge authority: the merge rests on plan
decision 19 and step 4. In another repository, use that repository's own
publication commands with a recorded `op-intent` and `op-result`, then `t3c pr
verify`; its own merge rules apply. Publication needs stable CPython 3.15
through uv, Git, `gh`, the configured App and its Keychain item,
`/usr/bin/security`, `/usr/bin/openssl` and HTTPS.

1. Preconditions: the exact head approved, the required checks and the
   evidence order complete, `t3c evidence check` verified, and the
   branch name, commit messages, pull request title and body privacy-clean. Fetch
   any advertised objects the checkout lacks, with a plain `git fetch` outside
   the helper.
2. `t3c publish push --repo-path P --work ID --head SHA --branch NAME` pushes
   the reviewed commit by SHA as the Worker App. Confirm with
   `git ls-remote`. Then, from the Task's checkout, `t3c publish pr --work ID --head SHA
   --branch NAME --base main --title-file F --body-file F` creates or reuses
   the pull request. `t3c evidence check ... --pr-ready` must show no gaps. Link the
   returned URL at once with `link_pull_request`. `t3c pr verify` checks any
   pull request's head, base and author.
3. The pull request body carries the evidence, the machine prerequisites, how to
   run any step that needs Shawn, and every GLM use. It says truthfully who
   wrote it and who opened it. `t3c publish check` mints and revokes a token to
   test the App setup.
4. Merge only your own Task, only when all of these hold: the exact approved
   head, passed required checks and hosted checks if any, the owned paths only,
   no change under `.github/workflows/`, no open owner question, and a clean,
   mergeable, open, non-draft pull request authored by the App. Then run, from
   the repository: `t3c publish run --status-file FILE -- gh pr merge N --repo
   OWNER/NAME --squash --match-head-commit SHA`. Supply no title, body, author
   email or admin bypass: GitHub generates the squash message, and plan decision
   7's generated trailer is the only exception. `publish run` needs a new
   `--status-file`; read it afterwards. If `run` cannot express a rule, stop and
   tell the manager; do not merge another way.
5. Verify: exit 0, child status 0, revocation ok, the App as merger, `origin/main`
   containing the merge, and the reviewed delta exact (compare trees when
   `main` did not move, else the merge's parent range with the reviewed one).
   Scan the merge message; a `Co-authored-by` trailer GitHub generated is allowed.
6. Send the manager one line: the pull request and merge commit, exit codes, the
   exact remote and pull request heads, the App identity, revocations and
   warnings, and the Task's GLM uses. The manager then checks `main` and the
   machine and starts dependent Tasks. A lead sends no progress reports and
   never reports readiness to the manager.
7. A step that fails for a reason outside your control: stop, keep the artifacts,
   and report the exact failed step. Never force a push, weaken a lease,
   duplicate a pull request, or use personal GitHub credentials. A privacy
   match goes to a Corrections round (unpublished text; its Worker brief names
   `PRIVACY_FINDINGS` and `UNPUBLISHED_RANGE`) or to Shawn (published); a match in the coordinator's own text (the title or
   body) is fixed by the coordinator. Read-only `gh` reads are allowed; every
   write goes through t3c. A status that is unknown or true-null: keep the
   artifacts and reconcile before retrying. A revocation failure: keep and
   report the status and its warning. An unsafe Git setting: repair the named
   setting and never expose its value. A workflow-file change, or exit 5, goes
   to Shawn.

## Updates and installation

- The installed version is the owner's checkout. `t3c version` shows which
  commit runs. After a merge, read the checkout's HEAD first; run
  `git pull --ff-only` only when T3 has not already fast-forwarded a clean
  `main`. A dirty, divergent or non-`main` checkout goes to Shawn.
- `t3c skill install --skills-dir DIR` links this directory into a skills
  directory; `t3c skill status --skills-dir DIR` reports the link. Installing
  writes outside `.local/`, so it waits for Shawn's explicit request.

## Command reference

Every registered command, once. Specs: `<root>/docs/cli/*.md`.

| Command | Use |
|---|---|
| `t3c version` | Helper commit, dirty flag, interpreter and root. |
| `t3c config check` | Validate the routing config and quota state; print digests. |
| `t3c work new` | Create a work from its `work.json`. |
| `t3c work show` | A work's metadata and complete history. |
| `t3c work list` | A repository's works, and any damaged ones. |
| `t3c work snapshot` | The digest of a work's history. |
| `t3c work reconcile` | Read-only inventory of uncertain dispatches and operations. |
| `t3c work repair` | Remove proven leftover temporary files. |
| `t3c event add` | Append one validated event to a work. |
| `t3c evidence check` | Compare a head's recorded evidence; `--pr-ready` adds the publication pair. |
| `t3c route input` | Record a Task's routing input; returns the input reference and policy digest. |
| `t3c route classify` | Ask Jev the lane, criticality and scope questions; record the answer. |
| `t3c route judge` | Record a manual lane, criticality and scope judgment. |
| `t3c route select` | Select the matrix target for a role, stage and round, a reviewer's pre-pass or a helper; exit 5 when the cell is dry. |
| `t3c route escalate` | Record one escalation: role, stages, reason, evidence and WIP commit. |
| `t3c quota show` | The quota state and the live refusals with their expiry. |
| `t3c quota record` | Remember a quota refusal for an instance or account and model (30 minutes). |
| `t3c quota activate` | Make a pool account the active one, with a reason. |
| `t3c brief render` | Render a Worker, Reviewer, pre-pass or continuation brief; prints its digest. |
| `t3c brief check` | Check a rendered brief against its template's rules; prints its digest. |
| `t3c brief check-escalation` | Validate a Worker's `<ESCALATION_REQUEST>` block; prints its fields and digest. |
| `t3c privacy check` | Screen a commit range, branch and texts against the identifier list. |
| `t3c privacy verify` | Evaluate an explicit privacy policy; prints policySatisfied and counts, never a clean claim. |
| `t3c publish check` | Mint and revoke a token to test the App setup. |
| `t3c publish push` | Push the reviewed commit as the Worker App. |
| `t3c publish pr` | Create or reuse the pull request and verify it. |
| `t3c publish run` | Run a command with a short-lived App token; needs `--status-file`. |
| `t3c pr verify` | Verify a pull request's head, base and author. |
| `t3c heavy run` | Run a heavy check exclusively in a clean worktree at an exact head. |
| `t3c heavy status` | Show the heavy-check lock and its holder. |
| `t3c skill install` | Link this skill into a skills directory. |
| `t3c skill status` | Report whether the link is installed. |
