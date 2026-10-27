# t3-compose implementation plan

Status: proposed on 2026-10-27. It follows the design revision in pull request #1
at `906fdb0`. If Shawn changes that revision, a Corrections round updates this
plan. The plan takes effect when Shawn approves and merges its pull request.

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

- Python 3.9 or later, standard library only. The helper runs other programs
  only through `subprocess`: `git`, `gh`, `grep`, `openssl`,
  `/usr/bin/security`, and the command given to `t3c heavy run` or
  `t3c publish run`.
- The helper runs as `python3 -I -S <t3-compose root>/bin/t3c` and refuses to
  run without both flags.
- Records, personal config and runtime state stay in Git-ignored `.local/`.
  Nothing is added to a managed repository.
- Tracked files, commit messages and pull request text hold no private
  identifier. Test fixtures use synthetic values that do not match the private
  identifier list; T3 task IDs, worktree paths and T3 branch names need a
  synthetic form.
- Workers commit on their branch only. Workers and Reviewers do not push, open,
  edit or merge pull requests, or post to GitHub. The manager publishes. Shawn
  merges t3-compose pull requests.
- The helper's checks are instructional. They create no security boundary
  ([Checks and publication][d-publication]).
- rapid-ship stays on hold until the pilot starts
  ([re-home decision 2][d-rehome]). No Task reads or runs anything in rapid-ship.
- The manager never moves or writes the owner's checkout beyond the design's
  list ([Manager threads and bases][d-bases]). After a merge, the manager reads
  the actual checkout HEAD. It may run `git pull --ff-only` on the owner's
  clean `main` only when T3 has not already fast-forwarded it
  ([decision 2](#decisions)).

## Conventions for every Task

- **Base.** A Task starts from `origin/main` after its dependencies have merged.
  When the manager uses another start, such as a stacked parent's head, it
  records the start and the reason.
- **Size.** Aim for 300 to 1,200 changed lines, tests included. One Reviewer
  Session must read the whole diff with its callers and tests and run the suite.
  The interim token step's first review covered about 2,100 lines, tests
  included, in one Session; 1,200 leaves a margin. A Task that would exceed
  about 1,200 lines splits where its section says. A port of already-reviewed code (GA-1 to
  GA-3) may reach about 1,400 lines, because its Reviewer compares it with the
  reviewed source.
- **Files.** A Task changes only the files it lists. Tasks that may run in
  parallel share no file. Only HS-1 edits `t3compose/cli.py`; later Tasks add
  modules that its registry loads.
- **Tests.** `SUITE` means
  `python3 -I -S -B -m unittest discover -s tests -t . -v`, run from the
  repository root; `SUITE -p 'test_x.py'` runs one file. The suite passes under
  the macOS system interpreter and a current Homebrew interpreter. Every test
  class derives from the offline guard in `tests/support.py` and is named
  `Offline…`, or `Simulated…` when it injects quota responses. Tests cover
  failure and refusal paths as well as success.
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
  with enumerated findings and scoped re-reviews follow the prose rules at the
  medium tier. Document reviews are medium. First reviews of challenging code
  follow the Task's tier; other first reviews are at least medium. agy is never
  a Reviewer. DOC-1 aligns the design with these owner decisions
  ([decisions 1 and 5](#decisions)).
- **Records.** Until RC-1 is installed, the manager keeps its manual dispatch
  log in `.local/`. From then on it records through the helper. The manual log
  stays as history and is not migrated.
- **Publication.** Until PB-1 is installed, the manager publishes with the
  interim token step, after it confirms that the file's hash equals its
  reviewed hash. The first publication after that goes through the helper and
  is L5.
- **Copied code.** Compose and Firstmate are references. A Worker who copies
  their code keeps the upstream MIT notice in the copied file's header and
  reports it ([publication decision 3][d-pubdec]).

## Contracts

This plan fixes the helper's layout, commands, record format and config schema,
which the design leaves to the builder. A Worker who needs to change a contract
reports it; the manager decides.

### Layout

| Path | Contents | Tasks |
|---|---|---|
| `bin/t3c` | entry point | HS-1 |
| `t3compose/` | one module per concern | each Task |
| `tests/` | offline suite | each Task |
| `live/` | live checks | JV-1, RT-1, RP-1 |
| `skill/SKILL.md`, `skill/templates/` | manager skill and brief templates | SK-1, BT-1, PH-1, FL-1 |
| `docs/` | configuration, record, restart and pilot contracts | CF-1, RC-1, PH-1, RP-1, FL-1 |
| `pilot/` | synthetic router examples | RP-1 |

### Commands

| Command | Purpose | Task |
|---|---|---|
| `t3c version` | root, commit, dirty flag, Python version | HS-1 |
| `t3c config check` | validate the routing config and quota state; print the digest | CF-1 |
| `t3c work new`, `show`, `list`, `snapshot`, `reconcile`, `repair` | work records | RC-1 |
| `t3c event add` | append one validated event | RC-1 |
| `t3c evidence check` | `verified`, or the gaps, for one head | EV-1 |
| `t3c route classify` | one bounded Jev request | JV-1 |
| `t3c route input`, `judge`, `select` | routing input; manager judgment; final tier and selection | RT-1 |
| `t3c quota show`, `record`, `activate` | agy quota state | RT-1 |
| `t3c privacy check` | the identifier list against messages, trees, branch name and pull request text | PV-1b |
| `t3c pr verify` | head, base, state and author of a pull request | PV-1b |
| `t3c publish check` | mint a token, verify its scope, revoke it | GA-1 |
| `t3c publish run -- CMD` | run one command with the App token | GA-2, GA-3 |
| `t3c publish push`, `pr` | the t3-compose publication flow | PB-1 |
| `t3c heavy run`, `status` | the machine-wide heavy-check lock | HC-1 |
| `t3c brief render`, `check` | fill and check brief templates | BT-1 |
| `t3c skill install`, `status` | the user-level symlink | SK-1 |

Output is JSON on stdout unless `--text` is given. Diagnostics go to stderr.
Nothing secret goes to either. Exit codes: 0 success; 1 check failed (blocked
or not verified); 2 invalid input or records; 3 invalid config, which blocks
routing; 4 manager judgment needed; 5 report to Shawn; 75 busy.

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
| `routing-classification` | routing-input reference and digest, routing-policy digest, rule version, criteria text and digest, declared risk facts, pinned model, the response's snapshot `model` and `id`, both answers with probabilities and confidence, outcome, reason |
| `routing-judgment` | routing-input reference and digest, routing-policy digest, domain, difficulty, reason, basis (Jev failure, low confidence, mixed or unclear, manual) |
| `routing-selection` | routing-input reference and digest, routing-policy digest, classification or judgment reference, role, round and review scope, tier, floors applied, escalation, selection, availability and fallback reason, config digest, catalog read time and digest, quota-state digest |
| `dispatch-intent` | role, round, `clientRequestId`, brief path and digest, selection, provisioned, start and review-base commits, dispatching checkout, isolation |
| `dispatch-confirmed` | `clientRequestId`, `taskId`, child thread reference, worktree path or unknown |
| `result` | `taskId`, completed T3 status evidence, worktree path, result commit, parent commit, branch observed, Worker claims, facts the manager verified, questions |
| `check` | check ID, command, commit, outcome, source (manager, Reviewer, assigned Session or Worker), evidence path |
| `criterion-evidence` | criterion ID, commit, source, evidence |
| `review` | review `taskId`, base, head, verdict, findings (ID, severity, summary) |
| `finding-disposition` | review task reference, finding ID, the Worker's response, disposition, by, evidence |
| `op-intent`, `op-result` | operation (push, pull request or other), target, commit, expected prior head or absence for a push; then the result and how it was confirmed |
| `decision` | question, answer or unknown, decided by, date |
| `scope` | the replaced fields and the decision that changed them |
| `next-action` | action or unknown |
| `reconciliation` | the uncertain item, live T3 and Git facts, outcome |
| `restart` | phase (before or after), checkpoint (last sequence and prefix digest), before-event reference for the after phase |
| `handoff` | pointers, hold end condition, pilot criteria, next action, checkpoint |
| `writer-transfer` | from, to, reconciliation reference, checkpoint |

A finding's key is `(review task reference, finding ID)`. A disposition must
name an existing finding in that review; the same ID in a later round is a
different finding. A checkpoint holds the last sequence and the digest of
`work.json` plus the ordered event prefix through that sequence, before the
checkpoint-bearing event. A verification recomputes that prefix, not the current
snapshot. Handoff, restart and writer-transfer events never hash themselves.

The common routing input is `{task, context}` and is capped at 16 KiB. `task`
has only the six fields above; `context` is null or the supplied retry context.
Its digest covers that whole canonical JSON object.
The routing-policy digest covers classification instructions, criteria and rule
version, risk facts and floors, the pinned Jev model, confidence threshold,
retry limit and uncertainty rule. CF-1 computes it from validated config. JV-1
and RT-1 use RC-1's common input recorder; judgments have the same freshness
contract as classifications. Selection names the current input and policy
explicitly and also records the whole config digest for model preferences.

Finding dispositions are `fixed` (with its commit), `resolved-outside-tree`
(with evidence), `declined` (minor findings only, with a reason), `withdrawn`
(by the Reviewer), `disputed`, `rejected-by-second-review` and `owner-decided`.

### Config schema

`.local/agent-routing.json`, version 2. Angle brackets are placeholders.

    {
      "version": 2,
      "master": Selection,
      "modelPreferences": {
        "software-engineering": {"medium": Selection, "challenging": Selection},
        "non-engineering":      {"medium": Selection, "challenging": Selection}
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
      "jev": {"provider": "openrouter", "model": "<pinned model>",
              "minConfidence": <number>, "maxContextRetries": 0 | 1,
              "uncertainResult": "manager-judgment", "timeoutSeconds": <1 to 120>,
              "keyFile": {"path": "<absolute path>", "field": "<name>"}},
      "availabilityFallback": {
        "roles": ["worker", "reviewer", "feature-lead"],
        "manager": "owner-choice",
        "chains": [{"tier": "medium" | "challenging",
                    "from": Selection, "to": [Selection]}],
        "recordReason": true, "reportReason": true}
    }
    Selection     = {"providerInstanceId": "<instance>", "model": "<model>",
                     "options": {"<id>": "<value>"}}
    PoolSelection = {"providerPool": "<pool>", "model": "<model>",
                     "options": {"<id>": "<value>"}}

Validation rules:

- Unknown keys and wrong types fail at every level. Option values are strings
  or booleans.
- Version 1 fails with the list of missing sections, so routing stays blocked
  until the new sections hold Shawn's values.
- Floors are medium or challenging only. Fact IDs are lowercase words joined by
  hyphens.
- `minConfidence` is above 0 and at most 1. `maxContextRetries` is 0 or 1.
  `uncertainResult` is `manager-judgment`.
- No pool member appears in `modelPreferences` or in a fallback chain, so agy is
  never a Reviewer and never a fallback.
- Each chain has an explicit tier and exactly one target. Each `(tier, from)`
  appears once, and its target differs from its source. Both endpoints are
  validated selections associated with that tier; neither must be a primary
  preference. This allows a configured primary to fall back to a non-primary
  selection and permits its reverse chain. A chain without its reverse is a
  notice, not an error, because directions are owner values. RT-1 enforces role
  eligibility, tier association and live availability at selection and uses no
  second chain after a fallback.
- `easyWorker` and `textHelper` name a configured pool.
- The digest is `sha256:` and the hex SHA-256 of the file's bytes. Every routing
  record and dispatch intent stores it.

The quota state file beside the config keeps its current shape:
`activeProviderInstanceId`, `quotaObservations` (per instance, a list of
`{observedAt, status, resetAt, evidence}`, where status is `exhausted` or
`available`) and `note`.

## Tasks

Each Task is one pull request. Live checks (L1 to L10) and owner steps are
listed under [Live checks and owner steps](#live-checks-and-owner-steps).

### HS-1 Helper skeleton and offline harness

- **Objective.** Create the package, the entry point, the shared JSON
  read utilities, the offline guard and the test command, so that later
  Tasks only add modules.
- **Design.** [Sources of truth and local records][d-records] (the helper);
  [t3-compose repository workflow][d-workflow] (`AGENTS.md` and its test
  command); [Pilot and validation][d-pilot] (test labels).
- **Files.** Create `bin/t3c`, `t3compose/__init__.py`, `t3compose/cli.py`,
  `t3compose/paths.py`, `t3compose/jsonio.py`, `tests/__init__.py`,
  `tests/support.py`, `tests/test_cli.py`, `tests/test_jsonio.py` and
  `tests/test_labels.py`. Modify `AGENTS.md`: the sentence that says no runtime
  or test commands exist gives way to `SUITE` and the label rule.
- **Interfaces.** `bin/t3c` checks for Python 3.9 or later and for
  `sys.flags.isolated` and `sys.flags.no_site`, puts its own root on `sys.path`
  and calls `t3compose.cli.main`. `cli.py` holds the fixed module list (`config`,
  `records`, `evidence`, `jev`, `routing`, `quota`, `privacy`, `pullrequest`,
  `github_app`, `token_run`, `publish`, `heavy`, `briefs`, `skill_install`).
  It loads each module that exists and calls its `register`, which may add
  commands to a group that other modules share. A module that exists but fails
  to import is an error, not a skip. `t3c version` prints the root, commit,
  dirty flag and Python version. `jsonio` provides canonical JSON, file digests
  and size-capped reads. Atomic persistence and crash recovery belong to RC-1.
- **Acceptance.**
  1. `python3 -I -S bin/t3c version` works under both interpreters. Without
     `-I` or `-S` it exits 2 and prints the correct command.
  2. A missing module is skipped; a broken module fails loudly.
  3. Canonical JSON and file digests are stable, and oversized reads fail.
  4. The offline guard blocks network sockets, `urllib`, and `security`, `gh`,
     `ssh` and `curl` subprocesses. A test proves each block.
  5. `tests/test_labels.py` fails when a test class lacks the guard or the
     `Offline` or `Simulated` prefix.
  6. `AGENTS.md` names `SUITE` and says that only the manager runs `live/`.
- **Offline tests.** `SUITE`.
- **Depends on.** This plan, merged.
- **Parallel with.** DOC-1; later code Tasks depend on HS-1.
- **Risk facts and tier.** Shared interface change (layout, registry, exit
  codes) → the medium tier. Reviewer: medium.
- **Review focus.** The registry's skip rule; the guard's completeness; JSON
  read limits; flag handling under Python 3.9.
- **Size.** About 350 lines.

### DOC-1 Design alignment with owner decisions

- **Objective.** Reconcile the design with Shawn's decisions of 2026-10-27,
  replace stale routing text, and record publication failure actions.
- **Design.** [Open decisions][d-open]; [Routing and personal configuration][d-routing];
  [Manager threads and bases][d-bases]; [Accepted review policy][d-review];
  [Feature lead options][d-leads]; [Design refinement][d-refine];
  [Checks and publication][d-publication]; [t3-compose repository workflow][d-workflow].
- **Files.** Modify `docs/superpowers/specs/2026-10-27-t3-compose-design.md`
  only.
- **Interfaces.** None; design text only.
- **Changes.**
  - Record [decisions 1, 2 and 5](#decisions), dated 2026-10-27, in the design.
    Decision 1 replaces open decision 4. Keep a heading named "Open decisions"
    with one line saying no decisions remain open and linking to the sections
    recording them, so links to `#open-decisions` keep working. Update the
    status line and superseded references to open items (plan input 3).
  - Replace the design's Reviewer-tier rule with decision 5: document reviews,
    enumerated Corrections and scoped re-reviews use the medium tier; first
    reviews of challenging code follow the Task's tier; agy is never a Reviewer.
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
  - Record Shawn's choice on GitHub-generated squash trailers (item 14) when
    he supplies it. Until then, retain that one explicit owner item separately
    from the "Open decisions" heading; do not infer an exception to
    commit-message privacy from public commit metadata.
- **Easy carve-outs.** Reserve the following exact text for a sequential
  Assignment at the easy tier after the reconciling edits finish, with the
  decision date and exclusive ownership of the design for that Assignment:
  "Documents are medium. Technical documentation of a software project counts
  as software engineering. A floor applies to what a Task changes, not what it
  mentions." For item 14, copying Shawn's eventual wording is also an easy-tier
  follow-up when the brief supplies that text. Any follow-up edits get current
  checks and a medium-tier re-review; they do not run beside DOC-1's writer.
- **Acceptance.** Decisions 1, 2 and 5 replace the superseded text without
  contradictions. Tier names replace fixed Worker, Reviewer and Feature lead
  model names. Items 1 to 4, 13 and 14 have the dispositions above. No other
  design decision changes. Tree and message privacy checks are clean; all
  links and anchors resolve, including `#open-decisions` with its heading and
  one-line pointer to the recorded decisions.
- **Offline tests.** Link and privacy checks; after HS-1 merges, `SUITE`,
  unchanged. DOC-1 has no runtime prerequisite.
- **Depends on.** This plan, merged. Item 14's final wording waits for Shawn;
  the other changes can proceed now.
- **Parallel with.** HS-1 and later code Tasks; it owns only the design.
- **Risk facts and tier.** Reconciliation across one document → the medium
  tier. The exact-text carve-outs above use the easy tier. Reviewer: medium,
  never agy.
- **Review focus.** Faithful decisions; no contradictory tier or checkout rule;
  no inferred squash-trailer exception; resolved links.
- **Size.** About 150 lines, including replaced text.

### CF-1 Config schema and validation

- **Objective.** Validate `.local/agent-routing.json` and the quota state file
  as [Config schema](#config-schema) defines, and document the contract, so
  that invalid config blocks routing.
- **Design.** [Routing and personal configuration][d-routing];
  [Jev decision routing][d-jev] (`jev.minConfidence`, the pinned model, the key
  source); [Open decisions][d-open], item 4, as answered by
  [decision 1](#decisions) and reconciled by DOC-1.
- **Files.** Create `t3compose/config.py`, `docs/configuration.md`,
  `docs/examples/agent-routing.example.json`,
  `docs/examples/agent-routing-state.example.json` and `tests/test_config.py`.
- **Interfaces.** `t3c config check [--config PATH] [--state PATH]` prints the
  digest, the rule version, the risk facts with their floors and any notices;
  it exits 0 or 3. `load_config()` returns a validated, read-only view and its
  digest, plus the routing-policy digest defined in [Record format](#record-format).
  Nothing is cached between commands, so a valid edit applies to the
  next dispatch and running children keep their recorded selection.
- **Acceptance.**
  1. Each validation rule has a passing and a failing test.
  2. The current version-1 shape fails with a message that names the missing
     sections.
  3. Both examples pass and use synthetic instance names. A primary with a
     non-primary fallback and its reverse pass; missing tier associations,
     duplicate sources in one tier and pool endpoints fail.
  4. An error names the path and the rule, and never prints a value read from a
     key file.
  5. `docs/configuration.md` says that Shawn changes values, or the manager at
     his instruction, and that a change applies to the next dispatch only.
- **Offline tests.** `SUITE -p 'test_config.py'`, then `SUITE`.
- **Depends on.** HS-1.
- **Parallel with.** RC-1, PV-1a; DOC-1 may still run.
- **Risk facts and tier.** Shared interface change (JV-1 and RT-1 read this
  contract) → the medium tier. Schema validation is bounded and fully
  testable; RT-1 enforces role exclusions. Reviewer: medium.
- **Review focus.** Strictness; the agy and fallback rules; that no routing
  preference hides in a constant.
- **Size.** About 700 lines.

### RC-1 Records store

- **Objective.** Store work records as [Record format](#record-format) defines:
  append-only, atomic, validated, with one writer, history and recovery from
  interrupted writes.
- **Design.** [Sources of truth and local records][d-records];
  [Manager threads and bases][d-bases] (the three commits);
  [Work cycle][d-cycle], steps 3 and 5;
  [Taking over existing Claude work][d-takeover] (reconcile before acting).
- **Files.** Create `t3compose/records.py`, `t3compose/schema.py` (event kinds
  as data), `docs/records.md`, `tests/test_records.py` and
  `tests/test_reconcile.py`. Modify `t3compose/jsonio.py` and
  `tests/test_jsonio.py` to add atomic replace and exclusive create.
- **Interfaces.**
  - `t3c work new --repo REPO --work ID --file WORK.json --writer W`
  - `t3c event add --repo REPO --work ID --kind KIND --file DATA.json --writer W [--checkout PATH]`
  - `t3c work show|list|snapshot|reconcile|repair --repo REPO [--work ID] [--through-seq N]`
    (`--through-seq` is for `snapshot` only).
  - `REPO` is an identity or a checkout path. `snapshot` prints a checkpoint:
    the last sequence and the canonical digest of `work.json` and its event
    prefix. `--through-seq` recomputes an earlier checkpoint. `reconcile` lists
    uncertain items: intents without confirmation, confirmed dispatches without
    a completed result or a stopped-and-preserved reconciliation, operation
    intents without results, and orphaned temporary
    files. `repair` prints and then removes orphaned temporary files; it never
    changes an event.
- **Rules.**
  - An event goes to a temporary file, is synced, then is linked to
    `<seq>.json` by exclusive create; its kind is inside the file. An existing
    destination means another writer owns that sequence, and the command exits
    2 without replacing the event or retrying at a later sequence.
  - The writer must equal the record's current writer. A `writer-transfer`
    needs a reconciliation showing the old writer stopped, its children were
    reconciled and its artifacts preserved. It also needs a checkpoint of the
    prefix before the transfer; uncertain liveness blocks transfer.
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
  - An invalid record makes commands on that work exit 2. Other work stays
    usable.
- **Acceptance.**
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
  9. Classifications and judgments must reference an existing routing input
     with matching input and policy digests.
- **Offline tests.** `SUITE -p 'test_jsonio.py'`, `SUITE -p 'test_records.py'`,
  `SUITE -p 'test_reconcile.py'`, then `SUITE`. Fixtures use synthetic task
  IDs and paths.
- **Depends on.** HS-1.
- **Parallel with.** CF-1, PV-1a; DOC-1 may still run.
- **Risk facts and tier.** Subtle invariants (recovery, data loss,
  concurrency) → the challenging tier. Reviewer: challenging.
- **Review focus.** Crash windows and fsync order; the exclusive-create race;
  the duplicate-dispatch block; writer checks.
- **Size.** About 1,200 lines, including the persistence tests moved from
  HS-1. If it grows past 1,200, checkpoint and reconciliation commands move to
  a challenging follow-up Task, which JV-1, RT-1, SK-1 and PH-1 then depend on.

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
- **Depends on.** HS-1.
- **Parallel with.** CF-1, RC-1.
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
- **Interfaces.**
  - `t3c route input --repo REPO --work ID --input TASK.json` records the
    common input with RC-1 and prints its reference, input digest and policy digest.
  - `t3c route judge --repo REPO --work ID --input-ref SEQ --policy-digest DIGEST --domain D --difficulty X --reason TEXT`
  - `t3c route select --repo REPO --work ID --role worker|reviewer|feature-lead|text-helper --input-ref SEQ --policy-digest DIGEST --round first|corrections --review-scope code|document|corrections-delta --catalog FILE --catalog-read-at TIME [--escalate]`
    prints the provider instance, model and options for `delegate_task`.
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
    `--escalate` sets it one step above the highest Worker tier already recorded
    on the Task; escalation from challenging exits 4. Easy work goes to the agy
    pool only when no floor applies.
  - Corrections with enumerated findings use the medium tier under the prose
    rules; a brief that adds unresolved work is split or classified afresh.
    Reviewer tier: medium for documents and scoped Corrections re-reviews;
    first reviews of challenging code follow the Task's tier, including any
    escalation. Other first reviews are at least medium. The round and scope
    are recorded; a Reviewer selection is never a pool member.
  - Feature lead: the preference at the Task's tier, at least medium. Text
    helper: the fixed `textHelper` route, with no classification.
  - A selection is available only when the catalog lists the instance and the
    model, the provider reports `canRunChildTask: true`, and the catalog
    advertises its options. A failed status check counts as unavailable. A
    catalog read more than 15 minutes earlier is refused.
  - An unavailable selection uses the role's one configured fallback at the
    selected tier, once. The target need not be a primary preference, but its
    tier association, role eligibility and live availability are checked. If
    the target is unavailable too, or no chain applies, the command exits 5
    (report to Shawn). The master selection never falls back.
  - agy: use the active account until confirmed exhaustion, then the next
    non-exhausted account in pool order. An exhausted account returns only after
    its reset time passes or an `available` observation. Unknown quota is usable.
    An unavailable active account stops with exit 4; it is not rotated. When
    every account is exhausted, the command exits 5.
  - `route select` writes only its `routing-selection` event. `quota record`
    and `quota activate` write the quota file atomically and keep every
    observation. Tool and correctness failures are not quota evidence;
    `quota record` needs a status and the evidence text.
- **Acceptance.** Offline tests cover each rule, including a changed input,
  criteria or threshold after both classification and judgment; an explicitly
  named stale input or policy; a non-primary fallback; a fallback at the wrong
  tier or ineligible for the role; and the first-review, document, Corrections
  and scoped re-review tiers. `Simulated…` tests inject
  quota observations to show rotation in order, wrap-around, reset evidence and
  all accounts exhausted. `live/catalog_shape.py` validates a saved real catalog
  (L3).
- **Prework.** Before dispatch, the manager saves a redacted sample of a real
  `orchestrator_capabilities` result in `.local/state/` and names it in the
  brief. The parser reads only the fields it needs and treats an unknown shape
  as unavailable.
- **Offline tests.** `SUITE -p 'test_routing.py'`, `SUITE -p 'test_quota.py'`,
  then `SUITE`.
- **Depends on.** CF-1, RC-1.
- **Parallel with.** JV-1; L1 and L2 as their prerequisites finish.
- **Risk facts and tier.** Guardrail boundary (role exclusions, agy never a
  Reviewer) and subtle invariants (one fallback, exhaustion order) →
  the challenging tier. Reviewer: challenging.
- **Review focus.** Tier arithmetic; no path to agy for a Reviewer or a floored
  Task; one fallback at most; the rotation rules; the config digest in every
  record.
- **Size.** About 1,100 lines. If it grows past 1,200, `quota` moves to a
  follow-up Task.

### JV-1 Jev adapter

- **Objective.** Ask Jev the domain and difficulty questions in one bounded
  request, validate the answer and record the classification.
- **Design.** [Jev decision routing][d-jev];
  [Routing and personal configuration][d-routing].
- **Files.** Create `t3compose/jev.py`, `live/jev_contract.py` and
  `tests/test_jev.py`.
- **Interfaces.** `t3c route classify --repo REPO --work ID --input TASK.json [--context EXTRA.json]`.
  `TASK.json` holds only `intent`, `acceptance`, `interfaces`, `scope`,
  `riskFacts` and `dependencies`. Unknown keys fail, so global boilerplate
  cannot enter. The input is capped at 16 KiB. Exit 0 means accepted. Exit 4
  carries `retry-with-context` or `manager-judgment` and a reason. Exits 2 and 3
  mean invalid input or config.
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
  - Before the request, RC-1's input recorder appends the canonical
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
- **Depends on.** CF-1, RC-1, PV-1a.
- **Parallel with.** RT-1; L1 and L3 as their prerequisites finish.
- **Risk facts and tier.** Security boundary (a credential and an external
  service) → the challenging tier. Reviewer: challenging.
- **Review focus.** The key on every error path; strict validation; confidence
  compared, not probability; bounded retries; the identifier screen.
- **Size.** About 900 lines.

### RP-1 Router pilot with synthetic inputs

- **Objective.** Exercise Jev routing on labeled synthetic examples while the
  rapid-ship hold lasts, and give Shawn results for tuning the threshold, the
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
  `python3 -I -S live/router_pilot.py --examples pilot/router-examples.json --catalog FILE --catalog-read-at TIME`.
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
- **Interfaces.** `t3c evidence check --repo REPO --work ID --head SHA [--pr-ready]`
  prints `verified` or the gaps. It exits 0 or 1, and 2 for invalid records.
- **Rules.** Verified needs all of the following. The head is the latest result
  commit. Every required check has a pass at the head from the manager, a
  Reviewer or an assigned Session; a Worker's claim alone does not count. The
  latest review covers exactly the review base and the head, and its verdict is
  not "changes required". Every finding of
  every round has a disposition, and no blocking or important finding is open,
  disputed or declined. Findings and dispositions join on `(review task
  reference, finding ID)`, so a prior round cannot clear a later finding with
  the same ID. Every acceptance criterion has evidence at the head.
  `--pr-ready` also needs a confirmed push of the head and a passing
  `pr verify` result.
- **Acceptance.** Offline tests cover: a stale review after the head moved; a
  missing check; a check on another commit; Worker-only evidence; open and
  disputed findings; a declined important finding; a finding resolved outside
  the tree; a second-review rejection; an owner decision; missing criterion
  evidence; repeated finding IDs in different rounds with only one resolved;
  a new commit after verification; and a fully verified case.
- **Offline tests.** `SUITE -p 'test_evidence.py'`, then `SUITE`.
- **Depends on.** RC-1.
- **Parallel with.** RP-1, PV-1b, BT-1, HC-1, GA-1 to GA-3.
- **Risk facts and tier.** None of the floor facts: bounded logic over RC-1's
  schema → the medium tier. It compares records and is fully testable;
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
  `tests/test_privacy.py` and `tests/test_pullrequest.py`.
- **Interfaces.**
  - `t3c privacy check --repo-path P --range BASE..HEAD [--branch NAME] [--text LABEL=FILE ...]`
  - `t3c pr verify --repo OWNER/NAME --number N --head SHA [--base BRANCH] [--author LOGIN]`
- **Rules.**
  - Every check loads the list through PV-1a and blocks on a loader failure.
  - Privacy covers every commit message and the whole tree at every commit in
    the range, the branch name and each text file. A match that a later commit
    removed still blocks, because a push publishes every commit. `*.lock`
    files are skipped, as in the manual check. Author and committer fields are
    not checked ([finding F7][d-findings]); item 14 remains an owner choice.
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
  `t3compose/briefs.py` and `tests/test_briefs.py`.
- **Worker template.** Sections, in order:
  - Role and routing: the tier, the model and the reason; stay on this
    Assignment and this model; start no agents or T3 tasks. A slot carries the
    routing policy's text directly when the Worker is agy.
  - Not alone, and where to work: preserve all edits that are not yours. Work
    only in your own worktree; never touch the owner's checkout or another
    worktree. A slot names any owner hold that applies, such as rapid-ship's.
  - Repository, commits and publication: the provisioned commit read from
    the dispatching checkout at dispatch, the start commit and the review base,
    and the HEAD check with its three outcomes. Never assume the checkout lags
    origin or reuse a provisioned commit recorded before a merge. A
    Worker that finds a commit missing does not fetch. Commit on the branch
    only; do not push, open, edit or merge pull requests, post to GitHub or run
    `gh`.
  - Read first: the repository's guides, the design, the routing policy's path,
    and the repository's settings check where one exists.
  - Owned paths, objective, interfaces, acceptance criteria and the
    repository's test-first rule.
  - Validation: the fast checks; the tree and commit-message privacy checks
    where the repository is public; heavy checks only when assigned, through
    `t3c heavy run`.
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
  required); return format.
- **Interfaces.**
  `t3c brief render --template worker|reviewer|PATH --values VALUES.json --out FILE`
  fills `{{NAME}}` placeholders; a `PATH` lets later Tasks render their own
  templates without changing this module. It refuses a missing value or a
  leftover placeholder, requires the agy slot when a Worker's selection is agy,
  refuses a Reviewer brief whose selection is agy, and prints the brief's
  digest for `dispatch-intent`. `t3c brief check FILE` checks the required
  sections and that the commits are full SHAs.
- **Acceptance.** The templates hold no private identifier and pass the tree
  privacy check. Offline tests cover rendering, missing values, leftover
  placeholders, the agy rules and digests. Every rule listed above appears in
  its template.
- **Offline tests.** `SUITE -p 'test_briefs.py'`, then `SUITE`.
- **Depends on.** HS-1, RC-1 (atomic data writes).
- **Parallel with.** RP-1, EV-1, PV-1b, HC-1, GA-1 to GA-3.
- **Risk facts and tier.** Shared interface change (every Assignment uses these
  briefs) → the medium tier. Reviewer: medium.
- **Review focus.** Completeness against the design; the HEAD-check wording;
  the limits of the privacy-rewrite exception; nothing private.
- **Size.** About 700 lines.

### HC-1 Heavy-check lock

- **Objective.** Run heavy checks one at a time on the machine, across manager
  threads and assigned Sessions.
- **Design.** [Checks and publication][d-publication]
  ([finding F6][d-findings]).
- **Decision.** An exclusive `fcntl.flock` lock on
  `.local/state/locks/heavy-check.lock` in the t3-compose checkout. Every
  manager thread runs the same helper with the same `.local/`, so the lock
  covers the machine. A dedicated runner holds the lock until the foreground
  check exits, even if the calling wrapper exits. The lock inode is created
  once and never renamed, replaced or unlinked. Holder metadata (label,
  commit, runner and check process IDs, start time) is atomically written to
  `heavy-check-status.json` beside it; it is never written into the lock file.
- **Files.** Create `t3compose/heavy.py` and `tests/test_heavy.py`.
- **Interfaces.**
  `t3c heavy run --repo-path WORKTREE --commit SHA --label TEXT --log FILE [--wait] -- CMD [ARGS...]`
  and `t3c heavy status`.
- **Rules.**
  - The command refuses unless the worktree's HEAD is the commit, its tree is
    clean, and it is a linked worktree rather than a repository's main
    checkout, so the owner's checkout is never used. The caller first confirms
    with `task_status` that the worktree's writer has finished.
  - Without `--wait`, a held lock exits 75 with the holder's details. With
    `--wait`, the call blocks in the kernel until the lock frees. The manager
    runs it as a Harness background command that notifies on exit, so nothing
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
  - Afterwards the helper reports whether HEAD and the tree are unchanged,
    prints a summary for a `check` event and exits with the command's status.
- **Acceptance.** Offline tests with subprocesses show that two concurrent
  runs serialize. Kill the calling wrapper while its check keeps running: a
  second run stays busy until that check exits, then can acquire the lock.
  Repeated status rewrites preserve the lock inode and cannot admit a second
  check. Tests also cover signal forwarding, release after the check exits,
  stale metadata, each refusal and the summary's fields.
- **Offline tests.** `SUITE -p 'test_heavy.py'`, then `SUITE`.
- **Depends on.** HS-1, RC-1 (atomic data writes).
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
- **Interfaces.** `t3c publish run -- CMD [ARGS...]`, started from the
  repository directory.
- **Acceptance.** Ported and new offline tests pass. They include a real
  `git credential fill` in a temporary repository with an isolated `HOME` and
  `XDG_CONFIG_HOME`, decoy credential helpers and `GIT_CONFIG_NOSYSTEM=1`,
  where the token wins and the decoy never answers; SSH refusal with
  `GIT_ALLOW_PROTOCOL` set; and revocation after a failing child, an
  interrupted one and a forwarded SIGTERM.
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
  the checks before minting.
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
- **Interfaces.** No new command; `t3c publish run` exits 1 with the kind of
  setting or command form it refused, and mints nothing.
- **Acceptance.** Offline tests under an isolated `HOME`, with decoy configs,
  cover each refusal and each refused command form, and show that a refusal
  happens before any token request. A decoy `GIT_CONFIG` points to a clean
  file while the repository config holds a forbidden setting: preflight must
  still find the setting and refuse. The test asserts that inspection and
  execution receive the same sanitized base environment.
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
- **Interfaces.**
  - `t3c publish push --repo-path P --work ID --head SHA --branch NAME`
  - `t3c publish pr --work ID --head SHA --branch NAME --base main --title-file F --body-file F`
- **Rules.**
  - `push`, in order: the helper's own tracked files must be clean, and its
    commit is recorded; `evidence check` must pass for the head;
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
    then `publish run -- git push --force-with-lease=<ref>:<expected> <https URL> <sha>:<ref>`.
    The explicit lease compares the remote to the recorded prior head (empty
    for an absent branch) during the push. It is used only after the ancestry
    proof, so it cannot authorize a history rewrite; bare `--force` and leases
    without an explicit expected value are forbidden. Any intervening movement
    fails the lease. `git ls-remote` confirms; an `op-result` is recorded. A push to an explicit
    URL adds no remote-tracking ref to the owner's checkout.
  - `pr`, in order: `privacy check` of the title, the body and the branch name;
    an `op-intent`; an existing pull request for the branch is reused, never
    duplicated; `publish run -- gh pr create` with explicit `--repo`, `--head`
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
    a fix to the manager's own text, or a report to Shawn.
  - No command merges. The manager then calls `link_pull_request` and records
    it.
- **Acceptance.** Offline tests, with injected runners, cover each stop
  condition, the order of records, and an interrupted flow that resumes
  without a second push or a second pull request; a Corrections update from
  the confirmed prior head; stale evidence or privacy on that update;
  unexpected remote movement; a non-fast-forward head; and a refused push
  when the branch changes after preflight. The first pull request after
  PB-1 is installed is published through it (L5).
- **Offline tests.** `SUITE -p 'test_publish.py'`, then `SUITE`.
- **Depends on.** RC-1, EV-1, PV-1b, GA-3.
- **Parallel with.** RP-1.
- **Risk facts and tier.** Security and guardrail boundaries (the publication
  identity and privacy) → the challenging tier. Reviewer: challenging.
- **Review focus.** The order of checks; idempotent resumption; push by commit
  to an explicit URL; ancestry proof plus exact lease; no history rewrite or merge.
- **Size.** About 700 lines.

### SK-1 Manager skill and installation

- **Objective.** The skill that runs the work cycle with the helper, and its
  user-level installation.
- **Design.** [Scope][d-scope] (installation); [Roles][d-roles];
  [Work cycle][d-cycle]; [Manager threads and bases][d-bases];
  [Checks and publication][d-publication]; [Design refinement][d-refine].
- **Files.** Create `skill/SKILL.md`, `t3compose/skill_install.py` and
  `tests/test_skill.py`. Modify `README.md` (installation, updates and status)
  and `AGENTS.md` (status).
- **`SKILL.md` covers.**
  - Hard rules: the manager makes no project edits; it does not move or write
    the owner's checkout beyond the design's list; Workers commit on their
    branch only; the manager publishes; agy is never a Reviewer; every Task gets
    an independent review; rapid-ship stays on hold until the pilot.
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
  - Results by worktree path and commit. Reviews: a new `delegate_task` for each
    round. Disputes: one second independent review, then Shawn.
  - Heavy checks through `t3c heavy run`. Verification through
    `t3c evidence check`.
  - Publication: `t3c publish push` and `t3c publish pr` on t3-compose.
    Elsewhere, the repository's own commands with a recorded intent and result,
    then `t3c pr verify`. Then `link_pull_request`. A privacy match leads to a
    Corrections round, a fix to the manager's own text, or Shawn. At a Keychain
    prompt, the manager asks Shawn to choose Allow, not Always Allow.
  - Quota: only confirmed exhaustion and successful availability checks are
    recorded; tool and correctness failures are not quota evidence. Design
    refinement: commissioned
    at the challenging tier and independently reviewed; a change to scope or to
    an owner decision goes to Shawn first.
  - Feature leads stay off until FL-2.
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
  names an explicit interpreter and the helper's absolute path. Installation is
  L6.
- **Offline tests.** `SUITE -p 'test_skill.py'`, then `SUITE`.
- **Depends on.** CF-1, RC-1, EV-1, JV-1, RT-1, PV-1b, GA-1, GA-2, GA-3, PB-1,
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
    publishes no rapid-ship task, and chooses no pilot task.
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
  - Router pilots: synthetic inputs until the hold lifts, then real task inputs
    as well, recorded with each decision.
- **Acceptance.** The files hold no private identifier. Every pilot criterion
  in the design maps to a checklist step. The restart procedure cites L7's
  result and uses RC-1's tested checkpoint contract for both restart phases,
  handoff and writer transfer. `tests/test_pilot_docs.py` renders the kickoff
  template with `t3c brief render` and checks that every `t3c` command the documents name
  exists. If L7 found no way to clear the conversation while keeping the
  thread, PH-1 records the gap and the manager asks Shawn before the handoff.
- **Offline tests.** `SUITE -p 'test_pilot_docs.py'`, then `SUITE`.
- **Depends on.** SK-1 and L7.
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
  their single writer, and reports of outcomes, blockers, changed interfaces
  and evidence to the manager. A lead makes no project edits, does not approve
  its own work and creates no leads. `docs/feature-leads.md` gives the judgment
  criteria for creating a lead, its route (the medium tier; the challenging
  tier for challenging decisions) and the scratch pilot's procedure.
- **Live (L10, scratch).** A scratch lead records an intent in its own record,
  then dispatches its own Worker. An owner question reaches the manager through
  T3's pending-question mechanism or a verified upward report. The lead stops
  mid-Task; the manager reconciles the lead's thread, its children's statuses
  and its artifacts, and a replacement takes over by `writer-transfer` without
  a duplicate child. A record write is interrupted and recovered without a
  duplicate dispatch.
- **Acceptance.** The template renders. The documents hold no private
  identifier. L10's evidence is recorded in `.local/` and reported to Shawn.
- **Offline tests.** `SUITE -p 'test_feature_lead.py'`, then `SUITE`.
- **Depends on.** The first pilot's completion.
- **Parallel with.** None planned.
- **Risk facts and tier.** None for the files: a template and procedures. The
  live pilot is the manager's work → the medium tier. Reviewer:
  medium. A defect that L10 finds in recovery code is fixed by a separate
  Task at the challenging tier; enumerated Corrections follow the medium tier.
- **Review focus.** One writer per record across the manager and the lead; no
  duplicate children; escalations that cannot be lost.
- **Size.** About 500 lines.

### FL-2 Enable Feature leads

- **Objective.** Turn Feature leads on in the skill once L10 passes.
- **Design.** As for FL-1.
- **Files.** Modify `skill/SKILL.md`.
- **Interfaces.** None new; the skill uses FL-1's template and RT-1's
  `feature-lead` role.
- **Exact text.** Replace "Feature leads stay off until FL-2." with:
  "Feature leads are optional after L10 passes. Create one for several
  dependent Tasks, repeated local integration and review, or enough parallel
  work to need a local coordinator. Use one lead level. Route coordination to
  the medium tier and challenging decisions to the challenging tier. Only the
  parent that created a child manages its lifecycle. Use the Feature lead
  template."
- **Acceptance.** Only that replacement. It gives the design's criteria, one
  lead level, the lead's route and the parent lifecycle rule. The `SKILL.md`
  command test passes.
- **Offline tests.** `SUITE`.
- **Depends on.** FL-1 and L10.
- **Parallel with.** None planned.
- **Risk facts and tier.** None; the brief supplies the exact replacement
  after FL-1's evidence → the easy tier. Reviewer: medium, never agy.
- **Review focus.** Agreement with the design's Feature lead options; leads
  stay optional.
- **Size.** About 10 lines; the exact replacement needs no separate module.

## Live checks and owner steps

| ID | When | Who | What | Label |
|---|---|---|---|---|
| L1 | after CF-1 | Shawn, or the manager at his instruction | Add the version-2 sections to the routing config with Shawn's values; `t3c config check` passes | owner step |
| L2 | after JV-1 and L1 | manager | `live/jev_contract.py`: one call to the pinned model | live, synthetic |
| L3 | after RT-1 and L1 | manager | Save a real catalog; run `live/catalog_shape.py` and one `t3c route select` on a synthetic work | live |
| L4 | after GA-1 | manager | `t3c publish check` | live |
| L5 | after PB-1 | manager | Publish the next pull request with `t3c publish push` and `t3c publish pr` | live |
| L6 | after SK-1 | Shawn, or the manager at his request | `t3c skill install`; a new manager Session lists the skill | live |
| L7 | before SK-1 | manager, with Shawn's hand if needed | Restart mechanism and `clientRequestId` retry, in a scratch thread | live |
| L8 | after RP-1 and L1 | manager | `live/router_pilot.py` | live, synthetic |
| L9 | after the handoff | rapid-ship manager thread | The pilot | live |
| L10 | after the pilot | manager | The nested scratch pilot (FL-1) | live |

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
| 1 | CF-1, RC-1, PV-1a | HS-1; DOC-1 may finish alongside them |
| 2 | RT-1, JV-1; L1 to L3 as ready | RT-1: CF-1, RC-1. JV-1: CF-1, RC-1, PV-1a. L1: CF-1. L2: JV-1, L1. L3: RT-1, L1 |
| 3 | RP-1, EV-1, PV-1b, BT-1, HC-1, GA-1 | RP-1: JV-1, RT-1. EV-1: RC-1. PV-1b: PV-1a. BT-1, HC-1: HS-1, RC-1. GA-1: HS-1 |
| 3a | GA-2, then GA-3; other stage-3 Tasks may continue | GA-2: GA-1. GA-3: GA-2 |
| 4 | PB-1; RP-1 may still run | RC-1, EV-1, PV-1b, GA-3 |
| 5 | SK-1; RP-1 may still run | all helper and template Tasks, and L7; RP-1 need not finish |
| 6 | PH-1 | SK-1, L7 |
| 7 | gated handoff, then pilot (L9) | Tasks through PH-1; L1 to L7; Shawn's go-ahead |
| later | FL-1, then FL-2 | the first pilot; FL-2 also waits for L10 |

Routing comes first, as Shawn requested on 2026-10-27. Dispatch CF-1, RC-1
and PV-1a first, then RT-1 as soon as CF-1 and RC-1 finish; JV-1 also needs
PV-1a. L1 follows CF-1; L2 follows JV-1 and L1; L3 follows RT-1 and L1.
The router goes live once JV-1, RT-1 and L1 to L3 pass, before publication
and the skill. DOC-1's remaining squash-trailer choice does not block routing.
L7 is independent of the helper and can run while the routing path is built.

The stages give dispatch priority, not extra dependencies. EV-1, PV-1b,
BT-1, HC-1 and GA-1 may start earlier once their listed dependencies finish
if that does not delay routing. Each parallel group has exclusive file
ownership. The post-routing chain is GA-1, GA-2, GA-3, PB-1, SK-1 and PH-1;
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
| 1 | A privacy match before publication | Decided. A match blocks publication. A match in a commit's message or tree returns to the Worker in a Corrections round whose brief carries the privacy-rewrite exception, which covers only unpublished commits. The manager fixes its own text: branch name, title and body. Shawn decides when an identifier seems necessary. | PV-1b, PB-1, BT-1, SK-1; DOC-1 records it |
| 2 | The failure action for the head-commit check | Decided. It applies to every repository: report to Shawn; not PR-ready. | PV-1b; DOC-1 records it |
| 3 | The open-decisions section's lifecycle | Decided. DOC-1 records the confirmed answer to former open decision 4 and keeps an "Open decisions" heading with one line saying no decisions remain open and pointing to their recorded answers, preserving `#open-decisions`. The pending squash-trailer choice stays a separate owner item. | DOC-1 |
| 4 | Risk floors and the documentation criterion | Decided by Shawn on 2026-10-27 ([decision 1](#decisions)). Floors count changed behavior; documents are medium and technical project documentation is software engineering. CF-1 defines the fields; the confirmed values enter `.local/` at L1. | CF-1, L1, DOC-1 |
| 5 | The interim token step | Decided. The helper absorbs it. GA-1 (minting, scope, revocation), GA-2 (running a command) and GA-3 (refusals before minting) port its reviewed behavior: the key only in an anonymous pipe, the token only in the child's environment, the helper reset, SSH refusal and redirect refusal. PB-1 builds the flow. After L4 and L5, the manager stops using the interim step, and Shawn may delete it. | GA-1, GA-2, GA-3, PB-1 |
| 6 | The interim step's two questions | Decided. Keep `protocol.ssh.allow=never` and remove an inherited `GIT_ALLOW_PROTOCOL`. Revoke every token: `check` fails when revocation fails; `run` warns and keeps the child's status. | GA-1, GA-2 |
| 7 | Heavy-check coordination across manager threads | Decided. One stable `flock` inode in `.local/state/locks/`, separate status metadata, and a runner that retains the lock until the check exits, even if the wrapper dies. | HC-1 |
| 8 | The identifier list | Decided. The helper loads it for every publication check and blocks when it is missing, empty or invalid. JV-1 also screens Jev input with it. | PV-1a, PV-1b, JV-1 |
| 9 | Who installs and updates the interim step | Decided. It changes only through a Worker Assignment with owned `.local/` paths and a review by file hash, as when it was written. The manager runs it only after confirming that the file's hash equals the reviewed hash. It is frozen once GA-3 is reviewed, and retired after L5. | Conventions; GA-3 |
| 10 | The in-flight restart | Procedure decided; mechanism open until L7. L7 tests the ways to clear the conversation while keeping the thread; PH-1 writes the procedure. If no way exists, the manager asks Shawn before the handoff. | L7, PH-1 |
| 11 | A message-only rewrite after a privacy match | Decided. A rewritten commit is a new commit, so the design's rule applies ([Work cycle][d-cycle], step 7). The manager confirms that the trees are identical, the required checks rerun on the new head, and a scoped re-review covers it. Keeping the earlier review would relax the design; only Shawn can make that change. | BT-1, SK-1 |
| 12 | The token step's second-review findings and usage rules | Decided. GA-2 adds V1 (forward SIGTERM and SIGHUP to the child, then revoke) and V3 (build the sanitized environment, removing `GIT_CONFIG`, before both preflight and execution). GA-3 adds V2 (refuse `trace2.envVars` and a false-like `http.sslVerify`), and the usage rules become checks: `publish run` refuses `git -C`, `git -c` and a shell as the command, and PB-1 runs it from the repository directory with explicit `--head` and `--base`. SK-1 carries the Keychain rule: Allow, not Always Allow. If the interim step stays in use for long, a Worker Assignment may add V1 to V3 to it first. | GA-2, GA-3, PB-1, SK-1 |
| 13 | T3 may fast-forward the owner's checkout after merge | Decided. Read the provisioned HEAD at each dispatch; never assume the checkout lags origin. After a merge, the manager may run `git pull --ff-only` on clean `main` only when T3 has not already fast-forwarded it ([decision 2](#decisions)). | RC-1, BT-1, SK-1; DOC-1 records it |
| 14 | GitHub-generated squash trailers | Open for Shawn: permit the generated owner-email trailer or remove it at merge. DOC-1 records his choice once supplied; until then commit-message privacy has no new exception. | DOC-1 |

## Coverage

| Requirement | Design | Tasks |
|---|---|---|
| Helper: bounded Jev request, atomic records, required fields, evidence on the current commit; no agents and no polling | [Sources of truth and local records][d-records] | HS-1, RC-1, EV-1, JV-1 |
| Validated config schema and its digest | [Routing and personal configuration][d-routing] | CF-1 |
| Routing: Jev classification, floors, Reviewer tier, agy never a Reviewer, live catalog, one fallback, agy exhaustion in order, recorded judgment | [Routing and personal configuration][d-routing]; [Jev decision routing][d-jev] | JV-1, RT-1 |
| Records: intent, the three commits, results by worktree path and commit, reviews, operations, decisions; one writer; history; recovery without duplicate dispatch | [Sources of truth and local records][d-records]; [Manager threads and bases][d-bases] | RC-1, EV-1 |
| Publication by commit; Worker App token; privacy; head and author checks | [Checks and publication][d-publication]; [t3-compose repository workflow][d-workflow] | PV-1a, PV-1b, GA-1, GA-2, GA-3, PB-1 |
| Heavy checks one at a time on the machine | [Checks and publication][d-publication] | HC-1 |
| Worker and Reviewer brief templates | [Work cycle][d-cycle]; [Manager threads and bases][d-bases] | BT-1 |
| Manager skill and user-level installation | [Scope][d-scope] | SK-1 |
| Labels, and the design's offline test list | [Pilot and validation][d-pilot] | HS-1; record validation and uncertain dispatch in RC-1; stale reviews and missing checks in EV-1; malformed Jev results in JV-1; unknown quota, failover and floors in RT-1, with simulated quota tests |
| The test command in `AGENTS.md` | [t3-compose repository workflow][d-workflow] | HS-1 |
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
   decisions; the config supplies the models.

**Open owner item (plan input 14).** GitHub-generated squash trailers may
include the owner's email. Shawn chooses whether to permit that generated
trailer or remove it at merge. DOC-1 records the choice once supplied.

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
