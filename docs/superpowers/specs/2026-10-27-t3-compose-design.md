# t3-compose design

Status: design decisions approved by Shawn on 2026-10-27. This revision, made on
2026-10-28, records Shawn's [re-home](#decisions-accepted-at-the-re-home),
[publication](#decisions-on-t3-compose-publication) and
[implementation-plan](#decisions-on-the-implementation-plan) decisions, and the
[findings](#findings). It takes effect when its pull request merges. No
decisions remain [open](#open-decisions).
Implementation and live validation are pending.

Terminology: [project glossary](../../../GLOSSARY.md). A Task is one pull request;
an Assignment is one delegated piece of work, such as its implementation or review.

## Purpose and acceptance

Give Shawn one engineering manager in T3 Code that continues rapid-ship's ongoing
development, delivers features, and refines designs. Preserve or improve the
quality of the existing Claude-managed work while assigning bounded implementation
to cheaper Workers. These goals and the quality criterion are owner requirements.

Quality is evaluated against the applicable repository rules, approved designs,
required checks, review of the final commit (by the assigned Feature lead when a
Task has one, or by a separate independent Reviewer when it does not; plan
decision 13), and resolved review findings. A cheaper model never reduces those
requirements. One successful pilot can demonstrate the workflow, but cannot
establish general quality equivalence between models. The owner judges the pilot
against the existing standard.

## Decision and alternatives

Build t3-compose as a small T3-specific manager package, initially for rapid-ship.
Its source location is `~/workspace/t3-compose`. This is a separate project,
not a rapid-ship feature or a new devctl responsibility. Personal configuration
and runtime state live in the Git-ignored `.local/` directory.

Compose provides valuable roles, task briefs, test-first execution and review
loops. At inspected commit `1d82e94709bdeb2289f75c531fad63b9685b0feb`, its crew
dispatcher requires herdr, and 28 production Python files reference `fc_herdr`.
That count describes coupling, not an estimate that all 28 files require edits.
A complete T3 backend would also need to address implementer execution, reporting,
recovery and agy routing. The owner's later Jev/OpenRouter authorization removes
the routing model's API-authentication conflict, but does not authorize API-backed
Worker models beyond the GLM Worker selection in plan decision 9, or Compose's
entire verification stack. Its skill's
required Linear CLI conflicts with rapid-ship's official MCP/API rule.

A Compose fork is a reasonable upstream contribution when adopting its full
workflow is the goal. It is not the recommended dependency for getting this
project moving. Keep the pilot evidence and the transport requirements suitable
for a later upstream proposal; there is no assumption that maintainers will
accept that proposal. Do not open issues, fork remotely or submit PRs as part of
this design task.

Firstmate already has an experimental T3 backend, but its inspected adapter only
accepts Claude and Codex and manages ordinary threads plus Treehouse worktrees.
It would still need agy support and account rotation for this use case.

Sources:

- https://github.com/prodoxx/compose/blob/1d82e94709bdeb2289f75c531fad63b9685b0feb/SKILL.md
- https://github.com/prodoxx/compose/blob/1d82e94709bdeb2289f75c531fad63b9685b0feb/bin/fc_crew.py
- https://github.com/kunchenguid/firstmate/blob/7da4c3621370534ff9689843d2b77f34158057fb/docs/t3code-backend.md

## Scope

The package provides a manager skill, Worker and Reviewer brief templates, a
Jev routing adapter, a small local task record, and validation of the evidence
needed to finish work. An optional feature-lead role is included below.
It uses T3's existing tools directly from the manager Session.

The manager skill installs at user level, as a symlink into the manager Harness's
user skill directory. The helper runs by absolute path, and personal config comes
from t3-compose's `.local/`. Nothing is added to a managed repository.

It does not provide another agent runtime, scheduler, daemon, UI, authentication
store, worktree pool, issue tracker, automatic merge service or general backend
framework. It does not recreate Compose's Figma, billing, stacked-PR machinery,
browser automation or holdout-test infrastructure. Existing project workflows
continue to supply those capabilities when required.

## Roles

The manager is the owner-facing Opus Session. It reads context, refines priorities,
identifies dependencies, commissions design work, delegates edits, reconciles
results, explains decisions, and owns overall delivery accountability across
features: cross-feature integration, integration tests in place and their
evidence on the intended integrated result, operational readiness, dependencies
and risks resolved or surfaced, and final delivery readiness to Shawn; for a led
Task, it sets the expected integration tests and machine prerequisites at Task
start and checks `main` and the machine after a merge (plan decisions 13 and 17).
When no Feature lead is assigned, the manager also performs
Task-level coordination, required-check execution or assignment, independent
review coordination through the final head, and publication of the Task's pull
request. It may write task records and briefs.
Project code, tests, configuration and documentation edits go to Workers,
including small fixes and integration conflicts. Coordinators make no project
edits; reading diffs and running checks remain allowed.

Workers own one brief and its named paths. They preserve other work, follow the
project's test-first rules, report evidence and surface decisions outside the
approved scope. They do not broaden their assignment or claim review authority.
They commit on their branch only; an assigned Feature lead publishes its Task's
own pull request, and the manager publishes for Tasks without a lead (see
[Checks and publication](#checks-and-publication)).

Reviewers are separate Claude or Codex agents. They assess specification
compliance, repository standards, correctness and test adequacy against an exact
base and head. They report findings without editing the reviewed tree. agy is
never a Reviewer.

Include an optional feature-lead role in version one. When a Feature lead is
assigned, it coordinates earlier independent reviews, runs or assigns required
checks, personally reviews and clears the Task's final head (with no separate
Reviewer dispatched for that final head), maintains current evidence and finding
dispositions, and publishes its Task's own pull request with the machine
prerequisites and integration evidence in the pull request body (without
authority to change other Tasks' pull requests; merge authority follows the
repository's rules, and in t3-compose only the assigned Feature lead
squash-merges its own Task's pull request as the Worker App after its own
exact-final-head review and passed required checks under plan decision 19). It
tells Shawn in its own thread that the pull request is ready, never reports pull
request readiness to the manager, and sends no progress reports; it sends the
manager one line when its pull request merges, plus blockers, cross-Task or
shared-interface changes, new machine prerequisites, owner rule changes and agy exhaustion. Because a lead makes no
project edits, its final-head review does not approve its own implementation
(plan decisions 13, 17 and 19). Use direct children for bounded independent
tasks in managed repositories; in t3-compose every Task has an assigned Feature
lead under plan decision 19. Enable real feature leads in the package skill only
after the nested pilot. The package does not impose a new agent-count cap; the
owner, runtime capacity, dependencies and exclusive file ownership determine
safe parallelism.

## Feature lead options

Flat delegation sends every Worker and Reviewer result to the Opus manager.
It has the fewest handoffs and keeps decisions in one context. It suits a short
fix, one independent task or the first controlled pilot. Across several active
features, the manager must absorb each test failure, review round and dependency,
which can consume its context and compete with its conversation with Shawn.

A permanent lead per feature gives each feature a local coordinator. It suits
long-running features with internal dependencies and repeated review/fix cycles.
The costs are an additional model Session, repeated context, another handoff and
more complicated recovery. A lead that merely repeats the manager's brief adds
cost without reducing coordination work. It does not inherently improve quality.

Use optional feature leads from version one, instantiated when
a feature has several dependent tasks, repeated local integration/review cycles,
or enough parallel activity that the manager would otherwise track every detail.
These are judgment criteria, not an automatic task-count threshold. Simple work
in a managed repository continues through direct delegation; in t3-compose every
Task has an assigned Feature lead (plan decision 19). One lead level is enough;
leads do not create other leads.

The manager owns cross-feature priorities, shared interfaces, owner decisions,
the global assignment record, assigning feature leads, and overall delivery
accountability: cross-feature integration, integration tests in place and their
evidence on the intended integrated result, operational readiness, dependencies
and risks resolved or surfaced, and final delivery readiness to Shawn; for a led
Task, it sets the expected integration tests and machine prerequisites when the
Task starts and checks `main` and the machine after a merge (plan decisions 13
and 17). A feature lead owns one approved feature brief,
decomposition within that scope, its Worker and earlier-round Reviewer
dispatches, local integration coordination, its Task's final-head review and
verification, and publishing its Task's own pull request. When a feature lead is
assigned, it must hold the original requirements, relevant designs and
interfaces, the final diff, callers and tests, and the check and review evidence
needed to personally review the final head. Under plan decision 13, when a Task
has a feature lead, the lead's own review is the final-head review and no
separate Reviewer reviews that head; reviews of earlier heads (Worker rounds and
Corrections re-reviews) still use separate Reviewers, and Tasks without a
feature lead still require a separate Reviewer on the final head. The lead runs
or assigns required checks (including the repository's heavy required check),
records its final-head review and finding dispositions, and publishes its Task's own
pull request under the repository's identity and privacy gates, with the machine
prerequisites and integration evidence in the pull request body (without
authority to change other Tasks' pull requests; merge authority follows the
repository's rules, and in t3-compose only the assigned Feature lead
squash-merges its own Task's pull request as the Worker App after its own
exact-final-head review and passed required checks under plan decision 19; the
manager publishes for Tasks without a lead, and never merges in t3-compose).
Under plan decision 17, Shawn reads open pull requests himself and the lead
tells Shawn in its own thread when the pull request is ready; a feature lead
never reports to the manager that a pull request is ready and sends no progress
reports. A lead sends the manager one line when its pull request merges, plus
blockers, cross-Task or shared-interface changes, new machine prerequisites,
owner rule changes and agy exhaustion, without turning that into a status-report channel. The manager uses the lead's verified
Task evidence and pull request body without adding a manager pre-merge readiness
approval or routinely repeating the same final-head review. A Verified Task or
PR-ready Task is distinct from integrated delivery readiness; the package
introduces no new deployment authority, no automatic readiness rule, and no
general merge authority beyond each repository's rules and plan decision 19's
own-Task t3-compose lead grant. A feature lead delegates project edits and makes
no implementation edits itself, so it cannot approve its own implementation when
it reviews a Worker's final head. Its software-engineering coordination route
uses the configured selection at the Task's tier, at least medium; challenging
engineering decisions use the challenging tier.

Only the parent that created a T3 child manages that child's task lifecycle. Thus
the lead manages its Workers; the master manages the lead. A lead cannot manage a
child created by the manager merely because the lead owns final-head verification.
Leads own their feature's work records and return child task references and
evidence; the manager alone owns the global assignment index. These are linked
records, not competing copies of a global state store. A parent
completion wake is not assumed while a lead still has live children; important
owner questions and cross-feature blockers must use T3's supported pending-question
mechanism or a verified upward-report path. Native nested delegation, escalation
delivery and recovery must pass a scratch pilot before real feature leads are enabled.
On lead failure, reconcile its thread, child statuses and artifacts before replacing
it. An existing child is never duplicated because a new lead cannot find its state.

The coordinator no-project-edit rule is instructional in version one. Existing
repository hooks and CI keep their actual enforcement. Do not claim the package
creates a security boundary or that full-access agents cannot bypass its records.

## Routing and personal configuration

Use `.local/agent-routing.json` in the t3-compose root as the canonical personal
routing config. This file and its adjacent policy and quota state are Git-ignored. The adjacent Markdown file explains operating rules and points to it.
The config selects models and reasoning options for the medium and challenging
tiers in each domain, the easy Worker pool and the text helper. These are
preferences, not hard-coded behavior. The master remains Opus 5.5; only Shawn
chooses a change to the Engineering manager's identity. Account identifiers and
observed quota state remain private. Package source contains no credentials.

Load and validate the config before every dispatch. Valid changes apply to future
dispatches without restarting the manager. Running children keep their recorded
selection. Invalid configuration blocks new routing rather than silently keeping
a cached value. Record the config digest with each selection so later changes do
not obscure why a model was chosen. Personal configuration can change models,
reasoning, account order and thresholds, but cannot waive repository requirements.

The configurable routing rule includes classification criteria, task-risk floors
and round tiers, not only model names. The current config captures model choices,
the confidence threshold and the approved availability fallback chains.
Implementation must add the classification criteria, task-risk floors and round
tiers below.
Code validates and applies those values; it must not hide changeable routing
preferences in prompts or constants. Repository rules, including agy never
Reviewer, remain separate constraints.

The manager declares a Task's risk facts from a configured list. Each fact has a
configured minimum tier, its floor, which is medium or challenging; there is no
easy floor. The first-round Worker tier is the higher of the difficulty, from
Jev or recorded manager judgment, and every applicable floor. Easy first-round
work goes to agy only when no floor applies. A floor counts what a Task changes.
The first implementation of code that handles credentials or enforces a
guardrail, and recovery or concurrency invariants, is challenging. Shared
interface changes are medium. Documents are medium; technical documentation of
a software project counts as software engineering. Exact-text mechanical edits
can use the easy tier. These are the initial values from
[plan decision 1](#decisions-on-the-implementation-plan), dated 2026-10-27.

Corrections rounds with enumerated findings use the easy tier, even when a
floor set the first round's tier. A brief that adds unresolved work is split or
classified afresh. Repeated correctness failures escalate to a medium-tier
Worker. Document reviews and scoped Corrections re-reviews use the medium tier.
First reviews of challenging code follow the Task's tier, including escalations;
other first reviews are at least medium. agy is never a Reviewer. The config's
`roundTiers` values set the Corrections, scoped re-review and document-review
tiers; plan decisions 5 and 6 supply their initial values. Risk facts, floors
and domain criteria are also config values, not constants.

The manager resolves each selection against T3's live catalog. A model is
available only when the catalog lists it and its provider reports
`canRunChildTask: true`. A failed provider status check counts as unavailable.
A listed model does not prove remaining quota. Persist a dispatch's selection
and reason so the owner can see why cheap work was escalated.

Resolve a pool selection from the active account in pool order, wrapping around.
Skip an account that does not offer the requested model and options; record
"model not offered". This is not quota evidence and does not change the active
account. Skip confirmed exhausted accounts until their reset time passes or
availability evidence clears them. Quota observations apply per account to all
its pool models, not per model. Select the first eligible offering account;
never select an unavailable account. The active account missing from the catalog,
reporting `canRunChildTask: false`, or failing its status check stops with exit 4,
without rotation. This rule applies before walking the pool. `easyWorker` uses
the same model-aware walk. Record the chosen account and every skipped account
with its reason in the private routing-selection record. Walking accounts is
not a fallback. If none is eligible, use the selection's one fallback or exit 5.

Resolve the old attempt before retrying. Preserve its work and supply a continuation
brief. Tool failures and correctness failures are not quota evidence. Repeated
correctness failures escalate to a medium-tier Worker. An unavailable master is
reported to the owner; the package does not replace the Engineering manager
without Shawn's explicit choice. For Workers, Reviewers and Feature leads, apply
the configured availability fallback at the selected tier. The config names
each source and target selection and its options; each chain has exactly one
target. Re-home decision 3 added reverse chains. A direct `Selection` may
carry an optional `roles` list: a non-empty subset of
`availabilityFallback.roles`; absent means all of them. A direct selection never
names a pool member. Under plan decision 12, a `PoolSelection` may be a medium
preference or either endpoint of a medium chain only with exactly
`"roles": ["worker"]`. Missing roles, another role or the challenging tier
fails validation. `easyWorker` and `textHelper` retain their existing schema and
semantics. agy is never a Reviewer. A role the selection does not serve treats
it as unavailable and may use that selection's
one fallback, once. Check the target's tier, role eligibility and live
availability. If it is unavailable too, or no chain applies, report to Shawn
(exit 5); never fall back twice or follow the target's own chain. Plan decision 9
supports GLM 5.3 as a medium Worker selection; plan decision 12 adds Argon
through the agy subscription. Shawn chose Argon as the medium software-engineering
Worker preference, with Sol as its one fallback; GLM remains unplaced. Medium
Reviewers and Feature leads use that fallback to Sol. The Engineering manager
applies the choice in private config. Argon needs no new API exception.
Record and report the reason, preserve all quality
requirements, and resolve the original attempt before retrying. Availability
fallback grants no extra permissions.

## Jev decision routing

Use Jev through OpenRouter, as explicitly authorized by the owner on 2026-10-27.
The helper calls OpenRouter's Decisions API directly; no Harness or T3 provider
runs Jev (plan decision 11).
The Jev exception is limited to the router. Claude, Codex and agy execution
continues through the existing subscription Harnesses; plan decision 9 adds
only the GLM medium Worker selection in Pi through OpenRouter as a second
exception.
Jev credentials stay in the existing private credential configuration; never
place them in briefs, source, logs or argv. The routing adapter reads its own
key from the `OPENROUTER_API_KEY` environment variable
first, else from a key file that personal config in `.local/` names.

Compose's `fc_routejev.py` asks typed questions about workflow and depth, retries
once with dependency context when uncertain, and applies policy floors in code.
Its `fc_tiers.py` then maps depth to configured model tiers. Firstmate's
`fm-dispatch-resolve.sh` asks Jev to match a task-specific brief to natural-language
rules, validates the returned choices/probabilities, then applies deterministic
model and account rules. Firstmate already supports the OpenRouter Decisions API.
Use these approaches as references; preserve MIT attribution when reusing code.

The adapter sends original task intent, acceptance criteria, relevant interfaces,
scope and task-specific risk facts. Omit repeated global instructions and credential
or account data; boilerplate safety language must not make every task appear hard.
Ask closed-choice questions for domain (software engineering, other, mixed/unclear)
and difficulty (easy, medium, challenging, insufficient context). These are
classification results, not free-form model identifiers. The domain criteria must
say that technical documentation of a software project counts as software
engineering, as plan decision 1 confirms. In the live check, jev-1.14 classified
a README spelling fix as domain OTHER (F3).

Code maps accepted classifications to the owner's routes. It independently applies
role exclusions (especially agy never Reviewer), hard task-risk floors, live model
catalog validation and sequential account exhaustion. Mixed/unclear work returns
to the manager to classify or split. Cross-cutting API changes, security boundaries
and unresolved engineering design do not become easy solely because Jev says so.

Validate response shape, choice membership, finite probability values and the
documented probability totals. Distinguish the chosen option's probability from
the provider's separate confidence metric. Start with Firstmate's default 0.6
minimum confidence, as the owner requested. Store it as `jev.minConfidence` in
the config and apply it to each required classification's confidence. It is a
starting setting, not a claim of measured accuracy. Shawn can adjust it from
qualitative pilot observations; a calibration study is not an entry requirement.
Low confidence gets at most one retry with relevant extra context, then
manager judgment with an explicit reason. API or malformed-response failures also
fall back to recorded manager judgment, never silent selection of the cheapest model.

Store the task-input digest, routing-rule version, domain criteria, declared risk
facts, configured Jev model, the response's snapshot and ID, classification,
probabilities, confidence, fallback/override reason and final selection.
Reclassify when task-relevant inputs or policy change. A new snapshot behind the
configured Jev alias is not a policy change. Availability and quota are checked
at dispatch even when classification is reused.

Jev does not generate prose. Typed routing needs no separate text model. When a
bounded text-generation step is useful, use the configured text helper through
T3 as a distinct fixed-role task, then consume its result. Do not add a synchronous
agy API gateway or route the router's own prerequisite recursively. A generated summary may assist
the manager but cannot replace original requirements in a consequential routing decision.

The existing local Jev browser skill uses OpenRouter for decisions and a separate
Claude/OpenRouter text helper. It does not already implement agy text generation
or this task router. Leave that browser setup unchanged; no browser is required
for routing.

Configure `~typesafe/jev-latest`, an alias that OpenRouter resolves to the
current Jev snapshot (plan decision 11). A live call with synthetic input
verified this Decisions API contract on 2026-10-27 (F3):

- Request: `POST https://openrouter.ai/api/alpha/decisions` with body
  `{model, state, questions: {<name>: {type: "choice", instructions, criteria: {<OPTION>: <text>}}}}`.
- Response: `answers.<name> = {type: "choice", choice, probabilities: {<OPTION>: p}, confidence}`,
  plus top-level `id`, `model`, `provider` and `usage`
  (`input_tokens`, `output_tokens`, `cost`).
- `model` is the resolved snapshot: `typesafe/jev-1.14` resolved to
  `typesafe/jev-1.14-20261014`. On 2026-10-28, `~typesafe/jev-latest` resolved
  to the same snapshot (F3).
- Probabilities summed to 1. `confidence` is separate from the chosen option's
  probability: 0.79 against 0.86 in one answer.
- A two-question call cost about 0.000024 USD.

OpenRouter's public model list omits decision models, so check availability with
a real call.

Additional sources:

- https://github.com/prodoxx/compose/blob/1d82e94709bdeb2289f75c531fad63b9685b0feb/bin/fc_routejev.py
- https://github.com/prodoxx/compose/blob/1d82e94709bdeb2289f75c531fad63b9685b0feb/bin/fc_tiers.py
- https://github.com/kunchenguid/firstmate/blob/7da4c3621370534ff9689843d2b77f34158057fb/docs/configuration.md#crew-dispatch-profiles-configcrew-dispatchjson
- https://openrouter.ai/docs/guides/community/jev-tutorial
- https://openrouter.ai/typesafe/jev-1.14 (returned a 404 page on 2026-10-27; F3)

## Sources of truth and local records

Repository designs and rules remain the technical authority. Linear remains the
project backlog, accessed through its official MCP or API. T3 owns child identity,
execution status, conversations, notifications and cancellation. Git and CI own
code and check evidence. Do not duplicate a live worker status machine locally.

A local work record stores only coordination and acceptance facts:

- Work ID and Linear issue, approved scope and design references.
- Repository identity, target branch, review base, owned paths and dependencies.
- Dispatch intent ID, T3 task IDs, selected role/model, provisioned and start
  commits, and returned worktree path.
- Result commits, check commands/outcomes and links or paths to their evidence.
- Review event reference (its sequence number), reviewer provenance (an
  independent Reviewer's T3 task ID, or the assigned Feature lead's thread
  without a fabricated task ID), reviewed base/head, review evidence path and
  digest (required for a lead review, optional for a task review), findings,
  responses, dispositions and review verdict, plus manager-recorded Feature lead
  assignments (plan decision 13).
- External operation intent and confirmed result for each push and pull request,
  and for other operations where the repository requires it.
- Owner decisions and next action, with an explicit unknown value where unresolved.

A record identifies a Worker's result by its worktree path and result commit, not
by a branch name, because T3 can rename a child's branch after provisioning (F8).
Read a branch name from the worktree when one is needed.

Each record has exactly one coordinator writer. For direct tasks that is the
manager; for lead-owned tasks it is the assigned feature lead. The manager alone
assigns a feature lead in the record; the latest assignment event names the
current lead. Assignments obey the same single-writer and reconciled-transfer
rules; manager-only assignment does not permit editing a live lead's record.
The lead persists intent in its own feature record before dispatch, without
waiting for a manager round trip. The manager owns the global index and reads linked feature records
for delivery readiness decisions; it does not rewrite a live lead's record.
Workers return their claims and artifacts through T3; a Worker's claim alone is
insufficient for check verification, whereas check evidence verified by the
manager, the current feature lead, a Reviewer or an assigned Session counts
(plan decision 13). Ownership transfer requires reconciling the old writer
and its children first; uncertain liveness blocks that transfer. The nested pilot
must test interrupted record writes and recovery without duplicate dispatches.
Keep runtime records out of tracked source and out of the managed project,
under t3-compose's Git-ignored `.local/state/`, keyed by repository identity. Existing account policy/state files
remain canonical; the new record refers to them rather than copying their contents.

Use a small Python standard-library helper on CPython 3.15 or later (stable
releases only, `sys.version_info.releaselevel == "final"`, selected through `uv`'s
global pin `uv python pin 3.15 --global` and resolved with
`PY="$(uv python find 3.15)"`; plan decision 14), run with `-I -S`, that handles
the bounded Jev request, writes records atomically, validates required fields and
checks whether evidence covers the current commit. Invocation uses
`"$PY" -I -S <root>/bin/t3c version`; the suite uses
`"$PY" -I -S -B -m unittest discover -s tests -t . -v`. On 2026-10-28, Python.org's
[official downloads listing](https://www.python.org/downloads/) lists stable
CPython 3.15.2, released 2026-10-25
([Python 3.15.2](https://www.python.org/downloads/release/python-3152/)), making 3.15 the
current stable minor; the runtime floor is 3.15, and Python 3.9 and the macOS
system interpreter are not supported. The helper launches no agents, polls
nothing, and performs no runtime network lookup to discover Python releases; a
move to a newer stable minor is an explicit owner pin and Task update. The exact
storage format and command names are fixed in the implementation plan (including
plan decision 15). Preserve historical attempts and reviews. Missing or invalid
records block the affected acceptance decision, not unrelated work.

## Manager threads and bases

Each managed repository has one manager thread, bound to a checkout of its T3
project's repository. T3 takes a worktree-isolated child's base from the manager's
checkout HEAD but creates the worktree in the T3 project's repository, and it
cannot change a thread's workspace after creation. A thread bound to another
repository therefore cannot provision such children (F1). The t3-compose build
manager is bound to the t3-compose root; the pilot runs from a manager thread
bound to the rapid-ship root (re-home decision 1).

A root-bound manager uses the owner's checkout. It never moves that checkout or
writes to it: no switch, reset, stash, commit or file edit. The exceptions
are:

- The manager's records under t3-compose's Git-ignored `.local/state/`.
- The quota state that the personal routing policy in `.local/` assigns to the
  manager.
- Notes in the personal routing policy that record a decision Shawn made and its
  date. The manager does not change routing values or account order without
  Shawn's instruction.
- A plain `git fetch`, which adds objects and updates remote-tracking refs.
- After a merge, `git pull --ff-only` on the owner's clean `main`, only when
  T3 has not already fast-forwarded it (plan decision 2, dated 2026-10-27).
- A push of a reviewed commit for publication, which changes only the remote and
  its remote-tracking refs.
- Operations that Shawn explicitly requests.

Neither a fetch nor a push moves HEAD, the index, the working tree, a stash or a
local branch. After a merge, read the checkout's actual HEAD; T3 may already
have fast-forwarded it (F2). Its HEAD need not be the intended base, for example
an advanced `origin/main` or a stacked parent's head. Read the provisioned
commit again at dispatch rather than assuming the checkout lags `origin`.
When the intended base is not local, the manager fetches before it writes the
brief.

Each Worker brief therefore names three commits:

- The provisioned commit: the checkout HEAD of the thread that dispatches the
  Worker, where T3 starts the Worker's worktree. That thread is the manager's, or
  a feature lead's for the lead's own children.
- The start commit: a new Task's base, or the previous head for a Corrections or
  continuation round.
- The Task's review base. It does not change between rounds.

Before any edit, the Worker checks HEAD and the tree in its worktree:

- HEAD is the start commit: the Worker continues.
- HEAD is still the provisioned commit, the tree is clean and the start commit
  exists: the Worker moves its branch to the start commit. A fresh worktree holds
  no work, so the move discards nothing.
- Anything else, including a branch that already holds commits: the Worker stops
  and reports. A Worker that finds a commit missing does not fetch.

Shared isolation starts a child in the manager's checkout (F1). Use it only for
the takeover exception under
[Taking over existing Claude work](#taking-over-existing-claude-work).

## Work cycle

1. Reconcile existing work before assigning anything. Read the applicable guides,
   run the repository's settings doctor, inspect the approved plan and check that
   no other manager, Feature lead or Worker owns the work being assigned. When a
   Feature lead is assigned to a Task, it runs the Task's local dispatch,
   earlier-round independent reviews, required checks, its own final-head review
   and its Task's pull request publication below with the original requirements,
   relevant designs and interfaces, callers and tests in context; when there is
   no Feature lead, the manager performs that Task-level coordination,
   commissions an independent Reviewer through the final head, and publishes the
   Task's pull request (plan decision 13).
2. Write a brief with objective, scope, the provisioned commit, start commit and
   review base, owned paths, acceptance criteria, interfaces, dependencies, the
   fast checks to run and return format. Unknown product decisions go to Shawn;
   routine implementation choices stay with the assigned agent. Carry agy
   Workers' instructions inline. Under plan decision 9, do the same for Pi
   Workers until its instruction-file handling is verified.
3. Record dispatch intent before calling T3. Use a stable `clientRequestId` for
   retries of that same dispatch and retain the returned `taskId`.
4. Dispatch asynchronously with explicit provider/model selection. Use T3-managed
   worktree isolation for new writers. Confirm first that the start commit exists
   and that the dispatching thread's checkout HEAD is still the provisioned
   commit. T3 starts the worktree at that HEAD and copies no uncommitted changes.
   Before any edit, the Worker checks HEAD as
   [Manager threads and bases](#manager-threads-and-bases) describes.
5. On completion, the responsible coordinator (the assigned Feature lead, or the
   manager when there is no lead) inspects the returned artifacts and commit. A
   completed T3 turn does not prove the brief is complete. Reconcile uncertain
   dispatches before launching another writer; use T3 status/history rather than
   blind retries. Only the parent that created a T3 child manages that child's
   lifecycle.
6. Commission independent review of the exact review base and head for earlier
   Worker rounds and Corrections re-reviews, and for the final head when the
   Task has no Feature lead. Every delegated review round gets a new T3
   delegated task containing the brief, prior findings, responses and unresolved
   objections; never use the child's backing thread to start that round. When a
   Task has an assigned Feature lead, the lead personally reviews the exact
   final head against the original requirements, relevant designs and
   interfaces, the final diff, callers and tests, and the check and review
   evidence, records its review evidence and clears that head; no separate
   Reviewer reviews that final head, and because the lead makes no project
   edits, this does not approve its own implementation (plan decision 13).
7. Send Corrections to a Worker with a brief whose start commit is the previous
   head; the review base does not change. Any new commit invalidates prior final
   acceptance until its required checks and review are current. Neither the
   manager nor a Feature lead marks an unresolved finding resolved merely because
   the Worker disagrees. For a factual dispute, obtain one second independent
   review of the original finding and evidence. Unresolved acceptance-blocking
   disputes and product decisions go to Shawn.
8. Mark work verified only when all acceptance criteria have current evidence,
   required checks passed on the current head, and the current head is cleared
   by the current Feature lead's final-head review (when the Task has a lead) or
   by an independent Reviewer (when it does not). An assigned Feature lead
   publishes its Task's own pull request with the machine prerequisites and
   integration evidence in the pull request body (without authority to change
   other Tasks' pull requests; merge authority follows the repository's rules,
   and in t3-compose only the assigned Feature lead squash-merges its own Task's
   pull request as the Worker App after its own exact-final-head review and
   passed required checks under plan decision 19) and tells Shawn in its own
   thread that the pull request is ready; it never reports pull request
   readiness to the manager and sends no progress reports. The lead sends the
   manager one line when its pull request merges, plus blockers, cross-Task or shared-interface changes, new machine
   prerequisites, owner rule changes and agy exhaustion. The manager publishes
   for Tasks without a lead and tells Shawn when such a pull request is ready.
   The manager retains overall delivery accountability across features—cross-feature
   integration, integration tests in place and their evidence on the intended
   integrated result, operational readiness, and resolved or surfaced
   dependencies and risks: at Task start it sets the expected integration tests
   and machine prerequisites, and after a merge it checks `main` and the
   machine, using the lead's verified Task evidence and pull request body
   without adding a manager pre-merge readiness approval or routinely repeating
   the same Task-level final-head review (plan decisions 13 and 17). Delivery
   readiness is not a per-Task verification
   input. Keep verified, PR-ready, merged and integrated delivery readiness
   distinct. Integration and publishing follow the repository's existing
   authority and identity rules (see
   [Checks and publication](#checks-and-publication)). The package grants no
   new deployment authority, introduces no automatic readiness rule, and grants
   no general merge authority beyond each repository's rules and plan decision
   19's own-Task t3-compose lead grant.

T3 notifications wake the dispatching parent. There are no parallel watcher
processes or polling loops. An interrupted manager or Feature lead reconstructs
decisions from the local record and reconciles actual T3/Git state before
continuing.

## Checks and publication

Workers run the fast checks that the brief names. When a Feature lead is
assigned, the lead runs a repository's heavy required check on the final head,
or assigns one Session at a time to run it; when there is no Feature lead, the
manager does so (plan decision 13). The check runs in a clean linked worktree
whose HEAD is the commit under check and where no writer is active, normally the
Worker's worktree after its turn ends; it never moves or writes the owner's
checkout. Before running or assigning the check in a writer's worktree, the
parent that created that writer confirms that it has completed; only the parent
that created a T3 child manages its lifecycle, so a Feature lead cannot manage a
child created by the manager merely because the lead owns final-head
verification. Heavy checks run one at a time on the whole machine, across all
manager threads, Feature leads and assigned Sessions (F6); how coordinators
serialize this is left to the implementation plan. A check on another commit
does not cover the head, and a Worker's claim alone does not satisfy check
verification; verified check evidence from the manager, the current Feature
lead, a Reviewer or an assigned Session is required. For the heavy check, this
refines step 1 of the [accepted review policy](#accepted-review-policy);
required checks still cover the final head.

Workers commit on their branch only. Workers and Reviewers do not push, open, edit
or merge pull requests, or post to GitHub. An assigned Feature lead publishes
its Task's own pull request, and the manager publishes for Tasks without a lead
(plan decision 13); lead publication grants no authority to change other Tasks'
pull requests, and merge authority follows the repository's rules (in
t3-compose, only the assigned Feature lead squash-merges its own Task's pull
request as the Worker App after its own exact-final-head review and passed
required checks under plan decision 19; the manager never merges in t3-compose).
The publishing coordinator records each publication
intent and its confirmed result. It publishes under the repository's identity
rules (for rapid-ship, its Worker App commands), from a Session rooted in that
repository or in t3-compose, as those rules require (F4). It publishes the
reviewed head by commit, for example `git push origin <sha>:refs/heads/<name>`,
not by branch name, because T3 can rename a child's branch (F8). Before it
reports the Task PR-ready, it confirms that the pull request's head commit equals
the reviewed head and checks the author where the repository's identity rules
name one. A mismatched head or author means: report to Shawn; not PR-ready.
This applies to every repository. It links each pull request to its T3 thread
with `link_pull_request`.

A private-identifier match blocks publication in every repository until
Corrections or Shawn resolves it. A match in a commit's message or tree returns
to the Worker in a Corrections round whose brief permits rewriting only the
unpublished commits needed to remove it. The publishing coordinator (the
assigned Feature lead for its Task, or the manager when there is no lead) fixes
its own branch name, pull request title and body. Shawn decides when an
identifier seems necessary.

These rules are instructional. Every child inherits full access and the owner's
local `gh` login, and Codex and agy children run without rapid-ship's hook until
its Codex and agy wiring merges (F5). Plan decision 9 adds Pi Workers; their
hook enforcement is unverified, so treat them as unguarded too. A child can
still push wherever that login can push; the package creates no security
boundary. For t3-compose's `main` ruleset, see [t3-compose repository workflow](#t3-compose-repository-workflow).

## t3-compose repository workflow

t3-compose is the public GitHub repository `proxy-hatch/t3-compose`, created on
2026-10-27 (re-home decision 4), under the MIT License (publication decision 3).
Its assigned Feature lead publishes its Task's own pull request from its
t3-compose-rooted Session, because a rapid-ship-rooted Claude
Session cannot push to it (F4). Tasks land through pull requests after the
assigned Feature lead's final-head review and passed required checks (plan
decisions 13 and 19).
Workers commit on their branch only. `AGENTS.md` describes this workflow and,
once the helper exists, its test command.

On t3-compose, every Task has an assigned Feature lead. Only that lead
publishes its Task's own pull request, as the rapid-ship Worker App and never
under Shawn's account (publication decision 1; plan decisions 13 and 19). The
lead has no authority over other Tasks' pull requests. On a managed repository,
that repository's identity rules apply instead, as
[Checks and publication](#checks-and-publication) describes. To publish to
t3-compose, the publishing coordinator mints a short-lived installation token
for the Worker App, scoped to t3-compose, and runs `git push` and `gh` with the
token only in their environment. The App's ID, installation account and Keychain
item are personal configuration in `.local/`. An interim token step in `.local/`
serves until the implementation plan decides where the helper provides it. The
token step clears Git's other credential helpers and refuses SSH transport for
the push, so a push cannot fall back to Shawn's stored credentials or SSH key.

When the publishing coordinator opens a pull request, it names the repository,
head branch and base branch explicitly. Before it reports a Task PR-ready, it
also confirms that GitHub records the Worker App as the pull request's author.
If this check fails, or the pull request's head commit differs from the reviewed
head, it reports the failure to Shawn and does not report the Task PR-ready.

The Worker App lacks the workflows permission, so it cannot push changes under
`.github/workflows/`; Shawn pushes those changes. This is known from the same
App's use in rapid-ship and not yet observed on t3-compose.

On t3-compose only, every Task has an assigned Feature lead, and that lead
squash-merges its own Task's pull request as the Worker App after its own
exact-final-head review and passed required checks (publication decision 2, as
superseded on 2026-10-28 by plan decision 19). Merge depends on the pull request
being open and non-draft, clean and mergeable, at the exact reviewed head, with
passed hosted checks if present, no `.github/workflows/` or out-of-owned-path
change, and no open owner question. GitHub generates the squash commit title and
message; agents supply neither. No `t3c` merge command is authorized. The
Engineering manager does not merge, a Feature lead never merges another Task's
pull request, and Workers and Reviewers still never merge. Tasks and stages
continue without waiting for Shawn: the manager checks `main` and the machine
after each merge and starts dependent Tasks and their leads once dependencies
merge. Changes under `.github/workflows/`, routing values and model placement,
the rapid-ship pilot (L9), product decisions and anything unclear remain
Shawn's. A managed repository's own rules decide who merges; rapid-ship's flight
mode lets agents approve and merge pull requests. On 2026-10-28, the manager
read the repository settings through the GitHub API: only squash merges are
allowed, and GitHub deletes a pull request's branch when it merges. `main` has a GitHub
ruleset (publication decision 4): a pull request is required, force
pushes and deletion are blocked, and there are no bypass actors. A child can
still push other branches with Shawn's login, and `main` accepts changes only
through pull requests. Someone holding Shawn's login could still edit the
ruleset, so it guards against mistakes, not deliberate bypass; it is not a
security boundary.

The repository is public, so tracked files, commit messages and GitHub posts,
such as pull request titles, bodies and comments, never hold the owner's email,
keys or tokens, the Jev key file's path, paths under the owner's configuration
directories, agy account or provider-instance names, T3 or Session IDs, or
rapid-ship's task list; write "recorded in `.local/`" instead. Before it
publishes, the publishing coordinator checks the commit messages and the pull
request's title and body against the private identifier list in `.local/`.
Commit author and committer fields do carry the owner's email, which F7 found
already public. On 2026-10-28, Shawn accepted the `Co-authored-by` trailers that
GitHub generates on a squash commit when a pull request merges (including when
Shawn merges or when an assigned Feature lead squash-merges its own t3-compose
pull request under plan decision 19; plan decision 7). They may
carry his commit email; agents never write it. These trailers are the only
exception for commit-message text. They sit on the base branch, normally
outside the review ranges that privacy checks cover. A match in a squash commit
that a branch update brings into a range goes to Shawn.

## Design refinement

Approved designs are the starting point. A new finding can justify a revision;
record the evidence and the affected decision. Commission difficult software design
work to the configured challenging software-engineering selection, and difficult
non-engineering design work to the configured challenging non-engineering selection.
Use independent review for earlier design heads and Corrections re-reviews.
The current Feature lead reviews a led Task's exact final design head; without a
lead an independent Reviewer does so, under plan decision 13.
Refinements that change product scope or an owner decision go to Shawn before
dependent implementation. Unaffected tasks can continue.

Design work uses the project's existing document and review conventions. It does
not turn every small clarification into a new design or recreate settled plans.

## Taking over existing Claude work

Shawn supplied the Claude Engineering manager's context dump on 2026-10-27. The
T3 manager verified it against Git and GitHub the same day: branch heads, dirty
file counts, upstream branches and open pull requests matched the dump. The dump
and its task list are recorded in `.local/`; tracked files do not copy them.

rapid-ship work waits for the pilot (re-home decision 2). Until t3-compose is
built and the pilot starts in the rapid-ship-bound manager thread, do not resume,
review, check or publish any rapid-ship task, and do not choose the pilot task.
t3-compose design, planning and implementation continue. On 2026-10-28
(plan decision 16), Shawn scoped a temporary exception while `t3c` is built:
rapid-ship development is temporarily unblocked under a private prose setup run
by a rapid-ship-bound manager thread. That rapid-ship-bound prose manager first
reconciles the old Claude work as below and dispatches nothing without Shawn's
approval. t3-compose agents still do no rapid-ship work (they read and run
nothing in any rapid-ship checkout), and the formal helper-backed pilot (L9)
still waits for `t3c` and its existing handoff gates.

At pilot start (and before the temporary rapid-ship-bound prose manager takes
over any work under plan decision 16), reconcile the recorded state with live
Git and T3 state before taking over any work: the old Claude manager's workflow
journals, worktrees, branches, commits, dirty files, PRs, review heads and
unanswered decisions. Quota-blocked or quiet does not prove all its children
have stopped. Establish that old dispatch has ceased and resolve active writers
before T3 takes ownership.

Preserve every worktree and uncommitted change. Failed or null workflow results
are incomplete evidence, not proof that nothing was written. Existing dirty trees
cannot be transferred by starting a new isolated child at this thread's HEAD.

For an existing worktree, verify its base, branch, dirty state and absence of a
live writer before assigning a recovery brief. A recovery child may use shared
isolation from the manager and an explicitly owned existing worktree path, working
only there; it must not edit the manager's checkout. This exception is for takeover
only; new work uses worktree isolation. No automatic reset, stash, cleanup or replay occurs.

This exception is not yet verified. The old Claude manager saw the Edit tool
refuse paths under rapid-ship's `.worktrees/` from a Session rooted in another
folder; that is a claim from the previous T3 manager's handoff, not re-run. At
pilot start, test the exception before any recovery brief relies on it.

## Pilot and validation

The formal helper-backed pilot (L9) runs from the rapid-ship-bound manager
thread once t3-compose is built and its handoff gates pass (re-home decisions 1
and 2; plan decision 16's temporary prose setup in a rapid-ship-bound manager
thread does not replace `t3c` or satisfy L9). Starting the formal pilot there is
a first manager context restart: that thread reconstructs state from the
records. It then reconciles existing work, as described under
[Taking over existing Claude work](#taking-over-existing-claude-work), and chooses
one bounded unfinished task whose requirements are approved and whose files have
no active writer. Choose the Worker tier from its risk; preserve the challenging
floor for a sensitive merge-guard change. A medium Worker-only pool selection
under plan decision 12 does not lower any first-round floor.

The first live pilot must demonstrate:

- A real T3 writer on the intended branch/base, followed by a separate Reviewer
  in this first pilot without a Feature lead; lead enablement follows L10/FL-2.
- The repository's required checks and evidence for every acceptance criterion.
- A recorded outcome for every review finding, with re-review after edits.
- Preserved unrelated work and no manager-authored project edits.
- Reconstruction of the same work state after a deliberate manager context restart.

That first restart comes before any pilot Task is dispatched. The pilot must also
restart the manager once while a dispatched pilot Task is in flight, and show that
the records plus live T3 and Git state give the same work state. The in-flight
restart keeps the thread that dispatched the Task, because only that thread
manages the Task's child; the implementation plan defines how the restart is done.

Then exercise one bounded agy task and a design-refinement task through the same
quality standard. Confirm receipt and validation of the required evidence rather
than counting Worker claims or number of agents as success.

Offline tests cover record validation, stale reviews, missing checks, unknown
quota, failover decisions, malformed Jev results, routing floors and uncertain
dispatch reconciliation. Label them offline. During the pilot, inspect
Jev decisions on examples covering engineering/non-engineering,
mixed scope and easy-but-sensitive tasks. Record mistaken cheap routes separately
from unnecessary expensive routes. Use Shawn's qualitative feedback to tune the
configured threshold; no pre-pilot accuracy study is required. Until the formal
pilot starts, pilot the `t3c` router with public or synthetic examples. Once the
formal pilot starts and lifts the `t3c` hold, router pilots may also use real
task inputs, recorded with each decision. Label synthetic evaluation explicitly.
Use injected quota responses to test rotation without deliberately exhausting an
account; label that test simulated. Live quota failover remains unverified until
an actual exhaustion event is observed. Test failure and refusal paths as well as
successful completion. No simulated test counts as a passing live integration.

## Approved scope and implementation status

Shawn approved the design choices on 2026-10-27. Package the discipline as
skills/templates plus a small Jev/record helper. Use T3 only; leave portability
and a Compose upstream adapter for later. Treat the pilot as evidence for
proceeding, not proof of universal quality parity.

The implementation plan is merged. The runtime helper, manager skill and live
pilots remain to be built. Moving the project does not by itself authorize
takeover of rapid-ship work; on 2026-10-28 Shawn scoped a temporary private
prose setup in a rapid-ship-bound manager thread while `t3c` is built (plan
decision 16), while t3-compose agents still do no rapid-ship work and the formal
`t3c` pilot remains gated.

## Accepted review policy

Shawn approved choice 1 on 2026-10-27: independently review every Task.
On 2026-10-28, Shawn refined step 4 and step 5 for Tasks that have a Feature
lead (plan decisions 13, 17 and 19): when a Task has a Feature lead, earlier heads (Worker
rounds and Corrections re-reviews) still go to separate Reviewers, and the
Feature lead's own review is the final-head review, with no separate Reviewer
dispatched for that head; when a Task has no Feature lead, a separate Reviewer
still reviews the final head.
A Task is the work of one pull request, as defined in rapid-ship's GLOSSARY.md.

1. The Worker implements the Task and runs the required checks.
2. Earlier-head reviews and Corrections re-reviews use a separate Reviewer,
   reading the diff, callers and tests against the approved brief and repository
   standards. Final-head review follows step 4; a led Task gets no separate
   Reviewer on that head.
3. The Worker makes required Corrections. Each finding receives an explicit disposition.
4. After Corrections, when the Task has an assigned Feature lead, the lead holds
   the original requirements, relevant designs and interfaces, the final diff,
   callers and tests, and the check and review evidence, personally reviews the
   exact final head commit, records its review evidence and clears it; no
   separate Reviewer reviews that final head. When the Task has no Feature lead,
   a separate Reviewer checks the final head commit after Corrections. Because a
   Feature lead makes no project edits, its final-head review does not approve
   its own implementation. Required checks must also cover that exact final
   commit.
5. For a Task with a Feature lead, the lead verifies that required checks, its
   final-head review and all finding dispositions are current on the exact final
   commit, publishes its Task's own pull request under the repository's identity
   and privacy gates with the machine prerequisites and integration evidence in
   the pull request body (without authority to change other Tasks' pull
   requests; merge authority follows the repository's rules, and in t3-compose
   only the assigned Feature lead squash-merges its own Task's pull request as
   the Worker App after its own exact-final-head review and passed required
   checks under plan decision 19), and tells Shawn in its own thread that the
   pull request is ready. Under plan decision 17, Shawn reads open pull requests
   himself: the lead never reports to the manager that a pull request is ready
   and sends no progress reports. The lead sends the manager one line when its
   pull request merges, plus blockers,
   cross-Task or shared-interface changes, new machine prerequisites, owner rule
   changes and agy exhaustion. The manager
   sets the expected integration tests and machine prerequisites when a Task
   starts and checks `main` and the machine after a merge, using the lead's
   verified Task evidence and pull request body without adding a manager
   pre-merge readiness approval or routinely repeating the same final-head
   review, investigates unresolved findings and cross-feature decisions, and
   owns overall delivery readiness to Shawn: cross-feature integration,
   integration tests in place and their evidence on the intended integrated
   result, operational readiness, and resolved or surfaced dependencies and
   risks. When there is no Feature lead, the manager checks the independent
   review result and check evidence, publishes the Task's pull request, and
   reports readiness to Shawn. Task verification and PR-ready remain distinct
   from integrated delivery readiness.

For who runs a repository's heavy required check, see
[Checks and publication](#checks-and-publication).

Use the configurable engineering model preferences for the Reviewer. Document
reviews and scoped Corrections re-reviews use the configured round tiers,
initially medium. First reviews of challenging code follow the Task's tier,
including escalations; other first reviews are at least medium. agy is never
a Reviewer. Review independence requires a separate Session that did not implement
the change; it does not require a different model vendor.

There is no initial quick-scan stage. A quick scan cannot replace the required
review, whether by the current lead or an independent Reviewer. Merge authority
follows each repository's rules (including plan decision 19's own-Task
t3-compose Feature lead grant), and all repository acceptance requirements
remain unchanged.

## Decisions accepted after grill-with-docs

Shawn accepted all four recommendations on 2026-10-27:

1. Include optional Feature leads in version one, enabled after the nested-lifecycle
   pilot. Keep small independent Tasks under the Engineering manager.
2. Permit configured automatic availability fallback for Workers, Reviewers and
   Feature leads at the selected tier, using the config's source and target
   selections. Report the reason and preserve quality requirements. Engineering manager model changes
   remain Shawn's explicit choice.
3. Keep the manager's no-project-edit rule advisory in version one. Check adherence
   in the pilot and preserve existing repository guardrails. New Harness-specific
   role enforcement is outside version one.
4. Obtain one second independent review for factual Worker/Reviewer disagreements.
   Record the original finding and evidence. Remaining disputes that prevent
   clearance, and product decisions, return to Shawn.

Storage format and helper command names were left to the builder on 2026-10-27;
the implementation plan now fixes them, including CD-2/3/4. Runtime capability
and recovery behavior require implementation tests. Takeover of rapid-ship work
now waits for the pilot (re-home decision 2, with the 2026-10-28 temporary prose
exception in plan decision 16).

## Decisions accepted at the re-home

Shawn made these decisions on 2026-10-27, in the Session that re-homed this work
to manager threads bound to each repository's checkout:

1. Re-home. Build t3-compose from a manager thread bound to the t3-compose root.
   Run the pilot from a manager thread bound to the rapid-ship root.
2. rapid-ship work waits for the pilot. Do not resume, review, check or publish
   any rapid-ship task, and do not choose the pilot task, until t3-compose is
   built and the pilot starts in the rapid-ship-bound manager thread.

   Exception note, 2026-10-28 (plan decision 16): Shawn said, "we have the design down. While waiting for t3c impl, create a prose prompt for this manager/feature lead/worker setup with multi-model in t3 code, so we can temporarily unblock rapid-ship dev".
   This scopes a temporary exception while `t3c` is built: rapid-ship
   development may proceed under a private prose setup run by a
   rapid-ship-bound manager thread, which first reconciles the old Claude work
   and dispatches nothing without Shawn's approval. t3-compose agents still do
   no rapid-ship work (they read and run nothing in any rapid-ship checkout),
   and the formal helper-backed pilot (L9) still waits for `t3c` and its
   existing handoff gates. The private prompt is not a runtime implementation
   and no formal pilot pass is claimed.
3. Add reverse availability fallbacks for Workers, Reviewers and Feature leads
   at the medium and challenging tiers, in addition to the existing forward
   chains. The personal config in `.local/` holds the selections in each
   direction.
4. t3-compose integrates through a public GitHub repository with pull requests.

Earlier owner decisions stand as recorded above.

## Decisions on t3-compose publication

Shawn made these decisions on 2026-10-27, after the re-home:

1. Agent GitHub identity. Agents publish to t3-compose only as the rapid-ship
   Worker App, `proxy-hatch-rs-worker`, installed on t3-compose. The manager
   mints a short-lived installation token scoped to t3-compose, with contents and
   pull request write access, and pushes and opens pull requests with it. Agents
   never push or post under Shawn's account. Workers and Reviewers never push or
   post.

   Role clarification, 2026-10-28: under plan decision 13, when a Task has an
   assigned Feature lead, that lead publishes its Task's own pull request as the
   Worker App under the same identity, privacy and head/author verification
   rules, without authority to merge or to change other Tasks' pull requests;
   the manager publishes for Tasks without a lead.
   Superseding note, 2026-10-28: decision 19 replaces the earlier merge
   prohibition only for an assigned lead's own-Task t3-compose squash merge.
   Every t3-compose Task has a lead; the general no-lead path remains for
   managed repositories. The identity, privacy and head/author gates remain.
2. Merge authority. Shawn merges t3-compose pull requests, squash only. Agents
   never merge.

   Owner note, 2026-10-28: Shawn said, "this is not a standing rule. Once used with rapid-ship, there is a flight-mode for agents to auto approve and merge PRs".
   The record above states t3-compose's current rule. A managed repository's
   own rules decide who merges. Workers and Reviewers still never merge.

   Superseding note, 2026-10-28 (plan decision 19): Shawn said, "permission upgrade: for this project t3c ONLY, feature leads have permission to auto-merge PRs after they have reviewed. I.e., continue dev of this project fully unsupervised."
   On t3-compose only, every Task has an assigned Feature lead, and that lead
   squash-merges its own Task's pull request as the Worker App after its own
   exact-final-head review and passed required checks. Merge depends on the pull
   request being open and non-draft, clean and mergeable, at the exact reviewed
   head, with passed hosted checks if present, no `.github/workflows/` or
   out-of-owned-path change, and no open owner question. GitHub generates the
   squash commit title and message; agents supply neither. The Engineering
   manager does not merge, a Feature lead never merges another Task's pull
   request, and Workers and Reviewers still never merge. rapid-ship and all
   other repositories keep their own merge rules.
3. License. MIT, in [`LICENSE`](../../../LICENSE). Reused Compose or Firstmate
   code keeps its MIT attribution.
4. Ruleset. `main` has a GitHub ruleset: a pull request is required, force pushes
   and deletion are blocked, and there are no bypass actors, because every agent
   shares Shawn's `gh` login. The ruleset was created on 2026-10-27 and verified
   the same day through the GitHub API, which listed the ruleset and the three
   rules that apply to `main`. All three merge methods remain enabled in the
   repository settings; squash-only is a working rule, not a setting.

   Settings note, 2026-10-28: the manager read the repository settings through
   the GitHub API. Only squash merges are allowed, and GitHub deletes a pull
   request's branch when it merges. This records the current settings, not a
   new decision.

[t3-compose repository workflow](#t3-compose-repository-workflow) applies them.

## Decisions on the implementation plan

Shawn made these decisions while reviewing the implementation plan. Their
numbers match the plan's Decisions section.

- **Decision 1, 2026-10-27: floors and documentation.** Documents are medium. Technical documentation of a software project counts as software engineering. A floor applies to what a Task changes, not what it mentions.
  The first implementation of code that handles credentials or enforces a
  guardrail, and recovery or concurrency invariants, is challenging. Shared
  interface changes are medium. Exact-text mechanical edits can use the easy
  tier. Values remain configurable. This answers former open decision 4.
- **Decision 2, 2026-10-27: checkout updates.** After a merge, read the actual
  checkout HEAD. The manager may run `git pull --ff-only` on the owner's clean
  `main` only when T3 has not already fast-forwarded it. Read the provisioned
  commit again at dispatch.
- **Decision 5, 2026-10-27: Corrections and review tiers.** Corrections with
  enumerated findings, document reviews and scoped re-reviews use the medium
  tier. First reviews of challenging code follow the Task's tier. The config
  supplies the models. Decision 6 replaces only the Corrections tier.
- **Decision 6, 2026-10-28: Corrections at the easy tier.** Corrections rounds
  with enumerated findings use the easy tier, even when a floor set the first
  round's tier. Their scoped re-reviews stay medium; agy is never a Reviewer.
  The config's `roundTiers` holds the Worker tier for enumerated Corrections
  and the Reviewer tiers for scoped Corrections re-reviews and document
  reviews. Decisions 5 and 6 supply the initial values: easy, medium and medium.
- **Decision 7, 2026-10-28: squash trailers.** Shawn accepts the
  `Co-authored-by` trailers that GitHub generates on a squash commit when he
  merges. They may carry his commit email, which F7 found already public.
  Agents never write it. The privacy rule above covers a match in a squash
  commit that a branch update brings into a review range.

  Application note, 2026-10-28: decision 19 applies this same exception to
  GitHub-generated trailers when a lead merges its own t3-compose pull request.
  Agents still never write Shawn's email or supply the squash title or message.
- **Decision 9, 2026-10-28: medium Worker selection.** Shawn first said: "we must also support openrouter GLM5.3 as a medium worker choice (fallback behind sonnet and sol). the BYO harness choice with it is Pi."
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
  catalog's exact instance and model IDs; never guess an ID.
- **Decision 11, 2026-10-28: Jev model and call path.** Shawn said: "calling jev directly is correct." After the manager reported that the Decisions API rejects `typesafe/jev-router`, Shawn said: "~typesafe/jev-latest is the correct choice."
  The helper calls Jev directly through OpenRouter's Decisions API; no Harness
  or T3 provider runs Jev, and Shawn removes the Pi provider added for Jev.
  The config names `~typesafe/jev-latest`, an alias for the current Jev
  snapshot. Each classification records the configured model and the
  response's snapshot and ID. A new snapshot behind the alias does not by
  itself make a classification stale.
- **Decision 12, 2026-10-28: medium Worker pool selections and Argon.** Shawn said: "There's another model to support: gemini-4-argon-high as medium worker model (in agy subscription, only 2/3 accounts have it, slowly rolling out), same tier as GLM5.3 and Sonnet/Sol."
  Argon through the agy subscription is a supported medium Worker choice,
  alongside the choice in decision 9. It serves Workers only, never a Reviewer,
  Feature lead, text helper or the master. Shawn then chose: "Use Argon as the medium Worker preference, with Sol as its one fallback".
  This is the medium software-engineering preference, with exactly
  `"roles": ["worker"]`; medium Reviewers and Feature leads reach Sol through
  its one fallback. The Engineering manager applies the choice in private
  config. GLM remains unplaced. The subscription covers Argon; this adds no API
  exception. agy Workers carry instructions inline.
  The catalog observation on 2026-10-28 found the model on two of three pool
  accounts with `canRunChildTask: true`; this is rollout evidence, not quota
  evidence. Account identities are recorded in `.local/`.
  The Engineering manager's shared contract CD-1 permits medium Worker-only
  pool preferences and chain endpoints with exactly `"roles": ["worker"]`.
  Direct selections never name pool members. Account choice walks from the
  active account in pool order, wrapping around and skipping non-offering or
  confirmed exhausted accounts. The active-account exit-4 rule remains.
  Account walking is not a fallback; one fallback at most remains the rule.
  Quota observations stay per account for all its pool models. L11 verifies
  both supported choices after Shawn places each; it is not a handoff gate.
- **Decision 13, 2026-10-28: Feature lead final-head review, lead publication and manager delivery readiness.**
  On the design sentence "The manager runs a repository's heavy required check on the final head, or assigns one Session at a time to run it", Shawn first said: "feature lead should have enough context to review the final head, when there is a feature lead. Manager should be in charge of ensuring delivery success (operational readiness, integ test in place, etc)". When asked whether the lead's own review replaces a separate final-head Reviewer, Shawn answered: "Yes, the lead reviews the final head", selecting "When a Task has a lead, the lead's own review is the final-head review. No separate Reviewer reviews that head."
  When a Feature lead is assigned, it must hold the original requirements,
  relevant designs and interfaces, the final diff, callers and tests, and the
  check and review evidence needed to personally review the final head. Its own
  review is the final-head review, and no separate Reviewer is dispatched for
  that final head; earlier heads (Worker rounds and Corrections re-reviews)
  continue to use separate Reviewers, and Tasks without a Feature lead still
  require a separate Reviewer on the final head. Because a Feature lead makes no
  project edits, reviewing a Worker's final head does not approve its own
  implementation. The lead runs or assigns required checks (including the
  repository's heavy required check) on the exact final commit, maintains
  current evidence and finding dispositions, and publishes its Task's own pull
  request under the repository's identity and privacy gates (without authority
  to merge or to change other Tasks' pull requests); when there is no Feature
  lead, the manager performs that Task-level coordination and publishes the
  Task's pull request. Under the 2026-10-28 refinement in decision 17, a Feature
  lead tells Shawn in its own thread when the pull request is ready, puts the
  machine prerequisites and integration evidence in the pull request body, never
  reports pull request readiness to the Engineering manager, and sends no
  progress reports; it sends the manager one line when Shawn merges, plus
  blockers, cross-Task or shared-interface changes, new machine prerequisites,
  owner rule changes and agy exhaustion. Workers and Reviewers still never push, open pull
  requests, post or merge. Heavy checks still run one at a time on the whole
  machine with a stable lock, in a clean linked worktree at the exact commit
  under check after its writer has finished, never moving or writing the owner's
  checkout. T3 lifecycle ownership is unchanged: only the parent that created a
  child manages it, so a lead cannot manage a child created by the manager
  merely because the lead owns final-head verification.
  Under the Engineering manager's shared contract CD-4, every review is
  referenced by its `review` event's sequence number (replacing "review task
  reference"); `reviewer` is either `{"kind": "task", "taskId": "<T3 task>"}` or
  `{"kind": "lead", "thread": "<lead thread>"}` (never with a fabricated
  `taskId`); `evidence` (path and digest) is required for a lead review and
  optional for a task review; a manager-only `lead-assignment` event names the
  work's current lead (thread, assigned-by, date and reason; latest assignment
  wins, under the existing writer/transfer invariants); findings key on
  `(review reference, finding ID)` with every later same ID distinct; EV-1
  requires the final-head review to come from the current lead when the work has a lead and from an independent task review
  otherwise (a lead must be current both at review and verification); `check`
  sources include Feature lead while Worker-only claims remain insufficient; and `withdrawn` is by the review's author (the Reviewer
  or the lead). The Engineering manager retains overall delivery accountability:
  cross-feature integration, integration tests in place and their evidence on
  the intended integrated result, operational readiness, dependencies and risks
  resolved or surfaced, and final delivery readiness to Shawn. Under decision
  17's 2026-10-28 refinement, manager delivery checks happen at two points—at
  Task start it sets the expected integration tests and machine prerequisites,
  and after a merge it checks `main` and the machine—using the lead's verified
  Task evidence and pull request body without adding a manager pre-merge
  readiness approval or routinely repeating the same Task-level final-head
  review. Delivery readiness is not an EV-1 input, and Task
  verification and PR-ready remain distinct from integrated delivery readiness.
  No new deployment or merge authority or automatic readiness rule is
  introduced, and this documentation revision claims no integration or live
  pass.

  Superseding note, 2026-10-28: decision 19 grants only the assigned lead
  authority to squash-merge its own Task's t3-compose pull request as the Worker
  App after its exact-final-head review and passed required checks, subject to
  the repository workflow's merge conditions. Every t3-compose Task has a lead;
  the manager does not merge. The one-line manager notification is sent when
  the pull request merges. Managed repositories retain their own merge rules;
  the general optional-lead/no-lead design and all review and evidence duties
  remain. Decision 13's earlier merge prohibition is historical, not operative
  for this own-Task grant.
- **Decision 14, 2026-10-28: Python 3.15 runtime through uv's global pin.**
  Shawn first said: "use latest stable python instead of python 3.9"
  He corrected: "correction: uv python pin 3.15 --global". He then authorized:
  "Upgrade uv, install 3.15.2, remove alpha".
  Under the Engineering manager's shared contract CD-3, the helper runtime and
  offline test suite require CPython 3.15 or later, stable releases only
  (`sys.version_info.releaselevel == "final"`), selected through `uv`'s global
  pin (`uv python pin 3.15 --global` is Shawn's setup choice) and resolved as
  one interpreter with `PY="$(uv python find 3.15)"`. On 2026-10-28,
  Python.org's [official downloads listing](https://www.python.org/downloads/)
  lists stable CPython 3.15.2, released 2026-10-25
  ([Python 3.15.2](https://www.python.org/downloads/release/python-3152/)), after 3.15.0 on
  2026-10-09; the runtime floor is 3.15, not 3.15.2. Python 3.9 and the macOS
  system interpreter are no longer supported, and the dual-interpreter suite
  requirement ends. Standard-library-only execution and `-I -S` (`-I -S -B` for
  `SUITE`) remain required. `bin/t3c` exits 2 in its existing message form when
  invoked on an interpreter older than 3.15 or on a pre-release. Tests that
  start Python child processes keep using `sys.executable`. Moving to a newer
  stable minor is an explicit change (Shawn's pin plus a Task raising the floor
  and documented invocation), with no runtime network discovery or automatic
  upgrade policy. `uv python find 3.15` alone does not prove a final release; on
  2026-10-28 the manager upgraded `uv` to 0.13.6, installed final CPython
  3.15.2, removed the 3.15.0a7 alpha and confirmed that `uv python find 3.15`
  resolves to final 3.15.2. No runtime suite exists in this documentation-only
  head, so no helper test pass is claimed here. The pinned interim Worker App
  token tool in `.local/` stays unchanged outside the helper on its reviewed
  interpreter until retired after L5.
- **Decision 15, 2026-10-28: CLI status contract, shared helper modules and HS-1 size exception.**
  Shawn approved the HS-1 design-review package with "Approve" and wrote
  "Overflowing 1200 line is fine for this pr" for PR #4 (HS-1). The Engineering
  manager's shared contract CD-2 fixes the helper's structured nonzero outcomes,
  sanitized exit-2 handling for unexpected exceptions, `--status-file` on
  `t3c publish run` as the sole stdout/stderr pass-through command, JSON summary
  exits for `t3c heavy run`, reserved global option names and `--text-file` on
  `t3c privacy check`, contextual `--repo` documentation, `t3compose/process.py`
  and `tests/test_process.py` as the only new HS-1 shared files, the reusable
  `t3compose.cli.helper_info()` metadata API, explicit brief ownership for
  post-HS-1 edits to shared helper files and `MODULES`, and the PR #4 size
  exception while Stage 1 (`CF-1`, `RC-1`, `PV-1a`) waits for both PR #3 (DOC-1)
  and PR #4 (HS-1) to merge. The implementation plan records the detailed
  command and module contracts. The manager withdrew the separate follow-up
  docs Task; DOC-1 includes CD-2 section 6 now. No other size waiver is granted.
- **Decision 16, 2026-10-28: temporary prose setup for rapid-ship.**
  Shawn said: "we have the design down. While waiting for t3c impl, create a prose prompt for this manager/feature lead/worker setup with multi-model in t3 code, so we can temporarily unblock rapid-ship dev".
  While `t3c` is being implemented, Shawn temporarily unblocked rapid-ship
  development under a private prose setup run by a rapid-ship-bound manager
  thread; the private prompt's path, thread ID and contents stay in `.local/`
  and never appear in tracked files. That rapid-ship-bound prose manager first
  reconciles the old Claude work and dispatches nothing without Shawn's
  approval. t3-compose agents still do no rapid-ship work (they read and run
  nothing in any rapid-ship checkout), and the formal helper-backed pilot (L9)
  still waits for `t3c` and its existing handoff gates. No formal pilot pass or
  runtime implementation of the private prompt is claimed.
- **Decision 17, 2026-10-28: Feature lead reporting and manager delivery timing.**
  Shawn said: "btw, notice how each feature lead notifies you when a task is ready for me to merge? I can check that they have open PRs for me myself. In the future, don't let them notify you at this point, it's a waste of your context and tokens".
  A Feature lead never reports to its manager that a pull request is ready, and
  sends no progress reports; Shawn reads open pull requests himself, and a lead
  still tells Shawn in its own thread that the pull request is ready. A lead
  still sends one line to the manager when Shawn merges, and sends blockers,
  cross-Task or shared-interface changes, new machine prerequisites, owner rule
  changes and agy exhaustion, without turning that into a status-report
  channel. Manager delivery duty moves to two points: at Task start the manager
  sets expected integration tests and machine prerequisites; after a merge it
  checks `main` and the machine. The lead's pull request body carries the
  prerequisites and integration evidence. There is no extra manager pre-merge
  readiness approval or repeated final-head review; the manager still owns
  overall delivery, and delivery readiness remains separate from EV-1.

  Superseding note, 2026-10-28: under decision 19, the one-line notification is
  sent when the pull request merges, including a lead's authorized own-Task
  t3-compose merge. The lead proceeds without waiting for Shawn; no ready or
  progress report to the manager is added.
- **Decision 19, 2026-10-28: t3-compose Feature lead merge authority.**
  Shawn said: "permission upgrade: for this project t3c ONLY, feature leads have permission to auto-merge PRs after they have reviewed. I.e., continue dev of this project fully unsupervised."
  On t3-compose only, every Task has an assigned Feature lead, and that lead
  squash-merges its own Task's pull request as the Worker App after its own
  exact-final-head review and passed required checks. The Engineering manager
  does not merge, a Feature lead never merges another Task's pull request, and
  Workers and Reviewers still never merge. rapid-ship and all other repositories
  keep their own merge rules. Merge depends on the pull request being open and
  non-draft, clean and mergeable, at the exact reviewed head, with passed hosted
  checks if present, no `.github/workflows/` or out-of-owned-path change, and no
  open owner question. Final-head review, privacy, Worker App identity, exact
  head/author verification and review evidence remain required. GitHub generates
  the squash commit title and message; agents supply neither, and decision 7
  covers GitHub-generated co-author trailers on merges under this grant while
  agents still never write Shawn's email. No `t3c` merge command is authorized.
  Tasks and stages continue without waiting for Shawn: the lead verifies the
  merge and result and sends its one-line manager handoff when the pull request merges, and
  the manager checks `main` and the machine after each merge and starts
  dependent Tasks and leads once dependencies merge. `.github/workflows/`
  changes, routing values and model placement, the rapid-ship pilot (L9),
  product decisions and anything unclear remain Shawn's, with no grant to do
  rapid-ship work.

## Findings

The T3 manager thread that preceded the re-home gathered F1 to F7 on 2026-10-27.
The build manager observed F8 the same day. The evidence is recorded in `.local/`.

- **F1, T3 isolation semantics.** `delegate_task` with `isolation: "worktree"`
  takes the base from the parent thread's checkout HEAD (its worktree path, else
  the project root), with `startFromOrigin: false`, then runs
  `git worktree add -b <branch> <path> <base>` in the T3 project's workspace root;
  `shared` isolation starts a child in the manager's checkout. A thread's
  workspace cannot change after creation, and a probe from a thread bound to
  another repository failed in provisioning.
  - Status: read from T3's source on 2026-10-27; the failed probe matches it.
  - Changes: [Manager threads and bases](#manager-threads-and-bases), work cycle
    step 4 and the takeover exception; prompted re-home decision 1.
- **F2, the manager's checkout can advance after merge.** A root-bound manager
  uses the owner's checkout. Its local `main` can lag `origin`, but T3 may
  fast-forward it after a linked pull request merges. Read the actual HEAD
  after merge and again at dispatch; do not assume it lags.
  - Status: the build manager observed an automatic fetch and
    `pull --ff-only` within 19 s of a linked pull request merging, likely by T3.
    The evidence is recorded in `.local/`.
  - Changes: [Manager threads and bases](#manager-threads-and-bases) and work
    cycle step 2.
- **F3, the Jev contract.** A two-question call returned the contract recorded
  under [Jev decision routing](#jev-decision-routing), with `typesafe/jev-1.14`
  resolving to snapshot `typesafe/jev-1.14-20261014` at about 0.000024 USD.
  jev-1.14 classified a README spelling fix as domain OTHER (0.86), and
  OpenRouter's public model list omits decision models.
  - Status: verified live on 2026-10-27 with synthetic input. On 2026-10-28,
    `~typesafe/jev-latest` resolved to the same snapshot and the Decisions API
    rejected `typesafe/jev-router`; the evidence is recorded in `.local/`.
  - Changes: [Jev decision routing](#jev-decision-routing) and former open
    decision 4, answered by plan decision 1.
- **F4, rapid-ship's hook and the Session's repository.** rapid-ship's guardrail
  hook judges every command by the Session's working repository, so a Claude
  Session rooted in a rapid-ship checkout is denied `gh` writes and plain
  `git push` even when they target another repository. A t3-compose-rooted
  Session is not affected.
  - Status: read from rapid-ship's hook source on 2026-10-27.
  - Changes: [Checks and publication](#checks-and-publication) and
    [t3-compose repository workflow](#t3-compose-repository-workflow).
- **F5, unguarded Harnesses.** Codex and agy children run without rapid-ship's
  hook until its Codex and agy wiring merges. Every child inherits full access
  and the owner's local `gh` login, which is the owner's personal GitHub account.
  Plan decision 9 adds Pi Workers; treat them as unguarded until hook
  enforcement is verified.
  - Status: the Codex and agy claim comes from the dump and the previous T3
    manager's handoff, not re-run. Pi enforcement remains unverified under
    plan decision 9.
  - Changes: [Checks and publication](#checks-and-publication).
- **F6, machine load.** Two rapid-ship full checks run at the same time both
  failed, with a setup timeout followed by a process-group kill error.
  - Status: a claim from the previous manager's dump, not re-run.
  - Changes: [Checks and publication](#checks-and-publication).
- **F7, the owner's email.** The owner's commit email is already public in
  rapid-ship's history, so publishing t3-compose's history exposes nothing new.
  - Status: verified live on 2026-10-27 through the GitHub API.
  - Changes: supports re-home decision 4 and the note on commit metadata in
    [t3-compose repository workflow](#t3-compose-repository-workflow).
- **F8, T3 renames children's branches.** On 2026-10-27, T3 renamed the branch
  of every worktree-isolated child that the build manager checked, 12 s to 24 s
  after `git worktree add` and before the child's first commit. A rename can come
  after the child has moved its branch to its start commit; the branch keeps that
  commit.
  - Status: observed by the build manager in the branch reflogs; recorded in
    `.local/`.
  - Changes: [Sources of truth and local records](#sources-of-truth-and-local-records)
    and [Checks and publication](#checks-and-publication).

## Open decisions

No decisions remain open; [publication decisions](#decisions-on-t3-compose-publication) 1 to 3 answer former items 1 to 3, and [implementation-plan decision 1](#decisions-on-the-implementation-plan) answers former item 4.
