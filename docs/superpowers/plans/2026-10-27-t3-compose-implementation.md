# t3-compose implementation plan

Status: proposed on 2026-10-27, revised on 2026-10-28 to record decisions 1–19
and shared contracts CD-1 to CD-4. It follows the design revision in pull request
#1 at `906fdb0` and the owner decisions recorded below. The revision takes effect
when its pull request merges. Implementation and live validation remain
pending.

Terminology: [project glossary][glossary]. A Task is one pull request; an
Assignment is one delegated piece of work. Each Task cites the sections of the
[design][design] that it implements.

## Goal

Build t3-compose from an empty package to an installed manager skill, then hand
the pilot to the rapid-ship-bound manager thread. The Engineering manager gets a
tested helper, brief templates and a skill, so that every Assignment follows the
design's rules and leaves its evidence on record.

## Architecture

A Python standard-library helper, `t3c`, runs by absolute path from the
t3-compose checkout. It validates personal config, sends one bounded Jev request
at a time, computes routing selections, appends records under `.local/state/`,
checks that evidence covers the current commit, checks publishable text against
the private identifier list, mints the Worker App token for t3-compose, and
serializes heavy checks with a machine-wide lock. It launches no agents and
polls nothing: the manager calls T3's tools itself and gives their results to
the helper. A user-level skill tells the manager which command each step of the
work cycle uses. Brief templates carry the rules into each Assignment.

## Constraints

- Stable CPython 3.15, selected through uv's global pin; the runtime floor is
  3.15, not the latest patch number. Standard library only. The helper launches
  other programs only through the injectable `t3compose.process` module:
  `git`, `gh`, `grep`, `openssl`,
  `/usr/bin/security`, and the command given to `t3c heavy run` or
  `t3c publish run`.
- Setup: `uv python pin 3.15 --global`. Resolve the one interpreter with
  `PY="$(uv python find 3.15)"`; invoke `"$PY" -I -S <root>/bin/t3c version`.
  The helper refuses missing isolation flags, versions below 3.15 and any
  pre-release (`sys.version_info.releaselevel != "final"`) with exit 2 in the
  existing message form. Python child tests keep `sys.executable`. A newer
  stable minor requires Shawn's pin and a Task raising the floor and commands;
  no runtime network lookup discovers releases (decision 14, CD-3).
- Records, personal config and runtime state stay in Git-ignored `.local/`.
  Nothing is added to a managed repository.
- Tracked files, commit messages and pull request text hold no private
  identifier. Test fixtures use synthetic values that do not match the private
  identifier list; T3 task IDs, worktree paths and T3 branch names need a
  synthetic form.
- Workers commit on their branch only. Workers and Reviewers do not push, open,
  edit or merge pull requests, or post to GitHub. An assigned Feature lead
  publishes its own Task's pull request; the manager publishes Tasks without
  a lead in managed repositories. Every t3-compose Task has a lead, who
  squash-merges only its own Task's pull request as the Worker App after its
  exact-final-head review and passed required checks, subject to decision 19's
  merge conditions. The manager does not merge t3-compose pull requests. A
  managed repository's own rules decide who merges; rapid-ship's flight mode
  lets agents approve and merge pull requests.
- The helper's checks are instructional. They create no security boundary
  ([Checks and publication][d-publication]).
- The original rapid-ship hold ([re-home decision 2][d-rehome]) has a dated
  exception in decision 16: a separate rapid-ship-bound manager may use a
  private prose setup while `t3c` is built, reconciling old Claude work first
  and dispatching nothing without Shawn's approval. t3-compose agents read
  and run nothing in rapid-ship. Formal L9 and its handoff gates still wait
  for `t3c`; the temporary setup is no formal pilot pass.
- The manager never moves or writes the owner's checkout beyond the design's
  list ([Manager threads and bases][d-bases]). After a merge, the manager reads
  the actual checkout HEAD. It may run `git pull --ff-only` on the owner's
  clean `main` only when T3 has not already fast-forwarded it
  ([decision 2](#decisions)).

## Conventions for every Task

- **Base.** A Task starts from `origin/main` after its dependencies have merged.
  When the manager uses another start, such as a stacked parent's head, it
  records the start and the reason.
- **Size.** Aim for 300 to 1,200 changed lines, tests included. One reviewing
  Session must read the whole diff with its callers and tests and run the suite:
  the current lead for a led final head, an independent Reviewer otherwise.
  The interim token step's first review covered about 2,100 lines, tests
  included, in one Session; 1,200 leaves a margin. A Task that would exceed
  about 1,200 lines splits where its section says. HS-1 / PR #4 alone may
  exceed 1,200 lines, with no second HS Task (decision 15). A port of
  already-reviewed code (GA-1 to GA-3) may reach about 1,400 lines, because its Reviewer compares it with the
  reviewed source.
- **Files.** A Task changes only the files it lists. Tasks that may run in
  parallel share no file. HS-1 establishes `cli.py`, `jsonio.py`, `paths.py`,
  `process.py` and `tests/support.py`. Later changes to these shared files,
  including appending to `cli.py`'s `MODULES`, require explicit owned-path edits
  in a manager-assigned brief. Across the sequential records-store split
  ([decision 18](#decisions)), `RC-1a` owns the initial `docs/records.md`,
  `t3compose/schema.py` and `tests/test_records.py`; `RC-1b` retains the assigned
  `jsonio.py` and `tests/test_jsonio.py` changes, creates `t3compose/records.py`
  and `docs/cli/records.md`, and extends `tests/test_records.py` and
  `docs/records.md`; and `RC-1c` extends `t3compose/records.py`,
  `docs/records.md`, `docs/cli/records.md` and `tests/test_records.py` and
  creates `tests/test_reconcile.py`. Each command module has its own spec file.
  Tasks with overlapping assigned shared-file edits run sequentially.
- **Tests.** After `PY="$(uv python find 3.15)"`, confirm a final 3.15.x release
  before taking evidence. `SUITE` means
  `"$PY" -I -S -B -m unittest discover -s tests -t . -v`, run from the repository
  root; `SUITE -p 'test_x.py'` runs one file. This is one interpreter, with no
  system/Homebrew compatibility matrix. Every test class derives from the offline guard in `tests/support.py` and is named
  `Offline…`, or `Simulated…` when it injects quota responses. Tests cover
  failure and refusal paths as well as success.
- **Specs.** `docs/cli.md` holds what every command shares: invocation,
  global options, output, exit codes and the module contract. A Task that adds
  or changes a command documents its options, output and exit codes in
  `docs/cli/<module>.md`, one file per command module. A Task that changes the
  test harness documents it in `docs/testing.md`. The Reviewer checks the
  specs against the code ([decision 10](#decisions)).
- **Live checks.** Live checks sit in `live/`, outside the suite, print `LIVE`
  and run only when the manager runs them after review. Synthetic inputs are
  labeled synthetic. No offline or simulated test counts as a live pass. Live
  quota failover stays unverified until a real exhaustion event.
- **Routing.** Each Task names its risk facts. A floor applies to what the
  Task changes, not what it mentions. First implementations of code that
  handles credentials or enforces a guardrail, and recovery or concurrency
  invariants, go to the challenging tier. Shared interface changes go to the
  medium tier. Documents go to the medium tier; exact-text, mechanical edits
  can be carved out for the easy tier. Technical documentation of a software
  project counts as software engineering. The config decides the model for
  each tier. The manager confirms the route at dispatch and records its class
  and reason; until the router is live, it routes in prose. Corrections rounds
  with enumerated findings use the easy tier, even when a floor set the first
  round's tier. Their scoped re-reviews are medium, as are document reviews.
  First reviews of challenging code follow the Task's tier; other first
  reviews are at least medium. agy is never a Reviewer. The config holds these
  round tiers. DOC-1 aligns the design with these owner decisions
  ([decisions 1, 5 and 6](#decisions)). Tuning the tiers is outside this plan
  ([decision 8](#decisions)).
- **Records.** Until the records store is installed through RC-1c (after RC-1a
  and RC-1b), the manager keeps its manual dispatch log in `.local/`. From then
  on it records through the helper. The manual log stays as history and is not
  migrated.
- **Review and delivery.** The current assigned lead personally reviews the
  exact final head with requirements, design, interfaces, final diff, callers,
  tests and evidence in context; no separate Reviewer reviews that head.
  Earlier-head reviews and Corrections re-reviews remain independent. Without
  a lead, an independent Reviewer clears the final head. Coordinators make no
  project edits. The lead owns required checks, including heavy checks; the
  manager does so for Tasks without a lead. Under decision 17, a lead puts the
  machine prerequisites and integration evidence in the pull request body, tells
  Shawn in its own thread when the pull request is ready, never reports pull
  request readiness to the manager, and sends no progress reports; it sends the
  manager one line when its pull request merges, plus blockers, cross-Task or
  shared-interface changes, new machine prerequisites, owner rule changes and
  agy exhaustion. The manager owns cross-feature integration, operational
  readiness, integration tests and final delivery readiness to Shawn—setting the
  expected integration tests and machine prerequisites at Task start and
  checking `main` and the machine after a merge—without adding a manager
  pre-merge readiness approval or repeating the lead's final review. Verified,
  PR-ready, merged and delivery-ready remain distinct; delivery readiness is
  not an EV-1 input (decisions 13, 17 and 19, CD-4). In t3-compose, the lead
  verifies its authorized own-Task squash merge and sends that one-line
  handoff; the manager checks `main` and the machine and starts dependent
  Tasks and leads after dependencies merge, without waiting for Shawn.
- **Publication.** The assigned lead publishes its own Task's pull request;
  the manager publishes Tasks without a lead. Until PB-1 is installed, that
  coordinator uses the interim token step after confirming its reviewed hash.
  The pinned tool remains unchanged on its reviewed interpreter outside the
  helper, and retires after L5. The first publication through the helper is L5.
  PB-1 supplies push and pull request commands only; the assigned t3-compose lead subsequently merges
  under decision 19, with no new helper merge command.
- **Copied code.** Compose and Firstmate are references. A Worker who copies
  their code keeps the upstream MIT notice in the copied file's header and
  reports it ([publication decision 3][d-pubdec]).

## Contracts

This plan fixes the helper's layout, commands, record format and config schema,
including shared contracts CD-2, CD-3 and CD-4. A Worker who needs to change a
contract reports it; the manager decides.

### Layout

| Path | Contents | Tasks |
|---|---|---|
| `bin/t3c` | entry point | HS-1 |
| `t3compose/` | one module per concern | each Task |
| `tests/` | offline suite | each Task |
| `live/` | live checks | JV-1, RT-1, RP-1 |
| `skill/SKILL.md`, `skill/templates/` | manager skill and brief templates | SK-1, BT-1, PH-1, FL-1 |
| `docs/` | `docs/cli.md` and `docs/testing.md`; `docs/cli/<module>.md`, one per command module; configuration, record, restart and pilot contracts | HS-1; each Task that adds commands; CF-1, RC-1a, RC-1b, RC-1c, PH-1, RP-1, FL-1 |
| `pilot/` | synthetic router examples | RP-1 |

### Commands

`t3c` below abbreviates `"$PY" -I -S <root>/bin/t3c`, after resolving the final
CPython 3.15 interpreter as above. This applies to briefs and skill commands too.

| Command | Purpose | Task |
|---|---|---|
| `t3c version` | root, commit, dirty flag, Python version | HS-1 |
| `t3c config check` | validate the routing config and quota state; print the digest | CF-1 |
| `t3c work new`, `show`, `list`, `snapshot`, `reconcile`, `repair` | work records (`new`, `show`, `list`), checkpoints, reconciliation and repair (`snapshot`, `reconcile`, `repair`) | RC-1b (`new`, `show`, `list`), RC-1c (`snapshot`, `reconcile`, `repair`) |
| `t3c event add` | append one validated event | RC-1b |
| `t3c evidence check` | `verified`, or the gaps, for one head | EV-1 |
| `t3c route classify` | one bounded Jev request | JV-1 |
| `t3c route input`, `judge`, `select` | routing input; manager judgment; final tier and selection | RT-1 |
| `t3c quota show`, `record`, `activate` | agy quota state | RT-1 |
| `t3c privacy check` | the identifier list against messages, trees, branch name and pull request text | PV-1b |
| `t3c pr verify` | head, base, state and author of a pull request | PV-1b |
| `t3c publish check` | mint a token, verify its scope, revoke it | GA-1 |
| `t3c publish run --status-file FILE -- CMD` | run one command with the App token | GA-2, GA-3 |
| `t3c publish push`, `pr` | the t3-compose publication flow | PB-1 |
| `t3c heavy run`, `status` | the machine-wide heavy-check lock | HC-1 |
| `t3c brief render`, `check` | fill and check brief templates | BT-1 |
| `t3c skill install`, `status` | the user-level symlink | SK-1 |

Output is one JSON value on stdout unless `--text` is given, including when
an `Outcome` carries data on a nonzero exit. Public diagnostics go to stderr;
nothing secret goes to either. The closed exit set is: 0 success; 1 check failed
(blocked or not verified); 2 invalid input or records, also a sanitized unexpected
helper failure; 3 invalid config, blocking routing; 4 manager judgment needed;
5 report to Shawn; 75 busy. Unexpected-failure stderr names the command path and
exception class, never a value.

Handlers return data for exit 0, or `Outcome(data, code)` using `ExitCode`.
`CommandError(message, code)` gives a message-only failure on stderr, no stdout,
and a listed nonzero code. Handlers never call `sys.exit` or write stdout;
public stderr diagnostics are allowed. Module specs document data per exit:
EV-1 gaps on 1; JV-1 retry/judgment, reason, input reference and policy digest
on 4; RT-1 reason on 4/5; CF-1 validation errors on 3; HC-1 holder details on 75.

`publish run` is the sole pass-through exception: once the child starts, its
stdout, stderr and status pass through unchanged. Failed revocation warns on
stderr and keeps the child's status. It requires `--status-file FILE`, written
atomically with mode 0600:

- Before starting the child: `{"childStarted": true, "childStatus": null}`.
- After exit: `{"childStarted": true, "childStatus": N, "revocation": "ok" | "failed"}`.
- Refusal or failure before start: `{"childStarted": false, "refusal": "<kind>"}`;
  exit 1 for a GA-3 refusal before minting, 2/3 for invalid input/config.

No file after exit means the child never started. A true `childStarted` with
null `childStatus` is unknown and requires reconciliation, such as `git ls-remote`.
Never infer start from status or parse child output. PB-1 uses the same logic
in-process and records these fields. GA-2/3 may refine or add fields only with
manager approval, preserving that rule.

`heavy run` logs child output to `--log FILE` and emits one JSON summary: child
status, whether HEAD and tree are unchanged, label, log path and times. It exits
0 only for child 0 plus unchanged HEAD/tree; 1 for child failure or HEAD/tree
change; 75 busy with holder details; 2/3 invalid input/config. It does not pass
the child's status through.

Global option names are reserved in `docs/cli.md`; `Registry` rejects module
collisions. PV-1b uses `--text-file LABEL=FILE`, leaving global `--text` intact.
`--repo` is contextual: an identity or checkout path in RC-1b, RC-1c, JV-1, RT-1 and
EV-1; `OWNER/NAME` for `pr verify`. Each module spec documents its meaning.

HS-1 supplies these shared interfaces:

- `cli`: `ExitCode`, `Outcome`, `PassThrough`, `CommandError`, `Registry`,
  `build_parser()`, `command_names()`, `helper_info()`.
- `jsonio`: `read_json_with_digest(path, max_bytes)` returns the parsed value
  and `sha256:<hex>` of the exact bytes read, alongside canonical JSON utilities.
- `process`: injectable `run()`, `helper_command()` and `ProcessError`.
- `paths`: `local_dir()`.

All later process launches call through the `process` module so tests can swap
it, with argument lists, no shell and no argument/environment echo in errors.
Tests use the fake runner in `tests/support.py`. Imports and `register` are cheap
and free of side effects; handlers take state paths only from `args.local_dir`.
The fixed registry stays fail-loud: a present broken module fails every command.
Shared-file and `MODULES` edits follow the explicit ownership rule above.

Every data file the helper writes is written atomically (a temporary file in the same
directory, fsync, then rename or exclusive link, then a directory fsync), with
mode 0600. HC-1's lock inode is the exception: it is created once with mode
0600 and never replaced or unlinked; its status is a separate data file. The
helper finds its root from its own real path, and `.local/`
beside it. A `--local-dir` option exists for tests.

### Record format

Records live under `.local/state/records/`, keyed by repository identity: the
managed repository's `origin` URL normalized to `<host>/<owner>/<name>`, for
example `github.com/proxy-hatch/t3-compose`. HTTPS and SSH forms give the same
identity. Labeled synthetic runs use `synthetic/<name>`.

    records/<identity>/work/<WORK-ID>/work.json
    records/<identity>/work/<WORK-ID>/events/<seq>.json

- `work.json` is written once. It holds `workId`, `linearIssue` (`null` where
  the repository has no Linear backlog), `repository`, `targetBranch`,
  `reviewBase`, `scope`, `designRefs`, `ownedPaths`, `dependencies`,
  `acceptance` (IDs and text), `requiredChecks` (ID, command, fast or heavy) and
  `writer`. A `scope` event replaces scope, acceptance or required checks; the
  latest one applies.
- Events are append-only, one file each, never edited or deleted. Each holds
  `schemaVersion`, `seq`, `kind`, `at` (UTC), `writer`, `helperCommit` and
  `data`. The filename contains only the sequence; the kind is inside the file.
  Each `<seq>.json` is created exclusively, so only one writer can own a
  sequence number, even when writers append different kinds. Commits are full
  40-character SHAs.
- A writer is `manager@<identity>` for the manager thread bound to that
  repository, or `lead:<WORK-ID>@<identity>` for a Feature lead.
- An unresolved value is `{"unknown": "<what is unresolved>"}`, never an
  omitted field.
- The global index is the set of work directories under an identity. There is
  no shared index file to corrupt.

| Kind | Required data |
|---|---|
| `routing-input` | task (original intent, acceptance, interfaces, scope, risk facts and dependencies), context (retry context or null), canonical input digest, routing-policy digest |
| `routing-classification` | routing-input reference and digest, routing-policy digest, rule version, criteria text and digest, declared risk facts, configured model, the response's snapshot `model` and `id`, both answers with probabilities and confidence, outcome, reason |
| `routing-judgment` | routing-input reference and digest, routing-policy digest, domain, difficulty, reason, basis (Jev failure, low confidence, mixed or unclear, manual) |
| `routing-selection` | routing-input reference and digest, routing-policy digest, classification or judgment reference, role, round and review scope, tier, floors applied, escalation, selection, chosen pool account or null, every skipped pool account with its reason (empty when no pool was walked), availability and fallback reason, config digest, catalog read time and digest, quota-state digest |
| `dispatch-intent` | role, round, `clientRequestId`, brief path and digest, selection, provisioned, start and review-base commits, dispatching checkout, isolation |
| `dispatch-confirmed` | `clientRequestId`, `taskId`, child thread reference, worktree path or unknown |
| `result` | `taskId`, completed T3 status evidence, worktree path, result commit, parent commit, branch observed, Worker claims, facts the manager verified, questions |
| `check` | check ID, command, commit, outcome, source (manager, Feature lead, Reviewer, assigned Session or Worker), evidence path |
| `criterion-evidence` | criterion ID, commit, source, evidence |
| `lead-assignment` | thread, assigned-by (manager), date, reason; latest event names the current lead |
| `review` | reviewer (`{kind:task,taskId:...}` or `{kind:lead,thread:...}`), base, head, verdict, findings (ID, severity, summary), evidence path and digest (required for lead, optional for task) |
| `finding-disposition` | review reference (review event sequence), finding ID, the Worker's response, disposition, by, evidence |
| `op-intent`, `op-result` | operation (push, pull request or other), target, commit, expected prior head or absence for a push; then the result and how it was confirmed |
| `decision` | question, answer or unknown, decided by, date |
| `scope` | the replaced fields and the decision that changed them |
| `next-action` | action or unknown |
| `reconciliation` | the uncertain item, live T3 and Git facts, outcome |
| `restart` | phase (before or after), checkpoint (last sequence and prefix digest), before-event reference for the after phase |
| `handoff` | pointers, hold end condition, pilot criteria, next action, checkpoint |
| `writer-transfer` | from, to, reconciliation reference, checkpoint |

Every review reference is the `review` event's sequence number, for either
reviewer form. A finding's key is `(review reference, finding ID)`. A disposition
must name an existing finding in that review; the same ID in a later round is a
different finding. A task review retains native provenance through the intent,
confirmation and result events with its `taskId`; a lead review has no `taskId`.
Both record base, head, verdict and findings. Lead evidence is a path plus digest.
Only the manager assigns a lead, through `lead-assignment` (thread, assigned-by,
date, reason); the latest event names the current lead. This grants no exception
to the single-writer or transfer rules: assignment/reassignment occurs while the
manager owns the record, with reconciliation and writer transfer when needed.
A lead review counts only from the current lead at that review event; final-head
verification also requires that lead still be current. One lead level remains.

A checkpoint holds the last sequence and the digest of
`work.json` plus the ordered event prefix through that sequence, before the
checkpoint-bearing event. A verification recomputes that prefix, not the current
snapshot. Handoff, restart and writer-transfer events never hash themselves.

The common routing input is `{task, context}` and is capped at 16 KiB. `task`
has only the six fields above; `context` is null or the supplied retry context.
Its digest covers that whole canonical JSON object.
The routing-policy digest covers classification instructions, criteria and rule
version, risk facts and floors, the configured Jev model, confidence threshold,
retry limit and uncertainty rule. CF-1 computes it from validated config. JV-1
and RT-1 use RC-1b's common input recorder (once RC-1c completes the records store); judgments have the same freshness
contract as classifications. Selection names the current input and policy
explicitly and also records the whole config digest for model preferences.

Finding dispositions are `fixed` (with its commit), `resolved-outside-tree`
(with evidence), `declined` (minor findings only, with a reason), `withdrawn`
(by the review author, Reviewer or lead), `disputed`, `rejected-by-second-review`
and `owner-decided`.

### Config schema

`.local/agent-routing.json`, version 2. Angle brackets are placeholders.

    {
      "version": 2,
      "master": Selection,
      "modelPreferences": {
        "software-engineering": {"medium": MediumSelection, "challenging": Selection},
        "non-engineering":      {"medium": MediumSelection, "challenging": Selection}
      },
      "easyWorker": PoolSelection,
      "textHelper": PoolSelection,
      "providerPools": {"<pool>": {"selection": "exhaust-in-order",
                                   "providerInstanceIds": ["<instance>", ...]}},
      "classification": {
        "ruleVersion": "<text>",
        "domain": {"instructions": "<text>", "criteria": {
          "SOFTWARE_ENGINEERING": "<text>", "OTHER": "<text>",
          "MIXED_OR_UNCLEAR": "<text>"}},
        "difficulty": {"instructions": "<text>", "criteria": {
          "EASY": "<text>", "MEDIUM": "<text>", "CHALLENGING": "<text>",
          "INSUFFICIENT_CONTEXT": "<text>"}}
      },
      "riskFacts": {"<fact-id>": {"description": "<text>",
                                  "floor": "medium" | "challenging"}},
      "roundTiers": {"corrections": "easy" | "medium" | "challenging",
                     "correctionsReview": "medium" | "challenging",
                     "documentReview": "medium" | "challenging"},
      "jev": {"provider": "openrouter", "model": "<model or alias>",
              "minConfidence": <number>, "maxContextRetries": 0 | 1,
              "uncertainResult": "manager-judgment", "timeoutSeconds": <1 to 120>,
              "keyFile": {"path": "<absolute path>", "field": "<name>"}},
      "availabilityFallback": {
        "roles": ["worker", "reviewer", "feature-lead"],
        "manager": "owner-choice",
        "chains": [MediumChain | ChallengingChain, ...],
        "recordReason": true, "reportReason": true}
    }
    Selection     = {"providerInstanceId": "<instance>", "model": "<model>",
                     "options": {"<id>": "<value>"}}
    Selection may also carry optional "roles": ["<role>", ...]
    PoolSelection = {"providerPool": "<pool>", "model": "<model>",
                     "options": {"<id>": "<value>"}}
    WorkerPoolSelection = PoolSelection with exactly "roles": ["worker"]
    MediumSelection = Selection | WorkerPoolSelection
    MediumChain = {"tier": "medium", "from": MediumSelection,
                   "to": [MediumSelection]}
    ChallengingChain = {"tier": "challenging", "from": Selection,
                        "to": [Selection]}

Validation rules:

- Unknown keys and wrong types fail at every level. Option values are strings
  or booleans.
- Version 1 fails with the list of missing sections, so routing stays blocked
  until the new sections hold Shawn's values.
- Floors are medium or challenging only. Fact IDs are lowercase words joined by
  hyphens.
- `roundTiers` holds the owner's round rules ([decisions 5 and 6](#decisions)):
  the Worker tier for enumerated Corrections, initially easy, and the Reviewer
  tiers for scoped Corrections re-reviews and document reviews, initially
  medium. A Reviewer tier is never easy.
- `minConfidence` is above 0 and at most 1. `maxContextRetries` is 0 or 1.
  `uncertainResult` is `manager-judgment`.
- `Selection` is direct and never names a member of any configured pool.
  It uses `providerInstanceId`; `PoolSelection` uses `providerPool`. The forms
  are disjoint: neither accepts the other's key.
- `MediumSelection` is the union of a direct `Selection` and a
  `WorkerPoolSelection`. Under [decision 12](#decisions), a `PoolSelection`
  in a medium preference or either medium chain endpoint requires exactly
  `"roles": ["worker"]`. Missing roles, any other role or any pool selection
  at the challenging tier fails. These rules apply to preferences and chain
  endpoints alike. agy is never a Reviewer. No other slot is broadened.
- Each chain has an explicit tier and exactly one target. Each `(tier, from)`
  appears once, and its target differs from its source, regardless of role
  limits. Both endpoints are validated selections associated with that tier;
  neither must be a primary preference. This allows a configured primary to
  fall back to a non-primary selection and permits its reverse chain. A chain
  without its reverse is a notice, not an error, because directions are owner
  values.
- Any direct `Selection`, including a preference or either chain endpoint, may
  carry optional `roles`: a non-empty subset of `availabilityFallback.roles`; absent means
  all of them. A role it does not serve treats it as unavailable, which may
  use that selection's one fallback. RT-1 checks tier, role eligibility and
  live availability. A selection falls back at most once; never fall back
  twice or follow the target's own chain ([decision 9](#decisions)).
- `easyWorker` and `textHelper` keep their existing `PoolSelection` shape and
  semantics; neither accepts `roles`. Every pool selection names a configured
  pool. `master` remains a direct `Selection`.
- The digest is `sha256:` and the hex SHA-256 of the file's bytes. Every routing
  record and dispatch intent stores it.

The quota state file beside the config keeps its current shape:
`activeProviderInstanceId`, `quotaObservations` (per instance, a list of
`{observedAt, status, resetAt, evidence}`, where status is `exhausted` or
`available`) and `note`. Observations remain per account for all its pool
models. No model-keyed quota contract is introduced. A model rollout gap is
not exhaustion evidence ([decision 12](#decisions)).

## Tasks

Each Task is one pull request. Live checks (L1 to L11) and owner steps are
listed under [Live checks and owner steps](#live-checks-and-owner-steps).

### HS-1 Helper skeleton and offline harness

- **Objective.** Create the package, the entry point, the shared JSON
  read utilities, the offline guard and the test command, so that later
  Tasks only add modules.
- **Design.** [Sources of truth and local records][d-records] (the helper);
  [t3-compose repository workflow][d-workflow] (`AGENTS.md` and its test
  command); [Pilot and validation][d-pilot] (test labels).
- **Files.** Create `bin/t3c`, `t3compose/__init__.py`, `t3compose/cli.py`,
  `t3compose/paths.py`, `t3compose/jsonio.py`, `t3compose/process.py`,
  `tests/test_process.py`, `tests/__init__.py`,
  `tests/support.py`, `tests/test_cli.py`, `tests/test_jsonio.py`,
  `tests/test_labels.py`, `docs/cli.md` and `docs/testing.md`. Modify
  `AGENTS.md`: the sentence that says no runtime or test commands exist gives
  way to `SUITE` and the label rule.
- **Interfaces.** `bin/t3c` checks for CPython 3.15 or later, rejects
  `sys.version_info.releaselevel != "final"`, and checks for
  `sys.flags.isolated` and `sys.flags.no_site`, puts its own root on `sys.path`
  and calls `t3compose.cli.main`. CD-2's shared interfaces in
  [Commands](#commands) apply, including `helper_info()` for helper metadata,
  `read_json_with_digest()`, the injectable process runner and fake test runner.
  No separate commit-reader module is added. `cli.py` holds the fixed module
  list (`config`, `records`, `evidence`, `jev`, `routing`, `quota`, `privacy`, `pullrequest`,
  `github_app`, `token_run`, `publish`, `heavy`, `briefs`, `skill_install`).
  It loads each module that exists and calls its `register`, which may add
  commands to a group that other modules share. A module that exists but fails
  to import is an error, not a skip. `t3c version` prints the root, commit,
  dirty flag and Python version. `jsonio` provides canonical JSON, file digests
  and size-capped reads. Imports/register have no side effects, registry
  collisions with reserved global options fail, and handlers use `args.local_dir`.
  Atomic persistence and crash recovery belong to RC-1b and RC-1c.
- **Acceptance.**
  1. After setup `uv python pin 3.15 --global` and
     `PY="$(uv python find 3.15)"`, `"$PY" -I -S <root>/bin/t3c version` works
     on final 3.15.x. Without `-I` or `-S`, below 3.15, or on a pre-release,
     it exits 2 in the existing message form naming the requirement.
  2. A missing module is skipped; a broken module fails loudly.
  3. Canonical JSON and file digests are stable, and oversized reads fail.
  4. The offline guard blocks network sockets, `urllib`, and `security`, `gh`,
     `ssh` and `curl` subprocesses, and the agent Harnesses `claude`, `codex`,
     `agy` and `pi` ([decision 9](#decisions)). A test proves each block.
  5. `tests/test_labels.py` fails when a test class lacks the guard or the
     `Offline` or `Simulated` prefix.
  6. `AGENTS.md` names `SUITE` and says that only the manager runs `live/`.
  7. `docs/cli.md` and `docs/testing.md` describe every implemented command,
     option, output and exit code, the module registry contract, the JSON
     utilities, shared process interfaces and offline guard contract. Pull
     request #4 adds them. Tests cover structured nonzero data, message-only
     errors, sanitized unexpected failures, reserved-option collisions,
     side-effect-free imports/register and injected process failures without
     argument/environment echo. Python child tests use `sys.executable`.
- **Offline tests.** `SUITE`.
- **Depends on.** This plan, merged.
- **Parallel with.** DOC-1; later code Tasks depend on HS-1.
- **Risk facts and tier.** Shared interface change (layout, registry, exit
  codes) → the medium tier. Reviewer: medium.
- **Review focus.** The registry's skip rule; the guard's completeness; JSON
  read limits; isolation and stable-release/floor refusals under the one
  uv-resolved CPython 3.15; CD-2 command and process contracts.
- **Size.** PR #4 alone may exceed 1,200 lines (decision 15); no second HS
  Task. This grants no other Task a size exception.

### DOC-1 Design alignment with owner decisions

- **Objective.** Reconcile the design and plan with Shawn's decisions of
  2026-10-27 and 2026-10-28, including decisions 13–16 and CD-1/2/3/4.
  Reconcile routing, final-head review, publication, runtime, shared CLI/process
  contracts, the temporary prose exception and Stage 1's merge gate.
- **Design.** [Open decisions][d-open]; [Routing and personal configuration][d-routing];
  [Manager threads and bases][d-bases]; [Accepted review policy][d-review];
  [Feature lead options][d-leads]; [Design refinement][d-refine];
  [Checks and publication][d-publication]; [t3-compose repository workflow][d-workflow].
- **Files.** Modify `docs/superpowers/specs/2026-10-27-t3-compose-design.md`.
  Decision 9 adds `docs/superpowers/plans/2026-10-27-t3-compose-implementation.md`
  to DOC-1 for the routing and command-spec contracts. Decision 12 updates
  both documents for the shared CF-1 and RT-1 pool contract. Decisions 13–16
  also add `GLOSSARY.md` and reconcile all three documents for CD-2/3/4.
- **Interfaces.** Documentation of decided contracts only; no runtime edits.
- **Changes.**
  - Record [decisions 1, 2 and 5](#decisions), dated 2026-10-27, and
    decisions 6, 7, 9, 11 and 12, dated 2026-10-28, in the design.
    Decision 1 replaces open decision 4. Keep a heading named "Open decisions"
    with one line saying no decisions remain open and linking to the sections
    recording them, so links to `#open-decisions` keep working. Update the
    status line and superseded references to open items (plan input 3).
  - Replace the design's Reviewer-tier rule with decision 5: document reviews
    and scoped re-reviews use the medium tier; first reviews of challenging
    code follow the Task's tier; agy is never a Reviewer. Record decision 6:
    enumerated Corrections use the easy tier, even after a floored first
    round, so the rule that easy work goes to agy only when no floor applies
    covers first rounds only. Say that these round tiers are config values,
    with decisions 5 and 6 as their initial values.
  - Replace fixed model names in routing, Feature lead, design-refinement and
    fallback text, including earlier decision summaries, with tier names and
    configured selections. The config decides models, options and availability
    fallbacks. Preserve the Engineering manager's owner-chosen identity rule.
  - Update the checkout rule and F2: after a merge, read the actual HEAD; the
    manager may pull a clean `main` with `--ff-only` only if T3 has not already
    fast-forwarded it. Read the provisioned commit again at dispatch (item 13).
  - Checks and publication gains two rules for every repository. A
    private-identifier match blocks publication until Corrections or Shawn
    resolves it. A mismatched pull request head, or author where identity
    rules name one, means "report to Shawn; not PR-ready" (items 1 and 2).
  - Record [decision 7](#decisions) in the t3-compose privacy rule. The
    `Co-authored-by` trailers that GitHub generates on a squash commit when
    Shawn merges may carry his commit email, which F7 found already public.
    Decision 19 applies the same exception to GitHub-generated trailers when
    a lead merges its own t3-compose pull request. Agents never write it. The
    trailers sit on the base branch, normally outside the review ranges that privacy checks cover. A match in a squash
    commit that a branch update brings into a range goes to Shawn (item 14).
  - Apply [decision 9](#decisions) to both documents: one fallback at most,
    role limits on any selection, Shawn's choice of GLM's place in config,
    the limited subscription exception, inline instructions and live catalog.
  - Scope merge authority to t3-compose's current rule and each managed
    repository's own rules; record Shawn's dated publication comment.
  - Record [decision 10](#decisions) and the command and test-harness spec
    convention in this plan, including HS-1's files and acceptance.
  - Apply [decision 11](#decisions) to both documents: the helper calls Jev
    directly, and the config names the `~typesafe/jev-latest` alias.
  - Apply [decision 12](#decisions) and CD-1 to both documents: medium
    Worker-only pool preferences and chain endpoints, model-aware account
    walking, per-account quota, role exclusions and L11 for the added choice.
    Record Shawn's choice of Argon as the medium software-engineering Worker
    preference with Sol as its one fallback; the Engineering manager changes
    private config. Preserve decision 11's call, alias, records and freshness.
  - Apply decisions 13–16 and CD-2/3/4 throughout design, plan and glossary:
    current lead final-head review and own-Task publication; manager delivery
    readiness; review sequence references, reviewer forms and lead assignments;
    stable uv-pinned Python 3.15; structured CLI outcomes, status files, shared
    interfaces/ownership and the HS-1 size exception; temporary prose setup;
    Stage 1 starts only after both PR3 and PR4 merge. Include CD-2 section 6
    now: the manager withdrew the separate follow-up documentation Task.
- **Easy carve-outs.** Reserve the following exact text for a sequential
  Assignment at the easy tier after the reconciling edits finish, with the
  decision date and exclusive ownership of the design for that Assignment:
  "Documents are medium. Technical documentation of a software project counts
  as software engineering. A floor applies to what a Task changes, not what it
  mentions." Any follow-up edits get current checks and a medium-tier
  re-review; they do not run beside DOC-1's writer.
- **Acceptance.** Decisions 1, 2, 5, 6, 7, 9, 11 and 12 replace the superseded
  text without contradictions. Tier names replace fixed Worker, Reviewer and
  Feature lead model names outside decisions 9 and 12 and sentences citing them.
  CF-1's schema, RT-1's selection and records, L11 and coverage agree with CD-1;
  no blanket pool fallback ban remains. First-round floors, review tiers and
  the one-fallback rule remain. This Task records Shawn's placement choice;
  the Engineering manager changes private config. Items 1 to 4, 13 and 14
  have the dispositions above. Merge authority is scoped to
  the repository and its current rules; decision 10 governs command and
  test-harness specs in this plan. Decisions 13–16 and CD-2/3/4 agree across
  glossary, review/record/EV-1 rules, publication and skill/templates, Python
  constraints and commands, downstream CLI/process contracts, coverage and
  dependencies. Exact owner quotes and dated history remain, with superseding
  notes; no headings/anchors are removed. No implementation or live pass is
  claimed. Tree and message privacy checks are
  clean; all links and anchors resolve, including `#open-decisions` with its
  heading and one-line pointer to the recorded decisions.
- **Offline tests.** Link and privacy checks; after HS-1 merges, `SUITE`,
  unchanged. DOC-1 has no runtime prerequisite.
- **Depends on.** This plan, merged.
- **Parallel with.** HS-1; Stage 1 waits for both PR3 and PR4 merges. It owns
  the glossary, design and plan.
- **Risk facts and tier.** Reconciliation across three documents → the medium
  tier. The exact-text carve-outs above use the easy tier. Reviewer: medium,
  never agy.
- **Review focus.** Faithful decisions 1–16 and CD-1/2/3/4; no contradictory
  tier, pool or checkout rule; a squash-trailer exception no wider than decision
  7; final-head/record/CLI/runtime consistency and resolved links.
- **Size.** Reconciliation includes replaced text; no new size waiver.

### CF-1 Config schema and validation

- **Objective.** Validate `.local/agent-routing.json` and the quota state file
  as [Config schema](#config-schema) defines, and document the contract, so
  that invalid config blocks routing.
- **Design.** [Routing and personal configuration][d-routing];
  [Jev decision routing][d-jev] (`jev.minConfidence`, the Jev model, the key
  source); [Open decisions][d-open], item 4, as answered by
  [decision 1](#decisions) and reconciled by DOC-1.
- **Files.** Create `t3compose/config.py`, `docs/configuration.md`,
  `docs/examples/agent-routing.example.json`,
  `docs/examples/agent-routing-state.example.json` and `tests/test_config.py`.
  Create `docs/cli/config.md`.
- **Interfaces.** `t3c config check [--config PATH] [--state PATH]` prints the
  digest, the rule version, the risk facts with their floors, the round tiers
  and any notices; it exits 0 or 3. Exit 3 carries validation errors, including
  missing version-2 sections, as one JSON value (CD-2). `load_config()` returns
  a validated, read-only view and its digest, plus the routing-policy digest defined in
  [Record format](#record-format).
  Nothing is cached between commands, so a valid edit applies to the
  next dispatch and running children keep their recorded selection.
- **Acceptance.**
  1. Each validation rule has a passing and a failing test.
  2. The current version-1 shape fails with a message that names the missing
     sections.
  3. Both examples pass and use synthetic instance names. A primary with a
     non-primary fallback and its reverse, a direct Worker-only preference
     and a direct Worker-only fallback target pass. Examples also include a
     medium Worker-only pool preference, a pool source with a direct target,
     and a direct source with a Worker-only pool target; all pass with exactly
     `"roles": ["worker"]` on each pool selection ([decision 12](#decisions)).
     Missing tier associations, duplicate sources in one tier, zero or
     multiple targets, a target equal to its source regardless of role limits,
     and an empty or out-of-set `roles` list on any direct selection fail.
     Pool preferences and endpoints without roles, with another role (alone
     or alongside Worker), or at the challenging tier fail. A direct
     selection naming a pool member fails in every slot, including chain
     sources and targets. Mixed direct/pool keys fail. Existing direct,
     reverse-chain and decision-9 Worker examples remain covered.
  4. An error names the path and the rule, and never prints a value read from a
     key file.
  5. `docs/configuration.md` says that Shawn changes values, or the manager at
     his instruction, and that a change applies to the next dispatch only.
- **Offline tests.** `SUITE -p 'test_config.py'`, then `SUITE`.
- **Depends on.** HS-1 / PR4 and DOC-1 / PR3, both merged. Stage 1 uses
  the merged plan, including CD-4 for RC-1a, RC-1b and RC-1c.
- **Parallel with.** RC-1a, RC-1b, RC-1c (sequential across the three RC-1 Tasks), PV-1a.
- **Risk facts and tier.** Shared interface change (JV-1 and RT-1 read this
  contract) → the medium tier. Schema validation is bounded and fully
  testable; RT-1 enforces role exclusions. Reviewer: medium.
- **Review focus.** Strictness; the agy and fallback rules; that no routing
  preference hides in a constant.
- **Size.** About 700 lines.

### RC-1 Records store

Under [decision 18](#decisions), the records store ships as three sequential
pull-request Tasks under one Feature lead—[RC-1a](#rc-1a-record-schema-contract-foundation),
[RC-1b](#rc-1b-durable-store-core-history-and-workevent-commands) and
[RC-1c](#rc-1c-checkpoints-reconciliation-and-repair)—each at most 1,200 changed
lines, with no size waiver and no fourth aggregate implementation Task or pull
request. Together they store work records as [Record format](#record-format)
defines: append-only, atomic, validated, with one writer, history and recovery
from interrupted writes ([Sources of truth and local records][d-records];
[Manager threads and bases][d-bases]; [Work cycle][d-cycle], steps 3 and 5;
[Taking over existing Claude work][d-takeover]).

Exclusive sequential file ownership across the three Tasks:

- `RC-1a` creates `t3compose/schema.py`, `tests/test_records.py` and the schema
  contract portion of `docs/records.md`. It adds no usable store and registers
  no CLI commands.
- `RC-1b` modifies `t3compose/jsonio.py` and `tests/test_jsonio.py` (atomic
  replace and exclusive create), creates `t3compose/records.py` and
  `docs/cli/records.md`, and extends `tests/test_records.py` and
  `docs/records.md` for the durable store core, history, common routing-input
  recorder, and `t3c work new`, `t3c work show`, `t3c work list` and
  `t3c event add`. It refuses `review`, `finding-disposition` and
  `lead-assignment` events until RC-1c adds their cross-event enforcement.
- `RC-1c` extends `t3compose/records.py`, `docs/records.md`,
  `docs/cli/records.md` and `tests/test_records.py`, and creates
  `tests/test_reconcile.py` for `t3c work snapshot`, `t3c work reconcile` and
  `t3c work repair`, prefix checkpoint verification, `restart`, `handoff` and
  `writer-transfer` enforcement, orphan-temporary crash recovery, and the full
  CD-4 review/finding history and current-lead provenance unit, including its
  tests and documentation. RC-1a validates schema shapes only; RC-1c enforces
  review references by event sequence, cross-round finding identity,
  disposition-to-finding joins and lead-assignment currency, including the
  cases relevant to EV-1.

Every Task that depends on the records store (`RT-1`, `JV-1`, `EV-1`, `BT-1`,
`HC-1`, `PB-1`, `SK-1` and `PH-1`) and Stage 2 wait for `RC-1c` (`RC-1a` alone
provides only the schema contract and clears no downstream Task). The complete
ten-item RC-1 acceptance contract holds across the three Tasks and on the
combined result after `RC-1c`.

### RC-1a Record schema contract foundation

- **Objective.** Define and validate the `work.json` and event schemas as data,
  keeping all required fields and CD-4 record contracts from
  [Record format](#record-format) explicit, and document the schema mapping in
  `docs/records.md`, without adding a usable store or CLI commands.
- **Design.** [Sources of truth and local records][d-records];
  [Manager threads and bases][d-bases] (the three commits);
  [Feature lead options][d-leads] and [Accepted review policy][d-review]
  (CD-4 review sequence references, reviewer forms, lead assignments and
  finding keys).
- **Files.** Create `t3compose/schema.py` (event kinds and record schemas as
  data), `tests/test_records.py` and `docs/records.md` (schema part and
  design-to-field mapping). Exclusive ownership of these files during RC-1a;
  `t3compose/records.py`, `docs/cli/records.md` and `t3compose/jsonio.py` are
  not touched.
- **Interfaces.**
  - Pure schema data definitions and validation functions in
    `t3compose/schema.py` for `work.json`, the event envelope, every event kind
    in [Record format](#record-format), repository identities
    (`<host>/<owner>/<name>` and `synthetic/<name>`), writer identifiers
    (`manager@<identity>` and `lead:<WORK-ID>@<identity>`), full 40-character
    commit SHAs, explicit `{"unknown": "<what is unresolved>"}` values (never
    omitted fields), checkpoint shapes and finding dispositions.
  - No usable store and no CLI commands (`t3compose/records.py` remains absent,
    so `t3compose/cli.py` continues to skip `records`).
- **Rules.**
  - Every `work.json` field and every event kind's required `data` fields in
    [Record format](#record-format) are validated strictly; missing required
    fields, unknown keys or wrong types fail validation.
  - CD-4 schema rules are enforced on event data: `reviewer` is either
    `{"kind": "task", "taskId": "<T3 task>"}` or
    `{"kind": "lead", "thread": "<lead thread>"}` (never with a fabricated
    `taskId`); `evidence` (path and digest) is required for a lead review and
    optional for a task review; every review reference in `finding-disposition`
    is an integer `review` event sequence number, and findings key on
    `(review reference, finding ID)`; `lead-assignment` requires `thread`,
    manager `assigned-by`, `date` and `reason`; `check` sources include manager,
    Feature lead, Reviewer, assigned Session and Worker (with Worker-only claims
    distinguished from verified sources). These are schema-shape checks only;
    cross-event review references, finding joins and current-lead provenance
    are enforced by RC-1c.
- **Acceptance.**
  1. Each event kind's required fields and `work.json`'s required fields have a
     passing and a failing test in `tests/test_records.py` (RC-1 acceptance item
     1).
  2. CD-4 schema validation covers both `reviewer` forms, rejects a fabricated
     lead `taskId`, requires lead `evidence` path and digest, validates integer
     review sequence references and `(review reference, finding ID)` keys,
     rejects a `lead-assignment` `assigned-by` other than the manager, and
     validates `check` sources and finding dispositions (schema parts of RC-1
     acceptance items 8 and 9).
  3. `docs/records.md` maps each record item in the design to a field (RC-1
     acceptance item 7, schema part).
  4. No store module or CLI command is added; `SUITE` passes with `records`
     still skipped by `cli.py`.
- **Offline tests.** `SUITE -p 'test_records.py'`, then `SUITE`. Fixtures use
  synthetic task IDs and paths.
- **Depends on.** HS-1 / PR4 and DOC-1 / PR3, both merged. Stage 1 uses the
  merged plan, including CD-4. RC-1a's code may merge before the decision-18
  documentation follow-up pull request merges, so it has no dependency on that
  documentation follow-up's merge.
- **Parallel with.** CF-1, PV-1a. Cannot run beside RC-1b or RC-1c.
- **Risk facts and tier.** Shared schema contract foundation (no persistence,
  concurrency or crash recovery) → the medium tier. Reviewer: medium, never agy.
- **Review focus.** Exact field and type coverage for `work.json` and every
  event kind in [Record format](#record-format); CD-4 reviewer, finding-key and
  lead-assignment schema rules; design-to-field mapping in `docs/records.md`.
- **Size.** About 800–1,100 lines, tests and documentation included (at most
  1,200 lines; decision 18).

### RC-1b Durable store core, history and work/event commands

- **Objective.** Add atomic file persistence and exclusive event creation to
  `jsonio.py`, implement the durable records store core, history and cross-event
  invariants outside the CD-4 review/finding history unit in
  `t3compose/records.py`, provide the common routing-input
  recorder, and expose `t3c work new`, `t3c work show`, `t3c work list` and
  `t3c event add`.
- **Design.** [Sources of truth and local records][d-records];
  [Manager threads and bases][d-bases] (the three commits);
  [Work cycle][d-cycle], steps 3 and 5.
- **Files.** Modify `t3compose/jsonio.py` and `tests/test_jsonio.py` to add
  atomic replace and exclusive create. Create `t3compose/records.py`
  and `docs/cli/records.md`. Extend `tests/test_records.py` and `docs/records.md`
  to add the store layout, atomic write protocol, history and core command
  rules. Exclusive ownership of these files after RC-1a merges.
- **Interfaces.**
  - `t3c work new --repo REPO --work ID --file WORK.json --writer W`
  - `t3c event add --repo REPO --work ID --kind KIND --file DATA.json --writer W [--checkout PATH]`
  - `t3c work show|list --repo REPO [--work ID]` (`--work` is required for
    `show` and omitted for `list`).
  - Common routing-input recorder in `t3compose/records.py` (used by JV-1 and
    RT-1) that validates the 16 KiB cap, computes the canonical input digest,
    and appends a `routing-input` event with the routing-policy digest.
  - `REPO` (`--repo`) is an identity or a checkout path, as `docs/cli/records.md`
    states. `helperCommit` comes from `t3compose.cli.helper_info()`; no separate
    Git-reader interface is invented. Subprocesses (`git`) run through
    `t3compose.process`. Handlers use `args.local_dir` and CD-2's closed exit
    set (`0` and `2`).
- **Rules.**
  - Every data file the store writes is written atomically (a temporary file in
    the same directory, fsync, then rename for `work.json` or exclusive link for
    `<seq>.json`, then a directory fsync), with mode `0600`.
  - An event goes to a temporary file, is synced, then is linked to
    `<seq>.json` by exclusive create; its kind is inside the file. An existing
    destination means another writer owns that sequence, and the command exits
    2 without replacing the event or retrying at a later sequence. No command
    edits or deletes an event.
  - The writer must equal the record's current writer.
  - In a Worker's `dispatch-intent`, read the dispatching checkout's actual
    HEAD at dispatch for the provisioned commit; never assume it lags origin.
    The three commits are full SHAs and the review base equals the record's. Round 1 starts at the review base. A later
    round starts at the latest recorded result commit, or at the previous start
    when no result commit exists. With `--checkout`, the helper confirms that
    the dispatching checkout's HEAD is the provisioned commit and that the
    start commit exists. A Reviewer's intent records the review base and the
    head under review instead.
  - A `dispatch-intent` for a writer is refused until the prior writer has a
    completed result with T3 evidence, or a reconciliation proves it stopped
    and its commits and uncommitted work were preserved. A
    `dispatch-confirmed` only identifies a dispatch; it never clears this
    block. Unknown liveness blocks a new writer. A retry reuses the same
    intent, `clientRequestId` and brief digest; it is not a new writer.
  - A `result` needs completed T3 status evidence, a worktree path and a result
    commit. A branch name is informational only ([finding F8][d-findings]).
  - Until RC-1c, the store API and `event add` refuse `review`,
    `finding-disposition` and `lead-assignment` events. RC-1a's schema shapes
    remain available, but RC-1b does not accept these events without RC-1c's
    complete history and provenance enforcement.
  - `routing-classification` and `routing-judgment` must reference an existing
    `routing-input` event with matching input and policy digests.
  - An invalid record makes commands on that work exit 2. Other work stays
    usable.
- **Acceptance.**
  1. Supported event kinds' required fields and store rules have passing and
     failing tests through the store API and CLI (RC-1 acceptance item 1, core
     store part). Both interfaces explicitly refuse all three deferred kinds:
     `review`, `finding-disposition` and `lead-assignment`.
  2. A failure injected at each atomic-write step leaves the old or new file,
     never a partial one; written data has mode 0600 (RC-1 acceptance item 2,
     atomic-write part).
  3. Race two writers using the current writer identity to append different
     kinds at the same sequence on one record. Exactly one `<seq>.json` is
     created with the winning kind inside; one writer wins and the other exits
     2 without overwriting it or appending at a later sequence (RC-1 acceptance
     item 3).
  4. A second writer is refused for both unconfirmed and confirmed-but-running
     dispatches, and for reconciliations with unknown liveness or unpreserved
     work. Only a completed result or a stopped-and-preserved reconciliation
     clears it (RC-1 acceptance item 4, duplicate-writer refusal part).
  5. No command edits or deletes an event (RC-1 acceptance item 6).
  6. `docs/records.md` and `docs/cli/records.md` document the store layout,
     persistence and `work new`, `work show`, `work list` and `event add`
     commands (RC-1 acceptance item 7, store core part).
  7. Classifications and judgments must reference an existing routing input
     with matching input and policy digests (RC-1 acceptance item 10).
- **Offline tests.** `SUITE -p 'test_jsonio.py'`, `SUITE -p 'test_records.py'`,
  then `SUITE`. Fixtures use synthetic task IDs and paths.
- **Depends on.** RC-1a, merged. Cannot run beside RC-1a or RC-1c.
- **Parallel with.** CF-1, PV-1a.
- **Risk facts and tier.** Subtle invariants (atomic persistence, the
  exclusive-create writer race and duplicate-dispatch refusal) → the
  challenging tier. Reviewer: challenging.
- **Review focus.** Fsync order and mode 0600; the exclusive-create race;
  the duplicate-dispatch block and three-commit checks; refusal of the three
  deferred CD-4 event kinds; routing-input digest matching.
- **Size.** About 900–1,200 lines, tests and documentation included (at most
  1,200 lines; decision 18).

### RC-1c Checkpoints, reconciliation and repair

- **Objective.** Complete the records store with `t3c work snapshot`,
  `t3c work reconcile` and `t3c work repair`, prefix checkpoint verification,
  `restart`, `handoff` and `writer-transfer` invariants, and recovery from
  interrupted writes and orphaned temporary files. Add the complete CD-4
  review/finding history and current-lead provenance unit, with tests and
  documentation, including the cases relevant to EV-1.
- **Design.** [Sources of truth and local records][d-records] (ownership
  transfer and interrupted-write recovery); [Work cycle][d-cycle], steps 1 and
  5; [Taking over existing Claude work][d-takeover] (reconcile before acting);
  [Pilot and validation][d-pilot] (deliberate context restarts);
  [Accepted review policy][d-review] (CD-4 review provenance, finding identity
  and lead history).
- **Files.** Modify `t3compose/records.py`, `docs/records.md`,
  `docs/cli/records.md` and `tests/test_records.py`. Create
  `tests/test_reconcile.py`. Exclusive ownership of these files after RC-1b
  merges. These files also cover the moved CD-4 unit and its EV-1-relevant
  tests and documentation.
- **Interfaces.**
  - `t3c work snapshot|reconcile|repair --repo REPO [--work ID] [--through-seq N]`
    (`--through-seq` is for `snapshot` only; `--work` is required for
    `snapshot` and optional for `reconcile` and `repair`).
  - `REPO` is an identity or a checkout path. `snapshot` prints a checkpoint:
    the last sequence and the canonical digest of `work.json` and its event
    prefix. `--through-seq` recomputes an earlier checkpoint. `reconcile` lists
    uncertain items: intents without confirmation, confirmed dispatches without
    a completed result or a stopped-and-preserved reconciliation, operation
    intents without results, and orphaned temporary files. `repair` prints and
    then removes orphaned temporary files; it never changes an event.
- **Rules.**
  - Enable the three event kinds refused by RC-1b only with full CD-4
    enforcement. CD-4 validates both `reviewer` forms from
    [Record format](#record-format),
    their base/head/verdict/findings and uniform event-sequence references.
    Task reviews retain native dispatch/result provenance (`dispatch-intent`,
    `dispatch-confirmed` and `result` with matching `taskId`); lead reviews have
    no `taskId`, require evidence path plus digest, and must come from the
    current lead named by the latest manager-only `lead-assignment` event at
    the review. Record the history needed for EV-1 to require that lead still
    be current at verification; cover current and replaced leads in tests.
    Repeated finding IDs in separate review rounds remain distinct under
    `(review reference, finding ID)`, and a `finding-disposition` with a missing
    or wrong review reference or unknown finding ID is refused. `withdrawn`
    requires the review author. `check` sources include Feature lead without
    accepting Worker claims.
  - A checkpoint holds the last sequence and the digest of `work.json` plus the
    ordered event prefix through that sequence, before the checkpoint-bearing
    event. Verification recomputes that prefix, not the current snapshot.
    `handoff`, `restart` and `writer-transfer` events never hash themselves, and
    appending them verifies their checkpoint against the existing prefix (plus
    the `before`-event reference on an `after`-phase `restart`).
  - The writer must equal the record's current writer. A `writer-transfer`
    needs a reconciliation showing the old writer stopped, its children were
    reconciled and its artifacts preserved. It also needs a checkpoint of the
    prefix before the transfer; uncertain liveness or unpreserved work blocks
    transfer. Only the manager records a `lead-assignment`; assignment or
    reassignment across writer transfers obeys the single-writer and
    reconciled-transfer rules.
  - `repair` removes only orphaned temporary files left by interrupted writes;
    it never modifies or unlinks `work.json` or any `<seq>.json` event file.
- **Acceptance.**
  All ten original RC-1 acceptance items must hold on the combined result after
  RC-1c:
  1. Each event kind's required fields have a passing and a failing test.
  2. A failure injected at each atomic-write step leaves the old or new file,
     never a partial one; written data has mode 0600. Test an interrupted write
     immediately before and after an event link, followed by recovery:
     `reconcile` reports orphaned temporary files, `repair` removes only those
     files, and a restarted append uses the next available sequence. The store
     stays readable, any linked event remains unchanged, and recovery leaves
     no partial events, duplicate sequences or sequence gaps.
  3. Race two writers using the current writer identity to append different
     kinds at the same sequence on one record. Exactly one `<seq>.json` is
     created with the winning kind inside; one writer wins and the other exits
     2 without overwriting it or appending at a later sequence.
  4. An intent without confirmation appears in `reconcile`. A second writer
     is refused for both unconfirmed and confirmed-but-running dispatches,
     and for reconciliations with unknown liveness or unpreserved work. Only a
     completed result or a stopped-and-preserved reconciliation clears it.
  5. `snapshot` is stable across reads. Its current digest changes on append,
     but a saved prefix checkpoint verifies after restart, handoff and writer
     transfer events are added. A changed prefix fails verification.
  6. No command edits or deletes an event.
  7. `docs/records.md` maps each record item in the design to a field.
  8. Repeated finding IDs in separate review rounds remain distinct; a
     disposition with a missing or wrong review reference is refused.
  9. Both review forms pass with valid provenance; fabricated lead `taskId`,
     missing lead evidence, invalid reviewer forms and a lead-assignment by
     anyone other than the manager fail. Test manager-only assignments through
     reconciled writer transfer, current-lead history, author-only withdrawal
     and the Feature lead check source.
  10. Classifications and judgments must reference an existing routing input
      with matching input and policy digests.
- **Offline tests.** `SUITE -p 'test_jsonio.py'`, `SUITE -p 'test_records.py'`,
  `SUITE -p 'test_reconcile.py'`, then `SUITE`.
  Fixtures use synthetic task IDs and paths.
- **Depends on.** RC-1b, merged. Cannot run beside RC-1a or RC-1b. Every
  downstream dependent of the records store (`RT-1`, `JV-1`, `EV-1`, `BT-1`,
  `HC-1`, `PB-1`, `SK-1` and `PH-1`) and Stage 2 wait for RC-1c.
- **Parallel with.** CF-1, PV-1a.
- **Risk facts and tier.** Subtle invariants (recovery, crash windows, prefix
  checkpoints, reconciled writer transfer and cross-event review provenance) → the challenging tier. Reviewer:
  challenging.
- **Review focus.** Crash windows before and after event link; `reconcile` and
  `repair` handling of orphaned temporary files without touching events; prefix
  checkpoint verification across `restart`, `handoff` and `writer-transfer`;
  reconciled writer-transfer and manager-only lead assignment invariants; review
  event-sequence references, cross-round finding identity, disposition joins
  and current-lead history/provenance, including EV-1-relevant cases.
- **Size.** About 830–1,080 lines, tests and documentation included (at most
  1,200 lines; decision 18).

### PV-1a Identifier-list loader and matcher

- **Objective.** Load the private identifier list and match bounded text with
  the manual check's semantics, so JV-1 can screen input before publication
  checks exist.
- **Design.** [t3-compose repository workflow][d-workflow] (the identifier
  list); [Jev decision routing][d-jev] (private data stays out of requests).
- **Files.** Create `t3compose/identifier_list.py` and
  `tests/test_identifier_list.py`.
- **Interfaces.** `load_identifiers(local_dir)` returns a validated list;
  `match_identifiers(identifier_list, text)` returns matching list line numbers,
  never the matched text. No new command.
- **Rules.** Load `.local/state/private-identifiers.txt` on every check. A
  missing, unreadable or empty list, or a pattern that `grep -E` rejects, blocks
  with exit 2. Matching uses `grep -E`, so results equal the manual check.
  Diagnostics name the list line number, never the pattern or matched text.
- **Acceptance.** Offline tests with synthetic lists cover clean text,
  matches, missing, unreadable, empty and invalid lists, equivalence with
  `grep -E`, a list edited between calls, and output that reveals no match.
- **Offline tests.** `SUITE -p 'test_identifier_list.py'`, then `SUITE`.
- **Depends on.** HS-1 / PR4 and DOC-1 / PR3, both merged. Stage 1 uses
  the merged plan, including CD-4 for RC-1a, RC-1b and RC-1c.
- **Parallel with.** CF-1, RC-1a, RC-1b, RC-1c (sequential across the three RC-1 Tasks).
- **Risk facts and tier.** Bounded loading and matching → the medium tier.
  PV-1b and JV-1 enforce their publication and external-request gates.
  Reviewer: medium.
- **Review focus.** Equivalence with the manual matcher; reload on every call;
  no matched text in diagnostics.
- **Size.** About 250 lines.

### RT-1 Routing selection and agy quota

- **Objective.** Turn a classification or a judgment, the declared risk facts,
  the live catalog and the quota state into one recorded selection, or a stop.
- **Design.** [Routing and personal configuration][d-routing];
  [Jev decision routing][d-jev] (code applies floors and exclusions);
  [Accepted review policy][d-review] (agy is never a Reviewer).
- **Files.** Create `t3compose/routing.py`, `t3compose/quota.py`,
  `live/catalog_shape.py`, `tests/test_routing.py` and `tests/test_quota.py`.
  Create `docs/cli/routing.md` and `docs/cli/quota.md`.
- **Interfaces.**
  - `t3c route input --repo REPO --work ID --input TASK.json` records the
    common input with RC-1b's recorder and prints its reference, input digest and policy digest.
  - `t3c route judge --repo REPO --work ID --input-ref SEQ --policy-digest DIGEST --domain D --difficulty X --reason TEXT`
  - `t3c route select --repo REPO --work ID --role worker|reviewer|feature-lead|text-helper --input-ref SEQ --policy-digest DIGEST --round first|corrections --review-scope code|document|corrections-delta --catalog FILE --catalog-read-at TIME [--escalate]`
    prints the provider instance, model and options for `delegate_task`. Exits
    4 and 5 carry a reason as one JSON value (CD-2).
  - `t3c quota show`;
    `t3c quota record --instance I --status exhausted|available --observed-at T [--reset-at T] --evidence TEXT`;
    `t3c quota activate --instance I --reason TEXT`.
- **Rules.**
  - Config is loaded and validated on every call; invalid config exits 3.
  - Selection names the current `routing-input` reference and policy digest.
    It loads that record, verifies it is the latest input for the work, and
    recomputes the policy digest from current config. The classification or
    judgment must reference exactly that input, input digest and policy digest;
    otherwise it exits 4. Risk facts come from that input, not a separate flag.
    Changed requirements, criteria, floors or confidence threshold require a
    new classification or judgment. The fixed text-helper route needs no
    classification; its input reference still records the bounded request.
  - First-round Worker tier: the higher of the difficulty and every declared
    fact's floor. The Corrections rule below replaces that first-round floor
    calculation for enumerated Corrections.
    `--escalate` sets it to the higher of the tier the round would get without
    escalation and one step above the highest Worker tier already recorded for
    the same round kind on the Task. It exits 4 when that recorded tier is
    challenging or no round of that kind is recorded. Easy first-round work
    goes to the agy pool only when no floor applies.
  - Corrections with enumerated findings use `roundTiers.corrections`
    (initially easy, so the agy pool), even when a floor set the first round's
    tier ([decision 6](#decisions)); a brief that adds unresolved work is
    split or classified afresh. Reviewer tier: `roundTiers.documentReview` for
    documents and `roundTiers.correctionsReview` for scoped Corrections
    re-reviews, both initially medium; first reviews of challenging code
    follow the Task's tier, including any escalation. Other first reviews are
    at least medium. The round and scope
    are recorded; a Reviewer selection is never a pool member.
  - Feature lead: the preference at the Task's tier, at least medium. Text
    helper: the fixed `textHelper` route, with no classification.
  - A selection is available only when the catalog lists the instance and the
    model, the provider reports `canRunChildTask: true`, and the catalog
    advertises its options. A failed status check counts as unavailable. A
    catalog read more than 15 minutes earlier is refused.
  - A direct selection, whether a preference or either chain endpoint, may
    limit its roles; absent limits, it serves all `availabilityFallback.roles`.
    A pool preference or chain endpoint is medium and has exactly
    `"roles": ["worker"]` ([decision 12](#decisions)). It cannot serve
    Reviewers or Feature leads. The added model in decision 12 also cannot
    serve the text helper or master. A role it does not serve treats it as
    unavailable. An unavailable selection uses
    its one configured fallback at the selected tier, once
    ([decision 9](#decisions)). The target need not be a primary preference,
    but its tier association, role eligibility and live availability are
    checked. If the target is unavailable too, or no chain applies, exit 5
    (report to Shawn). Never fall back twice or follow the target's own chain.
    Record and report every fallback. Record and report every GLM use as
    billed per token, even when it is the preference. The master never falls
    back.
  - Before walking a pool, check the active account itself. If it is missing
    from the catalog, reports `canRunChildTask: false`, or fails its status
    check, stop with exit 4 without rotation. A missing model on an otherwise
    available active account is handled by the walk below, not this stop.
  - Resolve a pool selection by walking from the active account in pool order,
    wrapping around once. Skip an account whose catalog does not list the
    requested model and options; record "model not offered". This is not quota
    evidence and does not rotate the active account. Skip confirmed exhausted
    accounts. An exhausted account returns only after its reset time passes
    or an `available` observation. Unknown quota is usable. Select the first
    eligible offering account; never select an unavailable account. Record
    the chosen account and every skipped account with its reason in
    `routing-selection`, retaining skipped-account reasons even when the final
    selection is a direct fallback. `easyWorker` uses this same model-aware walk.
  - Walking accounts is not a fallback and does not consume another fallback.
    If no eligible offering account remains, use the selection's one configured
    fallback or exit 5. A pool reached as a fallback cannot follow its own
    chain. Quota observations apply per account to all its pool models;
    rollout gaps are never exhaustion evidence. No model-keyed quota contract
    is introduced ([decision 12](#decisions)).
  - `route select` writes only its `routing-selection` event. `quota record`
    and `quota activate` write the quota file atomically and keep every
    observation. Tool and correctness failures are not quota evidence;
    `quota record` needs a status and the evidence text.
- **Acceptance.** Offline tests cover each rule, including a changed input,
  criteria or threshold after both classification and judgment; an explicitly
  named stale input or policy; a non-primary fallback; a fallback at the wrong
  tier or ineligible for the role; the first-review, document, Corrections
  and scoped re-review tiers; Corrections after a floored first round; an
  escalated Corrections round, including one after `roundTiers.corrections`
  rises and one with no recorded Corrections round; and changed `roundTiers`
  values. Under [decision 9](#decisions), a Worker-only preference serves a
  Worker; a Reviewer uses that preference's one eligible fallback. A
  Worker-only fallback target leaves a Reviewer with exit 5. A target that is
  itself the source of another chain is not followed further. GLM is eligible
  only for medium Workers; every use records and reports its per-token billing.
  Under [decision 12](#decisions), a medium Worker-only pool preference serves
  a Worker; a Reviewer and a Feature lead each reach its one eligible fallback.
  A Worker-only pool chain target serves a Worker and leaves a Reviewer or
  Feature lead with exit 5. The added choice never serves the text helper or
  master. `Simulated…` tests inject quota observations to show rotation in
  order, wrap-around and reset evidence. The walk skips a non-offering account,
  including the active account, without changing the active account for that
  rollout gap. It records each skip and the chosen account. Confirmed exhaustion
  excludes an account for all pool models. All offering accounts exhausted
  yields one fallback or exit 5, even if a non-offering account has usable quota.
  Account walking never consumes a second fallback, including for a pool chain
  target. An active account absent from the catalog, unable to run children or
  failing its status check yields exit 4 without rotation. No test selects an
  unavailable account. `easyWorker` has the same model-aware walk tests.
  `live/catalog_shape.py` validates a saved real catalog (L3).
- **Prework.** Before dispatch, the manager saves a redacted sample of a real
  `orchestrator_capabilities` result in `.local/state/` and names it in the
  brief. The parser reads only the fields it needs and treats an unknown shape
  as unavailable.
- **Offline tests.** `SUITE -p 'test_routing.py'`, `SUITE -p 'test_quota.py'`,
  then `SUITE`.
- **Depends on.** CF-1, RC-1c (after RC-1a and RC-1b).
- **Parallel with.** JV-1; L1 and L2 as their prerequisites finish.
- **Risk facts and tier.** Guardrail boundary (role exclusions, agy never a
  Reviewer) and subtle invariants (one fallback, exhaustion order) →
  the challenging tier. Reviewer: challenging.
- **Review focus.** Tier arithmetic and first-round floors; no path to agy
  for a Reviewer; medium Worker-only pool preferences and endpoints; role
  limits on preferences and endpoints; one fallback at most; model-aware
  walking and per-account quota; the config digest in every record.
- **Size.** About 1,100 lines. If it grows past 1,200, `quota` moves to a
  follow-up Task.

### JV-1 Jev adapter

- **Objective.** Ask Jev the domain and difficulty questions in one bounded
  request, validate the answer and record the classification.
- **Design.** [Jev decision routing][d-jev];
  [Routing and personal configuration][d-routing]; [decision 11](#decisions).
- **Files.** Create `t3compose/jev.py`, `live/jev_contract.py` and
  `tests/test_jev.py`. Create `docs/cli/jev.md`.
- **Interfaces.** `t3c route classify --repo REPO --work ID --input TASK.json [--context EXTRA.json]`.
  `TASK.json` holds only `intent`, `acceptance`, `interfaces`, `scope`,
  `riskFacts` and `dependencies`. Unknown keys fail, so global boilerplate
  cannot enter. The input is capped at 16 KiB. Exit 0 means accepted. Exit 4
  carries `retry-with-context` or `manager-judgment` and a reason. Exits 2 and 3
  mean invalid input or config. Exit 4 data also includes the input reference
  and policy digest (CD-2). `--repo` is an identity or checkout path in JV-1
  and RT-1, documented by each module spec.
- **Rules.**
  - One POST to the verified endpoint per call, with the configured timeout, a
    256 KiB response cap, no redirects and TLS verification.
  - The key comes from `OPENROUTER_API_KEY`, else from `jev.keyFile`. It never
    reaches argv, output, records or exception text.
  - The input is checked against the private identifier list, with PV-1a's
    loader and matcher, before it leaves the machine. A match, or a missing
    list, blocks the call.
  - Questions and criteria come from `classification` in config. The response
    must hold both answers with `type` `choice`, a choice among the options,
    probabilities for exactly the options, each finite and between 0 and 1,
    summing to 1 within 0.01, and a finite `confidence` between 0 and 1.
  - A classification is accepted only when both answers' `confidence`, not the
    chosen option's probability, reaches `jev.minConfidence`, the domain is not
    `MIXED_OR_UNCLEAR`, and the difficulty is not `INSUFFICIENT_CONTEXT`. Low
    confidence or insufficient context allows one retry with `--context` (at
    most `maxContextRetries`), then manager judgment. Mixed or unclear work goes
    to the manager directly. API errors and malformed answers go to manager
    judgment. No path selects a model.
  - Before the request, RC-1b's input recorder appends the canonical
    `routing-input`, with the current routing-policy digest. A retry's context
    is part of a new input and digest. The `routing-classification` event
    references that input and policy, and stores every field in
    [Record format](#record-format), including the response's snapshot `model`.
    A config change during the request makes its classification stale for
    selection; RT-1 requires reclassification or a fresh judgment.
- **Acceptance.** Offline tests, with an injected HTTP layer, cover every rule,
  including NaN, infinity, a wrong sum, a missing answer, an unknown choice, a
  redirect, a timeout, HTTP 4xx and 5xx, an oversized body, a refused second
  retry, and a fake key that never appears in captured output or records.
  `live/jev_contract.py` makes one synthetic call and checks the contract that
  the design records; the manager runs it after review (L2).
- **Offline tests.** `SUITE -p 'test_jev.py'`, then `SUITE`.
- **Depends on.** CF-1, RC-1c (after RC-1a and RC-1b), PV-1a.
- **Parallel with.** RT-1; L1 and L3 as their prerequisites finish.
- **Risk facts and tier.** Security boundary (a credential and an external
  service) → the challenging tier. Reviewer: challenging.
- **Review focus.** The key on every error path; strict validation; confidence
  compared, not probability; bounded retries; the identifier screen.
- **Size.** About 900 lines.

### RP-1 Router pilot with synthetic inputs

- **Objective.** Exercise Jev routing on labeled synthetic examples before the
  formal helper-backed pilot, and give Shawn results for tuning the threshold, the
  criteria and the floors.
- **Design.** [Pilot and validation][d-pilot] (router pilots; mistaken cheap
  routes recorded apart from unnecessary expensive ones);
  [Jev decision routing][d-jev].
- **Files.** Create `pilot/router-examples.json`, `live/router_pilot.py`,
  `docs/router-pilot.md` and `tests/test_router_pilot.py`.
- **Content.** At least twelve synthetic examples: software engineering, other
  work, mixed scope, easy but sensitive (at least one for each confirmed floor),
  and technical documentation. Each holds the author's expected domain, tier
  and floors, and is labeled synthetic. The runner classifies and selects each
  example under the `synthetic/router-pilot` identity, so synthetic decisions
  never mix with Task records. Its summary lists mistaken cheap routes apart
  from unnecessary expensive ones.
- **Interfaces.**
  `"$PY" -I -S <root>/live/router_pilot.py --examples pilot/router-examples.json --catalog FILE --catalog-read-at TIME`.
- **Acceptance.** Offline tests validate the examples file and run the runner
  against an injected Jev. After review and L1, the manager runs it live (L8)
  and reports the summary to Shawn. `docs/router-pilot.md` says that real task
  inputs join only after the pilot lifts the hold, and are recorded with each
  decision.
- **Offline tests.** `SUITE -p 'test_router_pilot.py'`, then `SUITE`.
- **Depends on.** JV-1, RT-1.
- **Parallel with.** EV-1, PV-1b, BT-1, HC-1, GA-1 to GA-3, PB-1, SK-1.
- **Risk facts and tier.** None of the floor facts → the medium tier. Reviewer: medium.
- **Review focus.** Coverage of every floor and of documentation; labels; no
  private data in the examples.
- **Size.** About 450 lines.

### EV-1 Evidence check

- **Objective.** Decide whether a work's evidence covers its current head, and
  list the gaps.
- **Design.** [Work cycle][d-cycle], steps 5 to 8;
  [Accepted review policy][d-review]; [Checks and publication][d-publication]
  (a check on another commit does not cover the head).
- **Files.** Create `t3compose/evidence.py` and `tests/test_evidence.py`.
  Create `docs/cli/evidence.md`.
- **Interfaces.** `t3c evidence check --repo REPO --work ID --head SHA [--pr-ready]`
  prints `verified` or the gaps. It exits 0 or 1, and 2 for invalid records.
- **Rules.** Verified needs all of the following. The head is the latest result
  commit. Every required check has a pass at that exact head from the manager,
  the current Feature lead, a Reviewer or an assigned Session; a Worker's claim
  alone does not count. The latest review covers exactly the review base and
  head, with a verdict other than "changes required". When a lead is assigned,
  only the current lead's own review counts: its thread must be current at the
  review event and at verification, and evidence path plus digest are required.
  An independent task review cannot substitute for that final-head review.
  Without a lead, an independent task review is required. Earlier reviews stay
  in history; their findings and dispositions still apply.
  Every finding of every review has a disposition, and no blocking or important
  finding is open, disputed or declined. Findings join on `(review reference,
  finding ID)`, where the reference is the review event sequence; a prior review
  cannot clear a later same-ID finding. `withdrawn` is by that review's author.
  Every acceptance criterion has evidence at the head. `--pr-ready` also needs
  a confirmed push of the head and a passing `pr verify` result. Manager delivery
  readiness is not an EV-1 input. `--repo` means an identity or checkout path.
  Exit 1 carries the gaps as one JSON value (CD-2).
- **Acceptance.** Offline tests cover: a stale review after the head moved; a
  missing check; a check on another commit; Worker-only evidence; open and
  disputed findings; a declined important finding; a finding resolved outside
  the tree; a second-review rejection; an owner decision; missing criterion
  evidence; repeated finding IDs in different review events with only one
  resolved; a new commit after verification; and a fully verified case.
  CD-4 cases accept the current lead's exact-head review and check, refuse a
  wrong or replaced lead, refuse a task final review when a lead exists, refuse
  missing lead evidence, and require an independent task final review when no
  lead exists. Delivery readiness neither grants nor blocks Task verification.
- **Offline tests.** `SUITE -p 'test_evidence.py'`, then `SUITE`.
- **Depends on.** RC-1c (after RC-1a and RC-1b).
- **Parallel with.** RP-1, PV-1b, BT-1, HC-1, GA-1 to GA-3.
- **Risk facts and tier.** None of the floor facts: bounded logic over the
  records store and RC-1a's schema → the medium tier. It compares records and is fully testable;
  PB-1 enforces the publication gate. Reviewer: medium.
- **Review focus.** Every path to `verified`; that Worker claims never count;
  exact base and head matching.
- **Size.** About 600 lines.

### PV-1b Publication privacy and pull request checks

- **Objective.** Check publishable text against the identifier list, and check
  a pull request's head, base, state and author, for every repository.
- **Design.** [t3-compose repository workflow][d-workflow] (privacy, the author
  check and failure action); [Checks and publication][d-publication] (head check).
- **Files.** Create `t3compose/privacy.py`, `t3compose/pullrequest.py`,
  `tests/test_privacy.py` and `tests/test_pullrequest.py`. Create
  `docs/cli/privacy.md` and `docs/cli/pullrequest.md`.
- **Interfaces.**
  - `t3c privacy check --repo-path P --range BASE..HEAD [--branch NAME] [--text-file LABEL=FILE ...]`
  - `t3c pr verify --repo OWNER/NAME --number N --head SHA [--base BRANCH] [--author LOGIN]`
- **Rules.**
  - Every check loads the list through PV-1a and blocks on a loader failure.
  - Privacy covers every commit message and the whole tree at every commit in
    the range, the branch name and each text file. A match that a later commit
    removed still blocks, because a push publishes every commit. `*.lock`
    files are skipped, as in the manual check. Author and committer fields are
    not checked ([finding F7][d-findings]). GitHub's squash trailers
    ([decision 7](#decisions)) sit on the base branch, normally outside a
    Task's review range, so the check makes no exception for them. If a branch
    update brings a squash commit into the range, a match there goes to Shawn.
  - Output names the field, commit, file and list line number, never matched
    text. A match blocks publication until Corrections or Shawn resolves it.
  - `pr verify` reads the pull request through the GitHub API, read-only. It
    fails when the head differs from `--head`, the base differs, the pull
    request is not open, or the author differs from `--author`. A failure
    prints "report to Shawn; not PR-ready" and exits 1. The head check applies
    to every repository; the author check applies where the repository's
    identity rules name an author. PB-1 passes the Worker App's bot login.
- **Acceptance.** Offline tests with temporary repositories cover matches in
  a message, an intermediate commit's tree, the branch name, the title and the
  body; a clean range; each list failure; output that never reveals a match;
  and `pr verify` with an injected API reader for every mismatch.
- **Offline tests.** `SUITE -p 'test_privacy.py'`,
  `SUITE -p 'test_pullrequest.py'`, then `SUITE`.
- **Depends on.** PV-1a.
- **Parallel with.** RP-1, EV-1, BT-1, HC-1, GA-1 to GA-3.
- **Risk facts and tier.** Guardrail boundary (publication privacy and pull
  request checks) → the challenging tier. Reviewer: challenging.
- **Review focus.** Fail-closed paths; intermediate commits; nothing private
  in output; failure action for every repository.
- **Size.** About 450 lines.

### BT-1 Brief templates

- **Objective.** Worker and Reviewer brief templates that carry the design's
  rules, and a command that fills and checks them.
- **Design.** [Work cycle][d-cycle], steps 2, 6 and 7;
  [Manager threads and bases][d-bases]; [Roles][d-roles];
  [t3-compose repository workflow][d-workflow] (privacy);
  [Accepted review policy][d-review].
- **Files.** Create `skill/templates/worker.md`, `skill/templates/reviewer.md`,
  `t3compose/briefs.py` and `tests/test_briefs.py`. Create
  `docs/cli/briefs.md`.
- **Worker template.** Sections, in order:
  - Role and routing: the tier, the model and the reason; stay on this
    Assignment and this model; start no agents or T3 tasks. A slot carries the
    routing policy's text directly when the Worker is agy. Under
    [decision 9](#decisions), Pi Workers also carry their instructions inline
    until Pi's instruction-file handling is verified.
  - Not alone, and where to work: preserve all edits that are not yours. Work
    only in your own worktree; never touch the owner's checkout or another
    worktree. A slot names any owner hold that applies, such as rapid-ship's.
  - Repository, commits and publication: the provisioned commit read from
    the dispatching checkout at dispatch, the start commit and the review base,
    and the HEAD check with its three outcomes. Never assume the checkout lags
    origin or reuse a provisioned commit recorded before a merge. A
    Worker that finds a commit missing does not fetch. Commit on the branch
    only; do not push, open, edit or merge pull requests, post to GitHub or run
    `gh`. An assigned lead publishes its own Task's PR; otherwise the manager
    publishes. A Worker cannot act as either publisher.
  - Read first: the repository's guides, the design, the routing policy's path,
    and the repository's settings check where one exists.
  - Owned paths, objective, interfaces, acceptance criteria and the
    repository's test-first rule.
  - Validation: the fast checks; the tree and commit-message privacy checks
    where the repository is public; heavy checks only when assigned, through
    `t3c heavy run`. Resolve the one final CPython 3.15 interpreter with
    `PY="$(uv python find 3.15)"`, then use `"$PY" -I -S <root>/bin/t3c` and
    `"$PY" -I -S -B -m unittest discover -s tests -t . -v`; reject pre-release
    evidence. Python children use `sys.executable`.
  - Commits: a conventional subject, a body that says why, and the attribution
    trailer.
  - Privacy-rewrite exception, present only in a Corrections brief issued after
    a privacy match: amend or rebase only the unpublished commits in the review
    range, to remove the match.
  - Return format: branch, worktree path, head, log, evidence for each
    criterion, each command with its result, questions, deviations with
    reasons, and a statement of what was not done.
- **Reviewer template.** Sections: independence (did not implement; a separate
  Session; edits, commits, pushes and posts nothing); role and the tier's
  reason; the exact base and head, read in the Reviewer's own worktree; what to
  read (the brief, prior findings, responses and unresolved objections); review
  questions; checks to run; findings format (ID; severity blocking, important or
  minor; `file:line`; problem; evidence; suggested fix; verified facts kept
  apart from judgments); verdict (approve, approve after fixes, or changes
  required); return format. This template serves earlier-head reviews and
  Corrections re-reviews, and final-head reviews only for Tasks without a lead.
  A current lead personally reviews its Task's final head and records path plus
  digest as CD-4 requires; no delegated Reviewer is sent that head.
- **Interfaces.**
  `t3c brief render --template worker|reviewer|PATH --values VALUES.json --out FILE`
  fills `{{NAME}}` placeholders; a `PATH` lets later Tasks render their own
  templates without changing this module. It refuses a missing value or a
  leftover placeholder, requires the inline instructions slot for agy Workers
  and, under [decision 9](#decisions), Pi Workers,
  refuses a Reviewer brief whose selection is agy, and prints the brief's
  digest for `dispatch-intent`. `t3c brief check FILE` checks the required
  sections and that the commits are full SHAs.
- **Acceptance.** The templates hold no private identifier and pass the tree
  privacy check. Offline tests cover rendering, missing values, leftover
  placeholders, inline instructions, the agy rules and digests. Every rule
  listed above appears in its template.
- **Offline tests.** `SUITE -p 'test_briefs.py'`, then `SUITE`.
- **Depends on.** HS-1, RC-1c (complete records store and atomic data writes, after RC-1a and RC-1b).
- **Parallel with.** RP-1, EV-1, PV-1b, HC-1, GA-1 to GA-3.
- **Risk facts and tier.** Shared interface change (every Assignment uses these
  briefs) → the medium tier. Reviewer: medium.
- **Review focus.** Completeness against the design; the HEAD-check wording;
  the limits of the privacy-rewrite exception; nothing private.
- **Size.** About 700 lines.

### HC-1 Heavy-check lock

- **Objective.** Run heavy checks one at a time on the machine, across manager
  threads, Feature leads and assigned Sessions.
- **Design.** [Checks and publication][d-publication]
  ([finding F6][d-findings]).
- **Decision.** An exclusive `fcntl.flock` lock on
  `.local/state/locks/heavy-check.lock` in the t3-compose checkout. Every
  coordinator runs the same helper with the same `.local/`, so the lock
  covers the machine. A dedicated runner holds the lock until the foreground
  check exits, even if the calling wrapper exits. The lock inode is created
  once and never renamed, replaced or unlinked. Holder metadata (label,
  commit, runner and check process IDs, start time) is atomically written to
  `heavy-check-status.json` beside it; it is never written into the lock file.
- **Files.** Create `t3compose/heavy.py` and `tests/test_heavy.py`.
  Create `docs/cli/heavy.md`.
- **Interfaces.**
  `t3c heavy run --repo-path WORKTREE --commit SHA --label TEXT --log FILE [--wait] -- CMD [ARGS...]`
  and `t3c heavy status`.
- **Rules.**
  - The command refuses unless the worktree's HEAD is the commit, its tree is
    clean, and it is a linked worktree rather than a repository's main
    checkout, so the owner's checkout is never used. The parent that created
    the writer first confirms its completion with `task_status`; lifecycle
    ownership stays with that parent. The lead owns the required check for a
    led Task, otherwise the manager owns it.
  - Without `--wait`, a held lock exits 75 with the holder's details. With
    `--wait`, the call blocks in the kernel until the lock frees. The assigned
    lead, or manager without a lead, runs it as a Harness background command that notifies on exit, so nothing
    polls.
  - The wrapper starts a dedicated runner that acquires the lock and starts
    the foreground check. The runner waits for the check even if the wrapper
    is killed; wrapper signals are forwarded and never release the lock
    before the check exits. The check also inherits the locked open file
    description across exec. No process unlocks it while the check is live;
    the runner closes its descriptor only after waiting for the check.
  - `status` tests the stable lock inode; metadata alone never authorizes a
    new run. A stale status file is labeled stale once the lock is free.
    Status writes cannot create another lock inode.
  - Child stdout/stderr go to `--log FILE`. Afterwards stdout holds one JSON
    summary for a `check` event: child status, HEAD/tree unchanged flags, label,
    log path and start/end times. Exit 0 requires child 0 and unchanged HEAD/tree;
    exit 1 means child failure or HEAD/tree change; 75 carries holder details;
    2/3 mean invalid input/config. Child status is never passed through (CD-2).
- **Acceptance.** Offline tests with subprocesses show that two concurrent
  runs serialize. Kill the calling wrapper while its check keeps running: a
  second run stays busy until that check exits, then can acquire the lock.
  Repeated status rewrites preserve the lock inode and cannot admit a second
  check. Tests also cover signal forwarding, release after the check exits,
  stale metadata, each refusal, log capture, the summary's fields and CD-2's
  exit mapping, including child 0 with a changed tree and arbitrary child exits.
- **Offline tests.** `SUITE -p 'test_heavy.py'`, then `SUITE`.
- **Depends on.** HS-1, RC-1c (complete records store and atomic data writes, after RC-1a and RC-1b).
- **Parallel with.** RP-1, EV-1, PV-1b, BT-1, GA-1 to GA-3.
- **Risk facts and tier.** Subtle invariants (concurrency) → the challenging tier, although the Task is small. Reviewer: challenging.
- **Review focus.** Lock lifetime; descriptor inheritance; the main-checkout
  refusal; the background-run pattern.
- **Size.** About 450 lines.

### GA-1 Worker App token: minting, scope and revocation

- **Objective.** Move the interim token step's minting into the helper as
  `t3c publish check`, with its reviewed behavior, revocation included.
- **Design.** [t3-compose repository workflow][d-workflow];
  [publication decision 1][d-pubdec].
- **Source.** The interim token step in `.local/`, at the hash that its latest
  security review approved for routine use. The manager names the file and its
  hash in the brief. The findings that review left to this plan are in GA-2 and
  GA-3. The port holds no private value: the App's ID, installation account
  and Keychain item stay in `.local/github-app.json`.
- **Files.** Create `t3compose/github_app.py` and `tests/test_github_app.py`.
  Create `docs/cli/github_app.md`.
- **Kept behavior.**
  - The App config is strict: permission names `contents` and `pull_requests`
    only, levels `read` or `write`, and a repository owner equal to the
    installation account, ignoring case.
  - The key comes from the Keychain item that the App config names. It is
    captured, never printed, and reaches `openssl` only through an anonymous
    pipe, never through a file or argv.
  - The RS256 JWT has `iat` 60 seconds before now and `exp` 540 seconds after
    now. Exactly one installation must match the configured account.
  - The token request is scoped to the one repository and the configured
    permissions. `check` passes only when the token reaches exactly that
    repository with exactly those permissions plus `metadata: read`.
  - Redirects are refused. An HTTP error reports GitHub's message without
    headers or secrets. A 422 also says that Shawn must add the repository to
    the installation or grant the requested permissions.
  - A filter removes secrets from all output. The entry's `-I -S` check (HS-1)
    covers the interim step's interpreter rule.
- **Kept from the first review's findings.** Every token is revoked with
  `DELETE /installation/token`. `check` fails when revocation fails, and names
  `expires_at`. An empty 2xx body reads as no content.
- **Interfaces.** `t3c publish check` prints the App slug, the installation,
  its repository selection, the granted permissions, `expires_at` and the
  repositories. It exits 0 or 1.
- **Acceptance.** The interim step's offline tests for these behaviors,
  revocation included, are ported with synthetic values and throwaway keys from
  `openssl genrsa` in a temporary directory, and they pass. No test touches the
  Keychain or the network. After review, the manager runs
  `t3c publish check` once (L4) and compares its output with the interim step's
  first live check.
- **Offline tests.** `SUITE -p 'test_github_app.py'`, then `SUITE`.
- **Depends on.** HS-1.
- **Parallel with.** RP-1, EV-1, PV-1b, BT-1, HC-1.
- **Risk facts and tier.** Security boundary (a private key and repository
  credentials) → the challenging tier. Reviewer: challenging.
- **Review focus.** Every path of the key, the JWT and the token; pipe and
  file-descriptor handling; the scope checks; revocation on every path; a
  comparison with the reviewed source.
- **Size.** About 1,300 lines, mostly ported tests.

### GA-2 Worker App token: running a command

- **Objective.** Move the interim step's `run` into the helper as
  `t3c publish run`, with its environment, Git override, signal handling and
  revocation.
- **Design.** [t3-compose repository workflow][d-workflow] (no fallback to
  Shawn's stored credentials or SSH key); [publication decision 1][d-pubdec].
- **Source.** As for GA-1.
- **Files.** Create `t3compose/token_run.py` and `tests/test_token_run.py`.
  Create `docs/cli/token_run.md`.
- **Kept behavior.**
  - The child receives the token only in `GH_TOKEN`. `GITHUB_TOKEN` is removed.
  - Git config passed through `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_n` and
    `GIT_CONFIG_VALUE_n` clears every credential helper, including URL-specific
    ones, and sets one helper for `https://github.com` that reads the token
    from the environment. Inherited `GIT_CONFIG_*` and `GIT_CONFIG_PARAMETERS`
    cannot add a helper back.
  - `protocol.ssh.allow=never` refuses SSH.
  - The key and the JWT never reach the child. The child's exit status passes
    through.
  - From the first review's findings: the child's environment also loses
    `GIT_ALLOW_PROTOCOL`, so SSH stays refused, and `GIT_SSL_NO_VERIFY`, every
    `GIT_TRACE*`, `GIT_CURL_VERBOSE`, `GIT_REDACT_COOKIES`, `GH_HOST`,
    `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN` and `GH_DEBUG`. Revocation
    runs in a `finally` block after the child exits, including after a failure
    or an interruption; a failed revocation prints a warning and keeps the
    child's status.
- **Added from the second review (plan input 12).**
  - V1: `run` forwards SIGTERM and SIGHUP to the child, waits for it, then
    revokes the token.
  - V3: build the sanitized environment before any preflight Git read. It
    removes `GIT_CONFIG` as well as the inherited `GIT_CONFIG_*` and other
    overrides above. GA-3 inspects Git with that environment; only after the
    checks pass is the token and controlled helper config added for execution.
    Inspection and execution therefore read the same underlying config files.
- **Interfaces.** `t3c publish run --status-file FILE -- CMD [ARGS...]`,
  started from the repository directory. It is the sole `PassThrough` command:
  child stdout/stderr remain unchanged. CD-2's atomic mode-0600 status file is
  required: before start, `{"childStarted": true, "childStatus": null}`; after
  exit, `{"childStarted": true, "childStatus": N, "revocation": "ok" | "failed"}`;
  pre-start refusal/failure, `{"childStarted": false, "refusal": "<kind>"}`.
  GA-3 refusal before minting exits 1, invalid input/config 2/3; once started,
  the child status is preserved, including after a revocation warning.
  No file after exit means no child started; true/null is unknown and needs
  reconciliation. Never infer start from exit status or parse child output.
  PB-1 calls this same logic in-process and records the fields.
- **Acceptance.** Ported and new offline tests pass. They include a real
  `git credential fill` in a temporary repository with an isolated `HOME` and
  `XDG_CONFIG_HOME`, decoy credential helpers and `GIT_CONFIG_NOSYSTEM=1`,
  where the token wins and the decoy never answers; SSH refusal with
  `GIT_ALLOW_PROTOCOL` set; and revocation after a failing child, an
  interrupted one and a forwarded SIGTERM. Tests cover every status-file
  state, atomic mode 0600, child exits that collide with helper refusal codes,
  absent-file no-start and true/null unknown outcomes; child output remains
  unchanged and revocation failure preserves status.
- **Offline tests.** `SUITE -p 'test_token_run.py'`, then `SUITE`.
- **Depends on.** GA-1.
- **Parallel with.** RP-1, EV-1, PV-1b, BT-1, HC-1.
- **Risk facts and tier.** Security boundary → the challenging tier.
  Reviewer: challenging.
- **Review focus.** Environment scrubbing; the completeness of the helper
  reset; signal handling and revocation timing.
- **Size.** About 1,000 lines.

### GA-3 Worker App token: refusals before minting

- **Objective.** Move the interim step's checks of the Git setup into the
  helper, add the second review's refusals, and turn its usage rules into
  checks, so that `run` refuses before it mints a token.
- **Design.** [t3-compose repository workflow][d-workflow] (no fallback to
  Shawn's stored credentials or SSH key).
- **Source.** As for GA-1.
- **Files.** Create `t3compose/token_preconditions.py` and
  `tests/test_token_preconditions.py`. Modify `t3compose/token_run.py` to call
  the checks before minting. Update `docs/cli/token_run.md`.
- **Kept behavior.** From the first review's findings: `run` builds GA-2's
  sanitized environment first and passes it to every preflight Git invocation.
  It refuses when Git's effective config, read from the directory `run` starts
  in with that environment, has an
  `extraheader`, a `url.*.insteadOf` or a `url.*.pushInsteadOf`, or when
  `~/.netrc` names `github.com`. It names the kind of setting, never its value.
- **Added from the second review (plan input 12).**
  - V2: `run` also refuses `trace2.envVars` and a false-like `http.sslVerify`
    or `http.<url>.sslVerify`.
  - The usage rules become checks: `run` refuses `git -C` or `git -c` before
    the Git subcommand, and a shell as the command, because the checks read
    Git's config from the directory `run` starts in.
- **Interfaces.** No new command; `t3c publish run --status-file FILE -- CMD`
  exits 1 for a GA-3 refusal, naming only the kind of setting/command form and
  minting nothing. It atomically writes mode-0600
  `{"childStarted": false, "refusal": "<kind>"}`. Invalid input/config exits 2/3
  before child start use the same no-start form. Callers use the file, never
  the status or child output, to distinguish refusal from a child's own exit.
- **Acceptance.** Offline tests under an isolated `HOME`, with decoy configs,
  cover each refusal and each refused command form, and show that a refusal
  happens before any token request. A decoy `GIT_CONFIG` points to a clean
  file while the repository config holds a forbidden setting: preflight must
  still find the setting and refuse. The test asserts that inspection and
  execution receive the same sanitized base environment. Every refusal
  asserts the status file's false `childStarted` and public refusal kind, with
  no secret value. Matching child exit codes never stand in for this evidence.
- **Offline tests.** `SUITE -p 'test_token_preconditions.py'`,
  `SUITE -p 'test_token_run.py'`, then `SUITE`.
- **Depends on.** GA-2.
- **Parallel with.** RP-1, EV-1, PV-1b, BT-1, HC-1.
- **Risk facts and tier.** Security boundary → the challenging tier.
  Reviewer: challenging.
- **Review focus.** That every refusal precedes minting; false-like boolean
  spellings; command forms that could slip past the checks.
- **Size.** About 700 lines.

### PB-1 Publication flow for t3-compose

- **Objective.** One command per publication step, which applies every check in
  order and records the intent and the result.
- **Design.** [Checks and publication][d-publication];
  [t3-compose repository workflow][d-workflow]; [Work cycle][d-cycle], step 8.
- **Files.** Create `t3compose/publish.py` and `tests/test_publish.py`.
  Create `docs/cli/publish.md`.
- **Interfaces.**
  - `t3c publish push --repo-path P --work ID --head SHA --branch NAME`
  - `t3c publish pr --work ID --head SHA --branch NAME --base main --title-file F --body-file F`
- **Rules.**
  - `push`, in order: the helper's own tracked files must be clean, and its
    commit is recorded through `t3compose.cli.helper_info()`; `evidence check`
    must pass for the head under CD-4;
    `privacy check` must pass for every commit that the push would publish
    (reachable from the head and from no ref that `git ls-remote` reports), and
    for the branch name; a change under `.github/workflows/` in those commits
    stops with exit 5, because Shawn pushes such changes; an `op-intent` is
    recorded; `git ls-remote` must show the branch absent, already at the
    head, or at the prior head from the latest confirmed push result for that
    branch. Already at the head records the result without a push. An update
    requires that exact confirmed prior head and a proof that it is an
    ancestor of the new reviewed head; unexpected movement and
    non-fast-forward updates stop before any push. The helper records the
    expected prior head (or absence), rechecks it immediately before pushing,
    then `publish run --status-file FILE -- git push --force-with-lease=<ref>:<expected> <https URL> <sha>:<ref>`.
    The explicit lease compares the remote to the recorded prior head (empty
    for an absent branch) during the push. It is used only after the ancestry
    proof, so it cannot authorize a history rewrite; bare `--force` and leases
    without an explicit expected value are forbidden. Any intervening movement
    fails the lease. `git ls-remote` confirms; an `op-result` is recorded. A push to an explicit
    URL adds no remote-tracking ref to the owner's checkout.
  - `pr`, in order: `privacy check` of the title, the body and the branch name;
    an `op-intent`; an existing pull request for the branch is reused, never
    duplicated; `publish run --status-file FILE -- gh pr create` with explicit `--repo`, `--head`
    and `--base`; `pr verify` with the App's bot login, read through GA-1's
    config loader; an `op-result`. After Corrections on an existing pull
    request, rerun evidence and privacy checks for the new head, its full
    review range, and the branch, title and body, even for commits already
    published. A privacy match in published history goes to Shawn; the
    unpublished-only rewrite exception cannot remove it. Reuse the pull
    request, then verify its new head, base, state and App author before
    recording PR-ready.
  - Both commands start `publish run` from the repository directory and pass no
    `-C`, `-c` or `cd`.
  - A failed check stops the flow and names the next step: a Corrections round,
    a fix to the publishing coordinator's own text, or a report to Shawn.
  - Both use GA-2's same run logic in-process and record `childStarted`,
    `childStatus` and refusal/revocation fields. No status file after exit means
    no child started; true/null requires reconciliation before a retry. Neither
    exit status nor child output proves start. An unknown push is reconciled
    with `git ls-remote`; an unknown PR operation with the repository's PR state,
    so resumption does not duplicate an operation.
  - Only the assigned lead publishes its own Task's PR; the manager publishes
    Tasks without a lead. Identity/privacy/head/author gates stay the same.
    These helper commands only push and open/reuse pull requests; PB-1 adds no
    merge command. The publishing coordinator then calls `link_pull_request`
    and records it. Subsequently, the assigned t3-compose lead squash-merges
    its own Task's pull request as the Worker App under decision 19, verifies
    the merge and result, and sends the one-line manager handoff. The merge
    follows all decision-19 conditions and leaves GitHub to generate the squash
    title and message; it is outside PB-1's command behavior.
- **Acceptance.** Offline tests, with injected runners, cover each stop
  condition, the order of records, and an interrupted flow that resumes
  without a second push or a second pull request; a Corrections update from
  the confirmed prior head; stale evidence or privacy on that update;
  unexpected remote movement; a non-fast-forward head; and a refused push
  when the branch changes after preflight; refusal versus identical child
  exit codes; absent-file no-start; true/null unknown reconciliation; lead and
  no-lead publication without bypassing CD-4. The first pull request after
  PB-1 is installed is published through it (L5).
- **Offline tests.** `SUITE -p 'test_publish.py'`, then `SUITE`.
- **Depends on.** RC-1c (after RC-1a and RC-1b), EV-1, PV-1b, GA-3.
- **Parallel with.** RP-1.
- **Risk facts and tier.** Security and guardrail boundaries (the publication
  identity and privacy) → the challenging tier. Reviewer: challenging.
- **Review focus.** The order of checks; idempotent resumption; push by commit
  to an explicit URL; ancestry proof plus exact lease; no history rewrite or
  merge in the helper commands, distinct from the lead's subsequent authorized
  own-Task t3-compose squash merge.
- **Size.** About 700 lines.

### SK-1 Manager skill and installation

- **Objective.** The skill that runs the work cycle with the helper, and its
  user-level installation.
- **Design.** [Scope][d-scope] (installation); [Roles][d-roles];
  [Work cycle][d-cycle]; [Manager threads and bases][d-bases];
  [Checks and publication][d-publication]; [Design refinement][d-refine].
- **Files.** Create `skill/SKILL.md`, `t3compose/skill_install.py` and
  `tests/test_skill.py`. Modify `README.md` (installation, updates and status)
  and `AGENTS.md` (status). Create `docs/cli/skill_install.md`.
- **`SKILL.md` covers.**
  - Hard rules: the manager makes no project edits; it does not move or write
    the owner's checkout beyond the design's list; Workers commit on their
    branch only; the assigned lead publishes its Task's own PR, otherwise the
    manager publishes in managed repositories. In t3-compose every Task has a
    lead, who alone squash-merges its own Task's PR as the Worker App after its
    exact-final-head review and passed required checks, subject to decision 19;
    the manager does not merge and no lead merges another Task's PR. Workers
    and Reviewers never push, post or merge; agy is never a Reviewer.
    Earlier-head/Corrections reviews
    stay independent; a led Task's final head is personally reviewed by its
    current lead, with no separate Reviewer on that head, while an unled Task
    requires an independent final review. Preserve dated history and decision
    16's temporary prose exception; t3-compose agents do no rapid-ship work,
    and formal L9 keeps its `t3c` and handoff gates.
  - Start and restart: `t3c version`, `t3c config check`, `t3c work list` and
    `t3c work reconcile`; then `task_status` and Git for each open item,
    recorded as `reconciliation` events, before anything new.
  - The work cycle, with the command for each step. A base that is not local is
    fetched with a plain `git fetch` before the brief is written. Routing uses
    the current input and policy references; Corrections and review scopes
    follow the tier rules in this plan.
  - Dispatch: read the actual checkout HEAD at every dispatch, even after T3
    fast-forwards it; preflight the three commits; `dispatch-intent` before
    `delegate_task`; async mode, worktree isolation, an explicit provider and
    model, and a stable `clientRequestId` of the form
    `t3c-<work>-<role>-r<round>`, which the task title repeats; then
    `dispatch-confirmed` with the `taskId`. An intent without a `taskId` is
    resolved from T3 first, through its thread list or a search for the title.
    The manager reissues the same call, with the same `clientRequestId` and
    brief, only when T3 shows no such task and L7 showed that T3 deduplicates
    by `clientRequestId`. Otherwise it reports to Shawn.
  - Briefs carry agy Workers' instructions inline. Under
    [decision 9](#decisions), Pi Workers also carry their instructions inline
    until Pi's instruction-file handling is verified. Under decision 9, Pi's
    OpenRouter key stays in its own configuration; the helper never reads, passes or logs it, and
    briefs, source, logs and argv never hold it.
  - Results by worktree path and commit. Delegated reviews: a new
    `delegate_task` per earlier-head/Corrections round, or final review without
    a lead. A lead holds requirements/design/interfaces/diff/callers/tests and
    evidence, personally reviews the exact final head, and records CD-4's
    reviewer form and evidence path plus digest. Manager-only lead assignments
    and review sequence references preserve history and finding identity.
    Disputes: one second independent review, then Shawn.
  - Heavy checks through `t3c heavy run`, owned by the lead when assigned or
    otherwise the manager, machine-wide serialization after writer completion
    in a clean exact-head linked worktree. Verification through
    `t3c evidence check`, including the current lead's required-check evidence.
    Under decision 17, a lead puts the machine prerequisites and integration
    evidence in the pull request body, tells Shawn in its own thread when the
    pull request is ready, never reports pull request readiness to the manager,
    and sends no progress reports; it sends the manager one line when its pull
    request merges, plus blockers, cross-Task or shared-interface changes, new machine
    prerequisites, owner rule changes and agy exhaustion. Manager delivery
    readiness covers cross-feature integration, operational readiness and
    integration tests—setting the expected integration tests and machine
    prerequisites at Task start and checking `main` and the machine after a
    merge—without adding a manager pre-merge readiness approval or repeating
    lead final review; it is separate from verified/PR-ready and is not an EV-1
    input.
  - Publication: `t3c publish push` and `t3c publish pr` on t3-compose.
    Elsewhere, the repository's own commands with a recorded intent and result,
    then `t3c pr verify`. Then `link_pull_request`. For t3-compose, the assigned
    lead proceeds to its own Task's authorized squash merge without waiting for
    Shawn. It verifies the open, non-draft, clean and mergeable PR at the exact
    reviewed head, passed required checks and hosted checks if present, App
    author, privacy,
    owned paths and absence of workflow changes or open owner questions. GitHub
    generates the squash title/message; decision 7's generated-trailer exception
    applies, and agents never write Shawn's email. The lead verifies the merge
    and result, then sends the one-line manager handoff. The manager checks
    `main` and the machine and starts dependent Tasks and leads after
    dependencies merge. PB-1 still implements only push/pr commands; no new
    helper merge command is authorized. Other repositories keep their own
    merge rules. A privacy match leads to a
    Corrections round, a fix to the publishing coordinator's own text, or Shawn. At a Keychain
    prompt, the manager asks Shawn to choose Allow, not Always Allow.
  - Quota: only confirmed exhaustion and successful availability checks are
    recorded; tool and correctness failures are not quota evidence. Design
    refinement: commissioned
    at the challenging tier. Earlier-head/Corrections reviews are independent;
    a current lead reviews a led Task's final design head, otherwise an
    independent Reviewer does. A change to scope or an owner decision goes to
    Shawn first.
  - For managed repositories, Feature leads stay off until FL-2. This package
    enablement gate does not change the t3-compose build's assigned leads.
  - Updates: the installed version is the owner's checkout; `t3c version` shows
    which commit runs. After a merge, read HEAD first. Only when T3 has not
    already fast-forwarded clean `main` may the manager run `git pull --ff-only`
    under decision 2. Dirty, divergent or non-`main` checkouts go to Shawn.
- **Interfaces.** `t3c skill install --skills-dir DIR [--name t3-compose-manager]`
  creates `DIR/NAME` as a symlink to `skill/`. It refuses an existing file, or a
  link that points elsewhere. `t3c skill status --skills-dir DIR`.
- **Acceptance.** Offline tests cover install and status in temporary
  directories. A test checks that every `t3c` command named in `SKILL.md`
  exists, and that every registered command appears in `SKILL.md`. The skill
  resolves stable CPython 3.15 with `PY="$(uv python find 3.15)"` after the
  owner setup `uv python pin 3.15 --global`, names `"$PY" -I -S <root>/bin/t3c`
  and the `SUITE` invocation, and refuses pre-release evidence. It documents
  CD-2's data-on-nonzero exits, required publish status file, heavy summary and
  shared-module ownership. It states decision 19's own-Task t3-compose lead
  merge authority and conditions, preserves all Worker/Reviewer bans and other
  repositories' rules, distinguishes push/pr commands from merge authority,
  and includes merge/result verification and the one-line manager handoff.
  Installation is L6.
- **Offline tests.** `SUITE -p 'test_skill.py'`, then `SUITE`.
- **Depends on.** CF-1, RC-1c (after RC-1a and RC-1b), EV-1, JV-1, RT-1, PV-1b, GA-1, GA-2, GA-3, PB-1,
  HC-1, BT-1, and L7's result.
- **Parallel with.** RP-1.
- **Risk facts and tier.** Shared interface change (it names every command).
  The skill describes guardrails but changes none, and a floor applies to what
  a Task changes → the medium tier. Reviewer: medium.
- **Review focus.** Agreement with the design, rule by rule; the restart and
  retry procedures; command usage; nothing private.
- **Size.** About 800 lines.

### PH-1 Pilot handoff

- **Objective.** Give the rapid-ship-bound manager thread what it needs to
  start the pilot: the kickoff template, the restart procedure and the pilot
  checklist.
- **Design.** [Pilot and validation][d-pilot];
  [Taking over existing Claude work][d-takeover];
  [Manager threads and bases][d-bases]; [re-home decisions 1 and 2][d-rehome].
- **Files.** Create `skill/templates/manager-kickoff.md`, `docs/restart.md`,
  `docs/pilot.md` and `tests/test_pilot_docs.py`.
- **Interfaces.** No new command. The kickoff template's placeholders are the
  pointers listed below, rendered with `t3c brief render`. The procedures use
  `t3c work snapshot`, `t3c work reconcile`, `t3c event add` and T3's
  `task_status`.
- **`docs/restart.md`.** Before a restart, the manager writes every pending
  event, takes a checkpoint (last sequence and prefix digest) with
  `t3c work snapshot`, then appends a before-phase `restart` event referring
  to that checkpoint. The restart uses the way L7 verified to clear the
  conversation while keeping the thread. Afterwards the manager loads the
  skill and recomputes the saved prefix with `snapshot --through-seq`,
  excluding the restart event and later events. It verifies the digest,
  reconciles open items with `task_status` and Git, then appends the after-phase
  event referencing the same checkpoint and before event, and records live
  changes. The same thread keeps managing its children.
- **Kickoff template.** Pointers only: the skill's name, the repository
  identity, where the records and the routing policy are, where the takeover
  dump is, and the checkpoint sequence and prefix digest. It names no task and repeats no
  state, so the thread must reconstruct its work state from the records.
- **`docs/pilot.md`.**
  - Before the kickoff, the build manager writes one handoff record under
    rapid-ship's identity: pointers, the hold's end condition, the pilot
    criteria, and the next action, which is to reconcile the takeover. It
    takes a checkpoint before the `handoff` event and stores it in that event.
    After reconciliation, the `writer-transfer` event carries its own
    pre-transfer checkpoint. The recipient verifies both saved prefixes and
    current writer ownership before acting. Writing this record resumes, reviews, checks and
    publishes no rapid-ship task, and chooses no pilot task. Decision 16's
    separate temporary prose manager does not satisfy or remove this formal
    handoff gate; it reconciles old work and needs Shawn's approval to dispatch.
  - First restart: the thread's start. The handoff and transfer prefixes must
    match their checkpoints; the whole current record has since grown.
  - Takeover: the design's reconciliation of the old manager's state. The
    shared-isolation exception is tested before any recovery brief relies on
    it. The manager confirms that rapid-ship's publication commands can publish
    a given commit; if they push only a branch, it confirms that the branch's
    head equals the reviewed head before and after the push.
  - One pilot Task chosen by the design's criteria; the in-flight restart in
    the same thread, by `docs/restart.md`; one bounded agy task; one
    design-refinement task.
  - Evidence: the design's list for the first live pilot.
  - Router pilots: synthetic inputs until the formal `t3c` pilot starts, then
    real task inputs as well, recorded with each decision. The temporary prose
    exception grants no earlier helper-router access to rapid-ship work.
- **Acceptance.** The files hold no private identifier. Every pilot criterion
  in the design maps to a checklist step. The restart procedure cites L7's
  result and uses RC-1c's tested checkpoint contract for both restart phases,
  handoff and writer transfer. `tests/test_pilot_docs.py` renders the kickoff
  template with `t3c brief render` and checks that every `t3c` command the documents name
  exists. If L7 found no way to clear the conversation while keeping the
  thread, PH-1 records the gap and the manager asks Shawn before the handoff.
- **Offline tests.** `SUITE -p 'test_pilot_docs.py'`, then `SUITE`.
- **Depends on.** RC-1c (after RC-1a and RC-1b), SK-1 and L7.
- **Parallel with.** RP-1 if it is still running.
- **Risk facts and tier.** None: the Task writes procedures and a template, not
  recovery code; L7 supplies the live evidence → the medium tier.
  Reviewer: medium.
- **Review focus.** That the kickoff leaks no state that would make the restart
  test trivial; the order of the hold, the reconciliation and the first
  dispatch; that the in-flight restart keeps the dispatching thread.
- **Size.** About 500 lines.

### FL-1 Nested-lifecycle pilot

- **Objective.** After the first pilot, prove native nested delegation,
  escalation delivery and recovery in a scratch pilot, before any real Feature
  lead.
- **Design.** [Feature lead options][d-leads];
  [decision 1 after grill-with-docs][d-grill].
- **Files.** Create `skill/templates/feature-lead.md`, `docs/feature-leads.md`
  and `tests/test_feature_lead.py`.
- **Interfaces.** No new command. The lead brief renders with
  `t3c brief render`; a lead writes its records with the `lead:` writer form
  from [Record format](#record-format).
- **Content.** The lead brief covers one approved feature brief, decomposition
  within it, the lead's own Worker and Reviewer dispatches, its own records as
  their single writer, and upward reports to the manager when its pull
  request merges (one line) or on blockers, cross-Task or shared-interface
  changes, new machine prerequisites, owner rule changes and agy exhaustion. A lead makes no project edits and creates no
  leads. It cannot approve its own implementation because it implements
  nothing; it personally reviews its Task's exact final head with requirements,
  design, interfaces, diff, callers, tests and evidence in context. No separate
  Reviewer reviews that final head; earlier-head/Corrections reviews remain
  independent. It owns all required checks including heavy ones, publishes its
  own Task's PR under the unchanged identity/privacy gates with the machine
  prerequisites and integration evidence in the PR body, tells Shawn in its own
  thread when the PR is ready, never reports PR readiness to the manager, and
  sends no progress reports (decision 17). For t3-compose only, every Task has
  a lead, who proceeds without waiting for Shawn and squash-merges its own Task's
  PR as the Worker App after its exact-final-head review and passed required
  checks, subject to decision 19's merge conditions. The lead verifies the
  merge and result and sends the one-line manager handoff; the manager does
  not merge or add a pre-merge approval and starts dependent Tasks/leads after
  dependencies merge. No new helper merge command is authorized. Other
  repositories keep their own merge rules. CD-4's manager-only lead assignment,
  lead reviewer
  form without `taskId`, evidence path/digest and uniform review sequence
  references are required; writer transfers and parent lifecycle ownership stay
  intact. The manager sets expected integration tests and machine prerequisites
  at Task start and checks `main` and the machine after a merge, without an
  extra pre-merge readiness approval or repeated final-head review; manager
  delivery readiness remains separate and is not EV-1 input.
  `docs/feature-leads.md` gives the judgment criteria for creating a lead, its route (the medium tier; the challenging
  tier for challenging decisions) and the scratch pilot's procedure.
- **Live (L10, scratch).** A scratch lead records an intent in its own record,
  then dispatches its own Worker. An owner question reaches the manager through
  T3's pending-question mechanism or a verified upward report. The lead stops
  mid-Task; the manager reconciles the lead's thread, its children's statuses
  and its artifacts, and a replacement takes over by `writer-transfer` without
  a duplicate child. A record write is interrupted and recovered without a
  duplicate dispatch.
- **Acceptance.** The template renders. The documents hold no private
  identifier. The scratch procedure exercises a lead's own final review and
  required-check evidence, manager-only lead assignment and replacement,
  own-Task publication authority with prerequisites and integration evidence in
  the PR body without a duplicate final Reviewer, and the distinct own-Task
  t3-compose lead merge authority and decision-19 conditions (no live merge
  is performed by the scratch procedure). It covers merge/result verification,
  the one-line handoff, manager continuation after dependencies merge, and
  decision 17's
  reporting and delivery rules (telling Shawn in the lead's thread when the PR
  is ready, no ready or progress reports to the manager, one-line merge and
  allowed exception reports, and manager delivery checks at Task start and
  after merge). L10's evidence is recorded in `.local/` and reported to Shawn.
- **Offline tests.** `SUITE -p 'test_feature_lead.py'`, then `SUITE`.
- **Depends on.** The first pilot's completion.
- **Parallel with.** None planned.
- **Risk facts and tier.** None for the files: a template and procedures. The
  live pilot is the manager's work → the medium tier. Reviewer:
  medium. A defect that L10 finds in recovery code is fixed by a separate
  Task at the challenging tier; its enumerated Corrections use the easy tier.
- **Review focus.** One writer per record across the manager and the lead; no
  duplicate children; escalations that cannot be lost.
- **Size.** About 500 lines.

### FL-2 Enable Feature leads

- **Objective.** Turn Feature leads on in the skill once L10 passes.
- **Design.** As for FL-1.
- **Files.** Modify `skill/SKILL.md`.
- **Interfaces.** None new; the skill uses FL-1's template and RT-1's
  `feature-lead` role.
- **Exact text.** Replace "For managed repositories, Feature leads stay off until FL-2." with:
  "In managed repositories, Feature leads are optional after L10 passes. Create
  one for several dependent Tasks, repeated local integration and review, or enough parallel
  work to need a local coordinator. Use one lead level. Route coordination to
  the medium tier and challenging decisions to the challenging tier. Only the
  parent that created a child manages its lifecycle. Use the Feature lead
  template. The current lead personally reviews its Task's exact final head,
  owns required checks and publishes its own Task's PR with the machine
  prerequisites and integration evidence in the PR body; no separate Reviewer
  reviews that head. Earlier-head reviews stay independent; without a lead,
  an independent Reviewer clears the final head and the manager publishes.
  A lead tells Shawn in its own thread when the PR is ready, never reports PR
  readiness to the manager, and sends no progress reports; it sends the manager
  one line when its pull request merges, plus blockers, cross-Task or
  shared-interface changes, new machine prerequisites, owner rule changes and agy exhaustion.
  Record CD-4 lead assignments, review provenance and evidence. At Task start
  the manager sets expected integration tests and machine prerequisites, and
  after a merge it checks `main` and the machine, without an extra pre-merge
  readiness approval or repeated final-head review; manager delivery readiness
  remains separate from Task verification and is not an EV-1 input.
  In t3-compose every Task has a lead, who squash-merges only its own Task's PR
  as the Worker App after its own exact-final-head review and passed required
  checks under decision 19's merge conditions. The lead verifies the exact
  reviewed head, App author, privacy, open non-draft and clean/mergeable state,
  passed hosted checks if present, owned paths, no workflow changes and no open
  owner question. GitHub generates the squash title/message; the generated
  co-author trailer exception applies, and agents never write Shawn's email.
  The lead verifies the merge and result and sends its one-line handoff; the
  manager does not merge and starts dependent Tasks/leads after dependencies
  merge, without waiting for Shawn. Workers/Reviewers never merge, a lead never
  merges another Task's PR, and other repositories keep their own merge rules.
  No new helper merge command is authorized; workflow changes, routing values
  and model placement, L9, product decisions and anything unclear remain Shawn's."
- **Acceptance.** Only that replacement. It gives the design's criteria, one
  lead level, the lead's route, the parent lifecycle rule, and decision 17's
  reporting and delivery rules, with decision 19's t3-compose-only own-Task
  lead merge authority, merge/result verification and one-line handoff. All
  merge conditions and Worker/Reviewer bans remain; no helper merge command
  is introduced. The `SKILL.md` command test passes.
- **Offline tests.** `SUITE`.
- **Depends on.** FL-1 and L10.
- **Parallel with.** None planned.
- **Risk facts and tier.** None; the brief supplies the exact replacement
  after FL-1's evidence → the easy tier. Reviewer: medium, never agy.
- **Review focus.** Agreement with the design's Feature lead options; leads
  stay optional in managed repositories; every t3-compose Task has a lead.
- **Size.** About 10 lines; the exact replacement needs no separate module.

## Live checks and owner steps

| ID | When | Who | What | Label |
|---|---|---|---|---|
| L1 | after CF-1 | Shawn, or the manager at his instruction | Add the version-2 sections to the routing config with Shawn's values; `t3c config check` passes | owner step |
| L2 | after JV-1 and L1 | manager | `live/jev_contract.py`: one call to the configured model | live, synthetic |
| L3 | after RT-1 and L1 | manager | Save a real catalog; run `live/catalog_shape.py` and one `t3c route select` on a synthetic work | live |
| L4 | after GA-1 | manager | `t3c publish check` | live |
| L5 | after PB-1 | assigned lead for its Task, otherwise manager | Publish the next pull request with `t3c publish push` and `t3c publish pr` | live |
| L6 | after SK-1 | Shawn, or the manager at his request | `t3c skill install`; a new manager Session lists the skill | live |
| L7 | before SK-1 | manager, with Shawn's hand if needed | Restart mechanism and `clientRequestId` retry, in a scratch thread | live |
| L8 | after RP-1 and L1 | manager | `live/router_pilot.py` | live, synthetic |
| L9 | after the handoff | rapid-ship manager thread | The pilot | live |
| L10 | after the pilot | manager | The nested scratch pilot (FL-1) | live |
| L11 | after RT-1 and Shawn places each supported choice in config | manager | Verify both medium Worker choices and role limits under [decisions 9 and 12](#decisions), as described below | live, synthetic |

Under [decisions 9 and 12](#decisions), L11 covers both GLM 5.3 and Argon
(`gemini-4-argon-high`). It runs for each after RT-1 and Shawn's placement of
that choice in config, using only the catalog's exact identifiers and options.
Shawn chose Argon as the medium software-engineering Worker preference with
Sol as its one fallback; the Engineering manager applies the private config.
GLM remains unplaced. L11 is independent of the handoff gate.

The manager saves a live catalog. For GLM it confirms that the Pi instance lists
GLM 5.3 with the requested options and `canRunChildTask: true`. For Argon it
confirms which pool accounts offer `gemini-4-argon-high` with the requested
options and `canRunChildTask: true`. The dated two-of-three observation in
decision 12 is not a substitute for this check. Run `t3c route select` once per
role on synthetic work for each placed choice: a Worker may get it; Argon must
resolve only to an offering, available account. Reviewers and Feature leads
never get either choice; each uses one eligible fallback or exits 5. Neither
choice serves the text helper or master. Record and report the selections and
any fallback. Record and report every GLM use's per-token billing; Argon remains
covered by the agy subscription.

L7 tests two behaviors in a scratch thread bound to the t3-compose root, with a
trivial read-only child. First, the ways to clear the conversation while
keeping the thread: the Harness's own clear command, if T3 passes it to the
provider, and any T3 control that starts a new provider session in the same
thread. A way passes when the same thread, with no prior conversation, reads the
child with `task_status` and receives its completion. Second, whether a repeated
`delegate_task` with the same `clientRequestId` returns the existing task and
starts no second child. The results are recorded in `.local/`.

L1 and L6 write outside `.local/state/`, so the manager runs them only at
Shawn's explicit request. After each merge, the manager reads the checkout's
actual HEAD. If T3 has not already fast-forwarded it, the manager may run
`git pull --ff-only` on the owner's clean `main` ([decision 2](#decisions)).
At every dispatch it reads HEAD again to record the actual provisioned commit.
Shawn authorized the scratch thread and manual clear for L7 ([decision 3](#decisions)).

## Sequence and parallelism

| Stage | Tasks that may run in parallel | Waits for |
|---|---|---|
| 0 | HS-1, DOC-1; L7 independently | this plan's merge; L7 has owner authorization |
| 1 | CF-1, PV-1a, and sequential RC-1a → RC-1b → RC-1c | CF-1, PV-1a, RC-1a: both DOC-1 / PR3 and HS-1 / PR4 merged (use the merged plan and CD-4; RC-1a adds no dependency on the decision-18 docs follow-up). RC-1b: RC-1a. RC-1c: RC-1b |
| 2 | RT-1, JV-1; L1 to L3 as ready | Stage 2 waits for all three RC-1 Tasks (RC-1a, RC-1b, RC-1c). RT-1: CF-1, RC-1c. JV-1: CF-1, RC-1c, PV-1a. L1: CF-1. L2: JV-1, L1. L3: RT-1, L1 |
| 3 | RP-1, EV-1, PV-1b, BT-1, HC-1, GA-1 | RP-1: JV-1, RT-1. EV-1: RC-1c. PV-1b: PV-1a. BT-1, HC-1: HS-1, RC-1c. GA-1: HS-1 |
| 3a | GA-2, then GA-3; other stage-3 Tasks may continue | GA-2: GA-1. GA-3: GA-2 |
| 4 | PB-1; RP-1 may still run | RC-1c, EV-1, PV-1b, GA-3 |
| 5 | SK-1; RP-1 may still run | all helper and template Tasks (including RC-1c), and L7; RP-1 need not finish |
| 6 | PH-1 | RC-1c, SK-1, L7 |
| 7 | gated handoff, then pilot (L9) | Tasks through PH-1; L1 to L7; Shawn's go-ahead |
| later | FL-1, then FL-2 | the first pilot; FL-2 also waits for L10 |

Routing comes first, as Shawn requested on 2026-10-27. Stage 1 starts only
when both PR3 (DOC-1) and PR4 (HS-1) have merged; CD-4 record work uses the
merged plan. Under [decision 18](#decisions), the records store ships as three
sequential Tasks under one Feature lead: RC-1a, then RC-1b, then RC-1c (RC-1a's
code may merge before the decision-18 documentation follow-up merges, so no
DOC-2 follow-up merge gate is added). Dispatch CF-1, RC-1a and PV-1a first,
then RC-1b once RC-1a merges, and RC-1c once RC-1b merges. Stage 2 waits for
all three RC-1 Tasks (`RC-1a`, `RC-1b` and `RC-1c`): dispatch RT-1 as soon as
CF-1 and RC-1c finish; JV-1 also needs PV-1a. L1 follows CF-1; L2 follows JV-1
and L1; L3 follows RT-1 and L1. The router goes live once JV-1, RT-1 and L1 to
L3 pass, before publication and the skill. L7 is independent of the helper and
can run while the routing path is built.

The Stage 1 two-merge gate is mandatory, and Stage 2 waits for all three RC-1
Tasks through RC-1c. Other stages give dispatch priority, not extra
dependencies. EV-1, PV-1b, BT-1, HC-1 and GA-1 may start earlier once their
listed dependencies finish if that does not delay routing (EV-1, BT-1 and HC-1
wait for RC-1c; PV-1b waits for PV-1a; GA-1 waits for HS-1). Each parallel
group has exclusive file ownership; RC-1a, RC-1b and RC-1c share the records
paths sequentially and never run in parallel with one another. The post-routing chain is GA-1, GA-2, GA-3, PB-1, SK-1 and PH-1;
EV-1, the privacy checks, templates and lock run beside the token Tasks.
The manager chooses concurrency from capacity and Shawn's direction. DOC-1's
exact-text carve-outs and FL-2 name easy-tier work.

The handoff to the pilot:

1. Every Task through PH-1 has merged, and Shawn's checkout includes them.
2. L1 to L7 have passed. L5 has published at least one pull request through the
   helper.
3. Shawn gives the go-ahead ([decision 4](#decisions)).
4. The build manager writes the handoff and writer-transfer events with their
   prefix checkpoints and renders the kickoff into `.local/state/briefs/`.
   It sends the kickoff to the rapid-ship-bound thread, or Shawn does.
5. The rapid-ship manager follows `docs/pilot.md`. After the writer transfer, the
   build manager writes no rapid-ship record.

## Plan inputs

The build manager collected fourteen items that the design and the interim token
step's reviews leave to this plan.

| # | Item | Disposition | Tasks |
|---|---|---|---|
| 1 | A privacy match before publication | Decided. A match blocks publication. A match in a commit's message or tree returns to the Worker in a Corrections round whose brief carries the privacy-rewrite exception, which covers only unpublished commits. The publishing coordinator fixes its own text: branch name, title and body. Shawn decides when an identifier seems necessary. | PV-1b, PB-1, BT-1, SK-1; DOC-1 records it |
| 2 | The failure action for the head-commit check | Decided. It applies to every repository: report to Shawn; not PR-ready. | PV-1b; DOC-1 records it |
| 3 | The open-decisions section's lifecycle | Decided. DOC-1 records the confirmed answer to former open decision 4 and keeps an "Open decisions" heading with one line saying no decisions remain open and pointing to their recorded answers, preserving `#open-decisions`. | DOC-1 |
| 4 | Risk floors and the documentation criterion | Decided by Shawn on 2026-10-27 ([decision 1](#decisions)). Floors count changed behavior; documents are medium and technical project documentation is software engineering. CF-1 defines the fields; the confirmed values enter `.local/` at L1. | CF-1, L1, DOC-1 |
| 5 | The interim token step | Decided. The helper absorbs it. GA-1 (minting, scope, revocation), GA-2 (running a command) and GA-3 (refusals before minting) port its reviewed behavior: the key only in an anonymous pipe, the token only in the child's environment, the helper reset, SSH refusal and redirect refusal. PB-1 builds the flow. After L4 and L5, the manager stops using the interim step, and Shawn may delete it. | GA-1, GA-2, GA-3, PB-1 |
| 6 | The interim step's two questions | Decided. Keep `protocol.ssh.allow=never` and remove an inherited `GIT_ALLOW_PROTOCOL`. Revoke every token: `check` fails when revocation fails; `run` warns and keeps the child's status. | GA-1, GA-2 |
| 7 | Heavy-check coordination across manager threads | Decided. One stable `flock` inode in `.local/state/locks/`, separate status metadata, and a runner that retains the lock until the check exits, even if the wrapper dies. | HC-1 |
| 8 | The identifier list | Decided. The helper loads it for every publication check and blocks when it is missing, empty or invalid. JV-1 also screens Jev input with it. | PV-1a, PV-1b, JV-1 |
| 9 | Who installs and updates the interim step | Decided. It changes only through a Worker Assignment with owned `.local/` paths and a review by file hash, as when it was written. The manager runs it only after confirming that the file's hash equals the reviewed hash. It is frozen once GA-3 is reviewed, and retired after L5. | Conventions; GA-3 |
| 10 | The in-flight restart | Procedure decided; mechanism open until L7. L7 tests the ways to clear the conversation while keeping the thread; PH-1 writes the procedure. If no way exists, the manager asks Shawn before the handoff. | L7, PH-1 |
| 11 | A message-only rewrite after a privacy match | Decided. A rewritten commit is a new commit, so the design's rule applies ([Work cycle][d-cycle], step 7). The manager confirms that the trees are identical, the required checks rerun on the new head, and a scoped re-review covers it (independent for earlier heads or an unled final head; current lead for a led final head, under decision 13). Keeping the earlier review would relax the design; only Shawn can make that change. | BT-1, SK-1 |
| 12 | The token step's second-review findings and usage rules | Decided. GA-2 adds V1 (forward SIGTERM and SIGHUP to the child, then revoke) and V3 (build the sanitized environment, removing `GIT_CONFIG`, before both preflight and execution). GA-3 adds V2 (refuse `trace2.envVars` and a false-like `http.sslVerify`), and the usage rules become checks: `publish run` refuses `git -C`, `git -c` and a shell as the command, and PB-1 runs it from the repository directory with explicit `--head` and `--base`. SK-1 carries the Keychain rule: Allow, not Always Allow. If the interim step stays in use for long, a Worker Assignment may add V1 to V3 to it first. | GA-2, GA-3, PB-1, SK-1 |
| 13 | T3 may fast-forward the owner's checkout after merge | Decided. Read the provisioned HEAD at each dispatch; never assume the checkout lags origin. After a merge, the manager may run `git pull --ff-only` on clean `main` only when T3 has not already fast-forwarded it ([decision 2](#decisions)). | RC-1b, BT-1, SK-1; DOC-1 records it |
| 14 | GitHub-generated squash trailers | Decided by Shawn on 2026-10-28 ([decision 7](#decisions)): accept them. They may carry the owner's commit email; agents never write it. They sit on the base branch, normally outside the review ranges that privacy checks cover; a match in a squash commit that a branch update brings into a range goes to Shawn. | DOC-1 records it; PV-1b |

## Coverage

| Requirement | Design | Tasks |
|---|---|---|
| Helper: bounded Jev request, atomic records, required fields, evidence on the current commit; no agents and no polling | [Sources of truth and local records][d-records] | HS-1, RC-1a, RC-1b, RC-1c, EV-1, JV-1 |
| Validated config schema and its digest; medium Worker-only pool preferences and endpoints under decision 12 | [Routing and personal configuration][d-routing] | CF-1 |
| Routing: Jev classification, floors, Reviewer tier, agy never a Reviewer, live catalog, role limits on any selection, one fallback at most, model-aware pool walking, per-account quota and skipped-account reasons under decision 12, recorded judgment | [Routing and personal configuration][d-routing]; [Jev decision routing][d-jev] | JV-1, RT-1 |
| Medium Worker choices under decisions 9 and 12: owner-controlled placement, role limits, live catalog and GLM billing | [Routing and personal configuration][d-routing] | DOC-1, CF-1, RT-1, L11 |
| Records: intent, the three commits, results by worktree path and commit, reviews, operations, decisions; one writer; history; recovery without duplicate dispatch | [Sources of truth and local records][d-records]; [Manager threads and bases][d-bases] | RC-1a, RC-1b, RC-1c, EV-1 |
| Publication by commit; Worker App token; privacy; head and author checks; subsequent own-Task t3-compose lead squash merge under decision 19 (no helper merge command) | [Checks and publication][d-publication]; [t3-compose repository workflow][d-workflow] | PV-1a, PV-1b, GA-1, GA-2, GA-3, PB-1; SK-1, FL-1, FL-2 describe merge authority |
| Heavy checks one at a time on the machine | [Checks and publication][d-publication] | HC-1 |
| Worker and Reviewer brief templates | [Work cycle][d-cycle]; [Manager threads and bases][d-bases] | BT-1 |
| Manager skill and user-level installation | [Scope][d-scope] | SK-1 |
| Labels, and the design's offline test list | [Pilot and validation][d-pilot] | HS-1; record schema validation in RC-1a, store validation, duplicate-dispatch refusal and refusal of the three deferred CD-4 event kinds in RC-1b, and CD-4 review/finding history, current-lead provenance, uncertain-dispatch reconciliation and recovery in RC-1c; stale reviews and missing checks in EV-1; malformed Jev results in JV-1; unknown quota, failover and floors in RT-1, with simulated quota tests |
| The test command in `AGENTS.md` | [t3-compose repository workflow][d-workflow] | HS-1 |
| Lead final-head review, own-Task publication and t3-compose merge, lead reporting limits and manager delivery timing; CD-4 schema shapes in RC-1a, three-kind refusal in RC-1b, full review-sequence/finding history and current-lead provenance/evidence enforcement in RC-1c (decisions 13, 17 and 19) | [Feature lead options][d-leads]; [Accepted review policy][d-review]; [Sources of truth and local records][d-records] | DOC-1, RC-1a, RC-1b, RC-1c, EV-1, BT-1, PB-1, SK-1, FL-1, FL-2 |
| Stable uv-pinned CPython 3.15, final-release floor, one interpreter and isolated commands (decision 14, CD-3) | [Sources of truth and local records][d-records] | DOC-1, HS-1, BT-1, SK-1; manager delivery checks |
| Structured nonzero CLI data, sanitized errors, publish status file, heavy JSON summary, reserved options and injectable shared process interfaces/ownership (decision 15, CD-2) | [Sources of truth and local records][d-records]; [Checks and publication][d-publication] | DOC-1, HS-1 and every later command Task; GA-2/3, PB-1, HC-1, PV-1b |
| Temporary private prose setup, no rapid-ship work by t3-compose agents, formal L9 gates unchanged; Stage 1 after PR3 and PR4 merge; records store split into three sequential Tasks and Stage 2 gated on all three (decisions 15, 16 and 18) | [Taking over existing Claude work][d-takeover]; [Pilot and validation][d-pilot]; [Sources of truth and local records][d-records] | DOC-1, CF-1, RC-1a, RC-1b, RC-1c, PV-1a, PH-1, SK-1 |
| Feature leads after the first pilot | [Feature lead options][d-leads] | FL-1, FL-2 |
| Pilot handoff, both restarts, synthetic router pilots | [Pilot and validation][d-pilot]; [Taking over existing Claude work][d-takeover] | PH-1, RP-1, L7 |

## Decisions

Shawn answered the plan's five questions on 2026-10-27:

1. **Floors and documentation.** A floor counts what a Task changes, not what
   it mentions. The first implementation of code that handles credentials or
   enforces a guardrail, and recovery or concurrency invariants, is
   challenging. Documents are medium; technical documentation of a software
   project counts as software engineering. Exact-text mechanical edits can use
   the easy tier. Values remain configurable.
2. **Checkout updates.** After a merge, the manager may run `git pull --ff-only`
   on the owner's clean `main` only when T3 has not already fast-forwarded it.
3. **Scratch thread.** The build manager may create the scratch thread for L7.
   If no tool clears its conversation, Shawn clears it by hand.
4. **Pilot start.** The rapid-ship pilot starts only on Shawn's go-ahead.
5. **Corrections and review tiers.** The prose rules govern: Corrections with
   enumerated findings, document reviews and scoped re-reviews use the medium
   tier. First reviews of challenging code follow the Task's tier. DOC-1
   aligns the design's Reviewer-tier text and fixed model names with the
   decisions; the config supplies the models. Decision 6 replaces the
   Corrections part.

Shawn added these decisions on 2026-10-28:

6. **Corrections at the easy tier.** Corrections rounds with enumerated
   findings use the easy tier, even when a floor set the first round's tier.
   Their scoped re-reviews stay medium, and agy is never a Reviewer.
7. **Squash trailers.** Shawn accepts the `Co-authored-by` trailers that
   GitHub generates on a squash commit when he merges. They may carry his
   commit email, which is already public ([finding F7][d-findings]). Agents
   never write it (plan input 14).

   Application note, 2026-10-28: decision 19 applies this same exception to
   GitHub-generated trailers when a lead merges its own t3-compose pull request.
   Agents still never write Shawn's email or supply the squash title or message.
8. **Routing stays untuned.** Unoptimized routing is acceptable for now.
   Evaluating and tuning the thresholds that route work to each model and
   reasoning level is backlog work outside this plan.
9. **Medium Worker selection, 2026-10-28.** Shawn first said: "we must also support openrouter GLM5.3 as a medium worker choice (fallback behind sonnet and sol). the BYO harness choice with it is Pi."
   He clarified: "Never fallback twice is a sound rule. The current routing rule remains flexible. Some time in the future I will consider GLM -> Sonnet fallback." and "GLM and jev are both added, as 2 new Pi providers in T3 code, using separate API keys."
   GLM 5.3 through OpenRouter, run in the Pi Harness with Shawn's own key, is a
   supported medium Worker selection. It serves Workers only, never a Reviewer,
   Feature lead, text helper or the master. Shawn chooses where it sits in the
   config and may later configure GLM -> Sonnet. Until Shawn places it, the
   config does not use it. The earlier ordered-target rule is withdrawn: each
   chain has exactly one target; a selection falls back at most once; never
   fall back twice. Role limits apply to preferences and chain targets alike.
   This is a second exception to subscription-only execution, limited to GLM
   Workers; the Jev exception still covers routing only. Each GLM use is billed
   per token and recorded and reported, whether a preference or a fallback.
   Pi Workers carry their instructions inline, as for agy, until Pi's
   instruction-file handling is verified. Pi's key stays in Pi. The helper
   never reads, passes or logs it; briefs, source, logs and argv never hold it.
   The selection is available only when T3's live catalog lists the Pi instance
   with GLM 5.3 and reports `canRunChildTask: true`. The config uses only the
   catalog's exact instance and model IDs; never guess an ID. L11
   verifies it once Shawn places GLM in config.
10. **Command and test-harness specs, 2026-10-28.** Shawn said: "I also don't see the spec supporting the impl choices in pr4. The impl design of all the CLI cmds implemented need to be surfaced."
    Every implemented command and test-harness contract has a written spec in
    the repository. HS-1 adds `docs/cli.md` and `docs/testing.md`; each later
    Task that adds commands adds its module's `docs/cli/<module>.md`, and the
    Reviewer checks the specs against the code.
11. **Jev model and call path, 2026-10-28.** Shawn said: "calling jev directly is correct." After the manager reported that the Decisions API rejects `typesafe/jev-router`, Shawn said: "~typesafe/jev-latest is the correct choice."
    The helper calls Jev directly through OpenRouter's Decisions API (JV-1); no
    Harness or T3 provider runs Jev, and Shawn removes the Pi provider added
    for Jev. The config names `~typesafe/jev-latest`, an alias for the current
    Jev snapshot. Each classification records the configured model and the
    response's snapshot `model` and `id`. A new snapshot behind the alias does
    not by itself make a classification stale.
12. **Medium Worker pool selections and Argon, 2026-10-28.** Shawn said: "There's another model to support: gemini-4-argon-high as medium worker model (in agy subscription, only 2/3 accounts have it, slowly rolling out), same tier as GLM5.3 and Sonnet/Sol."
    Argon through the agy subscription is a supported medium Worker choice,
    alongside decision 9's choice. It serves Workers only, never a Reviewer,
    Feature lead, text helper or the master. Shawn then chose: "Use Argon as the medium Worker preference, with Sol as its one fallback".
    This is the medium software-engineering preference, with exactly
    `"roles": ["worker"]`; medium Reviewers and Feature leads reach Sol through
    its one fallback. The Engineering manager applies the choice in private
    config. GLM remains unplaced. The subscription covers Argon; no new API
    exception is added. agy Workers
    carry instructions inline. The catalog observation on 2026-10-28 found
    it on two of three pool accounts with `canRunChildTask: true`; account
    identities are recorded in `.local/`. This is rollout evidence, not quota
    evidence, and does not promise later availability.
    The Engineering manager's shared contract CD-1 permits `PoolSelection`
    in medium preferences and either medium chain endpoint only with exactly
    `"roles": ["worker"]`. Missing or other roles and the challenging tier
    fail validation. Direct selections never name pool members. `easyWorker`
    and `textHelper` keep their schema and semantics; no other slot broadens.
    Resolve a pool from the active account in pool order, wrapping around.
    Skip non-offering accounts with "model not offered"; this neither proves
    exhaustion nor changes the active account. Skip confirmed exhausted
    accounts under the existing reset/availability rules. Choose the first
    eligible offering account and record it and every skipped account with
    its reason. An active account absent from the catalog, unable to run
    children or failing its status check still stops with exit 4, without
    rotation. Never select an unavailable account. `easyWorker` uses the
    same walk. Walking is not a fallback: none left uses the selection's one
    fallback or exits 5; never follow a fallback target's own chain. Quota
    observations stay per account for all its pool models, without model keys.
    Role exclusion applies to preferences and endpoints: Reviewers and
    Feature leads reach one eligible fallback or exit 5. CF-1 and RT-1 implement
    this contract; private config records Shawn's chosen placement. L11 verifies both
    supported choices after Shawn places each, independently of the handoff gate.

13. **Lead final-head review, publication and manager delivery, 2026-10-28.**
    Shawn said: "feature lead should have enough context to review the final head, when there is a feature lead. Manager should be in charge of ensuring delivery success (operational readiness, integ test in place, etc)"
    He confirmed: "Yes, the lead reviews the final head"
    Chosen option: "When a Task has a lead, the lead's own review is the final-head review. No separate Reviewer reviews that head."
    The assigned lead holds requirements, design, interfaces, final diff,
    callers, tests and evidence, personally reviews the exact final head and
    owns required checks including heavy checks. Earlier-head reviews and
    Corrections re-reviews remain independent; without a lead an independent
    Reviewer clears the final head. Preserve all prior reviews and dispositions.
    Leads implement nothing, so reviewing a Worker's final head does not approve
    their own implementation. The lead publishes its own Task's PR; the manager
    publishes Tasks without a lead. Workers/Reviewers still never push, post,
    open/edit PRs or merge; Shawn alone merges t3-compose, squash only.
    Heavy checks serialize machine-wide with a stable lock, in a clean linked
    worktree at the exact head after writer completion. Owner-checkout and
    parent lifecycle rules remain. The manager owns cross-feature integration,
    operational readiness, integration tests and final delivery readiness to
    Shawn, without repeating lead final review. Under decision 17's 2026-10-28
    refinement, a lead puts the machine prerequisites and integration evidence
    in the PR body, tells Shawn in its own thread when the PR is ready, never
    reports PR readiness to the manager, and sends no progress reports; it sends
    the manager one line when Shawn merges, plus blockers, cross-Task or
    shared-interface changes, new machine prerequisites, owner rule changes and
    agy exhaustion. Manager delivery checks move to two points: at Task start
    the manager sets expected integration tests and machine prerequisites, and
    after a merge it checks `main` and the machine, without an extra pre-merge
    readiness approval. Verified/PR-ready and overall
    delivery readiness remain distinct; delivery readiness is not an EV-1 input.
    CD-4 uses the review event sequence as every review reference and keys
    findings by `(review reference, finding ID)`. Every later same ID is distinct.
    `reviewer` is `{kind:task,taskId:...}` with native task provenance, or
    `{kind:lead,thread:...}` without `taskId`. Both record base/head/verdict/
    findings; evidence path plus digest is required for lead, optional for task.
    Manager-only `lead-assignment` records thread, assigned-by, date and reason;
    latest names the current lead. Only that lead's exact-head review counts
    for a led Task; otherwise an independent task review is required. Feature
    lead joins check sources, with its current exact-head pass accepted and
    Worker claims insufficient. `withdrawn` is by the review author. Record
    single-writer, transfer and history invariants remain.

    Superseding note, 2026-10-28: decision 19 replaces the Shawn-only merge
    rule for t3-compose. Every t3-compose Task has an assigned lead, who alone
    squash-merges its own Task's PR as the Worker App after its exact-final-head
    review and passed required checks, subject to decision 19's merge conditions.
    The manager does not merge; the general optional-lead/no-lead design remains
    for managed repositories. The one-line notification is sent when the PR
    merges. All review, evidence, identity and privacy duties remain.
14. **Stable Python through uv, 2026-10-28.** Shawn said: "use latest stable python instead of python 3.9"
    He corrected: "correction: uv python pin 3.15 --global"
    CD-3 selects stable CPython 3.15 through uv's global pin, floor 3.15,
    standard library only. Reject older versions and any pre-release
    (`sys.version_info.releaselevel != "final"`) with the existing exit-2 form.
    Setup and invocation are:

        uv python pin 3.15 --global
        PY="$(uv python find 3.15)"
        "$PY" -I -S <root>/bin/t3c version
        "$PY" -I -S -B -m unittest discover -s tests -t . -v

    One interpreter replaces the system/Homebrew matrix. Python child tests
    retain `sys.executable`. Official sources verified on 2026-10-28:
    [Python downloads](https://www.python.org/downloads/) and
    [Python 3.15.2](https://www.python.org/downloads/release/python-3152/) list
    3.15.2 final, released 2026-10-25; 3.15.0 has been stable since 2026-10-09.
    Latest patch 3.15.2 does not raise the minimum above 3.15. Shawn authorized
    "Upgrade uv, install 3.15.2, remove alpha"; the manager installed final
    3.15.2 and removed the alpha. The manager confirms runtime and integration
    tests for delivery. This docs-only head has no helper runtime suite and
    claims no such pass. A newer stable minor needs Shawn's pin and a Task
    raising the floor and commands; no runtime release lookup. The pinned
    Worker App token tool stays unchanged on its reviewed interpreter outside
    the helper until retired after L5.
15. **Approved CLI/process contract and HS-1 size, 2026-10-28.** Shawn said
    "Approve" to the HS-1 design-review package and "Overflowing 1200 line is fine for this pr"
    about PR4 (HS-1), with no second HS Task and no general size waiver.
    CD-2 fixes the closed exits 0/1/2/3/4/5/75, data on nonzero outcomes,
    message-only `CommandError`, sanitized unexpected failures on 2, the sole
    `publish run` pass-through and its mandatory atomic mode-0600 status file,
    and heavy-run JSON summary exits. It reserves global option names, gives
    PV-1b `--text-file`, documents contextual `--repo`, and requires all later
    program launches through injectable `process` without shells or argument/
    environment echo. HS-1 adds only `process.py`/`test_process.py` to its
    existing files; no separate commit-reader module. Shared interfaces and
    explicit ownership of later shared-file/`MODULES` changes are fixed above;
    RC-1 retains its assigned `jsonio` work. RC-1/PB-1 use `cli.helper_info()`
    for helper commit metadata. Imports/register stay side-effect-free and
    handlers use `args.local_dir`. The manager withdrew the separate follow-up
    docs Task: DOC-1 includes CD-2 section 6 now. Stage 1 (CF-1/RC-1/PV-1a)
    starts only after both PR3 and PR4 merge, using the merged plan and CD-4
    (decision 18 splits aggregate RC-1 into sequential RC-1a, RC-1b and RC-1c,
    with `jsonio` and `cli.helper_info()` in RC-1b and all RC-1 dependents and
    Stage 2 waiting for RC-1c).
16. **Temporary prose setup, 2026-10-28.** Shawn said: "we have the design down. While waiting for t3c impl, create a prose prompt for this manager/feature lead/worker setup with multi-model in t3 code, so we can temporarily unblock rapid-ship dev"
    This dated exception supersedes the hold in re-home decision 2 and Taking
    over existing Claude work only for a separate rapid-ship-bound manager's
    temporary private prose phase. It reconciles old Claude work first and
    dispatches nothing without Shawn's approval. t3-compose agents do no
    rapid-ship work. Formal L9 still waits for `t3c` and the existing handoff
    gates. Private prompt paths/content, thread/account identifiers and claims
    of formal pilot completion do not belong in tracked documents.
17. **Lead reporting and manager delivery timing, 2026-10-28.** Shawn said: "btw, notice how each feature lead notifies you when a task is ready for me to merge? I can check that they have open PRs for me myself. In the future, don't let them notify you at this point, it's a waste of your context and tokens"
    A Feature lead never reports to its manager that a pull request is ready,
    and sends no progress reports. Shawn reads open pull requests himself; a
    lead still tells Shawn in its own thread that the pull request is ready. A
    lead still sends one line when Shawn merges, and sends blockers, cross-Task
    or shared-interface changes, new machine prerequisites, owner rule changes
    and agy exhaustion, without turning that into a status-report channel.
    Manager delivery duty moves to two points: at Task start the manager sets
    expected integration tests and machine prerequisites; after a merge it
    checks `main` and the machine. The lead's pull request body carries the
    prerequisites and integration evidence. There is no extra manager pre-merge
    readiness approval or repeated final-head review; the manager still owns
    overall delivery, and delivery readiness remains separate from EV-1.

    Superseding note, 2026-10-28: under decision 19, the one-line notification
    is sent when the pull request merges, including a lead's authorized own-Task
    t3-compose merge. The lead proceeds without waiting for Shawn; no ready or
    progress report to the manager is added.
18. **Three sequential records-store Tasks, 2026-10-28.** Shawn was asked:
    "RC-1 (the records store) will be about 2,200–2,800 lines, more than twice the plan's 1,200-line limit per PR. How should it ship?"
    Shawn chose the option: "Three PRs in sequence (Recommended)", with the
    supplied option text:
    "Each stays under the limit: (1) the record schema, about 800–1,100 lines; (2) the durable store, history and the work and event commands, about 900–1,200; (3) checkpoint, reconcile and repair, about 650–850. … Stage 2 waits for all three"
    (rejecting "One PR, size exception"; no size waiver is granted).
    RC-1 splits into three sequential pull-request Tasks under one Feature lead,
    each at most 1,200 changed lines: RC-1a (record schema contract foundation
    in `docs/records.md`, `t3compose/schema.py` and `tests/test_records.py`,
    with no usable store or commands; medium tier, about 800–1,100 lines;
    depends on merged HS-1 / PR4 and DOC-1 / PR3, runs in parallel with CF-1
    and PV-1a, and adds no dependency on this documentation follow-up's merge),
    RC-1b (durable store core and history, `jsonio.py` and `tests/test_jsonio.py`
    additions, `t3compose/records.py`, `docs/cli/records.md`,
    `tests/test_records.py`, `docs/records.md` updates, the common routing-input
    recorder, dispatch provenance and duplicate-writer refusal, the
    exclusive-create writer race, and `work new`, `show`, `list`
    and `event add`; challenging tier, about 900–1,200 lines; depends on RC-1a
    and cannot run beside it), and RC-1c (`work snapshot`, `reconcile` and
    `repair`, prefix checkpoint verification, `restart`, `handoff` and
    `writer-transfer` enforcement, orphan-temporary crash recovery and the full
    CD-4 review/finding history and current-lead provenance unit in
    `t3compose/records.py`, `docs/records.md`, `docs/cli/records.md`,
    `tests/test_records.py` and `tests/test_reconcile.py`; challenging tier,
    about 830–1,080 lines; depends on RC-1b and cannot run beside it). The full
    original RC-1 record contract and all ten acceptance items hold across the
    three Tasks and on the combined result after RC-1c. Every Task that
    depended on RC-1 (RT-1, JV-1, EV-1, BT-1, HC-1, PB-1, SK-1 and PH-1) waits
    for RC-1c, and Stage 2 waits for all three.

    Decomposition note, 2026-10-28: the Engineering manager moved the CD-4
    review/finding history unit and its tests and documentation from RC-1b to
    RC-1c after the Worker estimated RC-1b at 1,350 lines. This keeps RC-1b
    within 1,200 lines. The moved unit covers review references by event
    sequence, cross-round finding identity, disposition-to-finding joins and
    current-lead provenance/lead-assignment currency, including EV-1-relevant
    tests. RC-1b refuses `review`, `finding-disposition` and `lead-assignment`
    events until RC-1c; RC-1a continues to validate schema shapes only. Current
    estimates are RC-1a 800–1,100, RC-1b 900–1,200 and RC-1c 830–1,080 lines,
    all at most 1,200 (about 2,530–3,380 combined). The option's original
    650–850 estimate and the question's aggregate estimate above are historical. RC-1a created `tests/test_records.py` in PR8; RC-1b and RC-1c
    extend that file. All three pull requests keep the same lead, and the full
    combined RC-1 acceptance and every RC-1c dependency gate remain unchanged.

19. **t3-compose Feature lead merge authority, 2026-10-28 (~18:00 CST).**
    Shawn said: "permission upgrade: for this project t3c ONLY, feature leads have permission to auto-merge PRs after they have reviewed. I.e., continue dev of this project fully unsupervised."
    On t3-compose only, every Task has an assigned Feature lead, and that lead
    squash-merges its own Task's pull request as the Worker App after its own
    exact-final-head review and passed required checks. The Engineering manager
    does not merge, a Feature lead never merges another Task's pull request,
    and Workers and Reviewers still never merge. rapid-ship and all other
    repositories keep their own merge rules; the general optional-lead/no-lead
    package design remains for managed repositories.
    Merge depends on the pull request being open and non-draft, clean and
    mergeable, at the exact reviewed head, with passed hosted checks if present,
    no `.github/workflows/` or out-of-owned-path change, and no open owner
    question. Final-head review, privacy, Worker App identity, exact head/author
    verification and review evidence remain required. GitHub generates the
    squash commit title and message; agents supply neither. Decision 7 covers
    GitHub-generated co-author trailers on merges under this grant; agents
    still never write Shawn's email. PB-1 implements push/pr commands only;
    no new helper merge command is authorized.
    Tasks and stages continue without waiting for Shawn. The lead verifies the
    merge and result and sends its one-line manager handoff when the pull
    request merges, with no ready or progress report. The manager sets expected
    integration tests and machine prerequisites at Task start, checks `main`
    and the machine after each merge, and starts dependent Tasks and leads once
    dependencies merge. `.github/workflows/` changes, routing values and model
    placement, the rapid-ship pilot (L9), product decisions and anything unclear
    remain Shawn's. This grants no rapid-ship work and adds no implementation
    Task, runtime, API, schema or publication-tool behavior.

[glossary]: ../../../GLOSSARY.md
[design]: ../specs/2026-10-27-t3-compose-design.md
[d-scope]: ../specs/2026-10-27-t3-compose-design.md#scope
[d-roles]: ../specs/2026-10-27-t3-compose-design.md#roles
[d-leads]: ../specs/2026-10-27-t3-compose-design.md#feature-lead-options
[d-routing]: ../specs/2026-10-27-t3-compose-design.md#routing-and-personal-configuration
[d-jev]: ../specs/2026-10-27-t3-compose-design.md#jev-decision-routing
[d-records]: ../specs/2026-10-27-t3-compose-design.md#sources-of-truth-and-local-records
[d-bases]: ../specs/2026-10-27-t3-compose-design.md#manager-threads-and-bases
[d-cycle]: ../specs/2026-10-27-t3-compose-design.md#work-cycle
[d-publication]: ../specs/2026-10-27-t3-compose-design.md#checks-and-publication
[d-workflow]: ../specs/2026-10-27-t3-compose-design.md#t3-compose-repository-workflow
[d-refine]: ../specs/2026-10-27-t3-compose-design.md#design-refinement
[d-takeover]: ../specs/2026-10-27-t3-compose-design.md#taking-over-existing-claude-work
[d-pilot]: ../specs/2026-10-27-t3-compose-design.md#pilot-and-validation
[d-review]: ../specs/2026-10-27-t3-compose-design.md#accepted-review-policy
[d-grill]: ../specs/2026-10-27-t3-compose-design.md#decisions-accepted-after-grill-with-docs
[d-rehome]: ../specs/2026-10-27-t3-compose-design.md#decisions-accepted-at-the-re-home
[d-pubdec]: ../specs/2026-10-27-t3-compose-design.md#decisions-on-t3-compose-publication
[d-findings]: ../specs/2026-10-27-t3-compose-design.md#findings
[d-open]: ../specs/2026-10-27-t3-compose-design.md#open-decisions
