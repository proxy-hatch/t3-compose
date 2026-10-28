# t3-compose design

Status: design decisions approved by Shawn on 2026-10-27. This revision, made on
2026-10-28, records Shawn's [re-home](#decisions-accepted-at-the-re-home),
[publication](#decisions-on-t3-compose-publication) and
[implementation-plan](#decisions-on-the-implementation-plan) decisions, and the
[findings](#findings). It takes effect when Shawn approves and merges its pull
request. No decisions remain [open](#open-decisions).
Implementation and live validation are pending.

Terminology: [project glossary](../../../GLOSSARY.md). A Task is one pull request;
an Assignment is one delegated piece of work, such as its implementation or review.

## Purpose and acceptance

Give Shawn one engineering manager in T3 Code that continues rapid-ship's ongoing
development, delivers features, and refines designs. Preserve or improve the
quality of the existing Claude-managed work while assigning bounded implementation
to cheaper Workers. These goals and the quality criterion are owner requirements.

Quality is evaluated against the applicable repository rules, approved designs,
required checks, independent review of the final commit, and resolved review
findings. A cheaper model never reduces those requirements. One successful pilot
can demonstrate the workflow, but cannot establish general quality equivalence
between models. The owner judges the pilot against the existing standard.

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
results and explains decisions. It may write task records and briefs. Project
code, tests, configuration and documentation edits go to Workers, including small
fixes and integration conflicts. Reading diffs and running checks remain allowed.

Workers own one brief and its named paths. They preserve other work, follow the
project's test-first rules, report evidence and surface decisions outside the
approved scope. They do not broaden their assignment or claim review authority.
They commit on their branch only; the manager publishes (see
[Checks and publication](#checks-and-publication)).

Reviewers are separate Claude or Codex agents. They assess specification
compliance, repository standards, correctness and test adequacy against an exact
base and head. They report findings without editing the reviewed tree. agy is
never a Reviewer.

Include an optional feature-lead role in version one. Use direct children for
bounded independent tasks. Enable real feature leads only after the nested pilot.
The package does not impose a new agent-count cap; the owner, runtime capacity,
dependencies and exclusive file ownership determine safe parallelism.

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
continues through direct delegation. One lead level is enough; leads do not create
other leads.

The manager owns cross-feature priorities, shared interfaces, owner decisions,
the global assignment record and final readiness reporting. A feature lead owns
one approved feature brief, decomposition within that scope, its Worker/Reviewer
dispatches and local integration coordination. It reports outcomes, blockers,
changed interfaces and evidence to the manager. It delegates project edits and
cannot approve its own implementation. Its software-engineering coordination
route uses the configured selection at the Task's tier, at least medium;
challenging engineering decisions use the challenging tier.

Only the parent that created a T3 child manages that child's task lifecycle. Thus
the lead manages its Workers; the master manages the lead. Leads own their feature's
work records and return child task references and evidence; the manager alone owns
the global assignment index. These are linked records, not competing copies of a
global state store. A parent
completion wake is not assumed while a lead still has live children; important
owner questions and cross-feature blockers must use T3's supported pending-question
mechanism or a verified upward-report path. Native nested delegation, escalation
delivery and recovery must pass a scratch pilot before real feature leads are enabled.
On lead failure, reconcile its thread, child statuses and artifacts before replacing
it. An existing child is never duplicated because a new lead cannot find its state.

The manager's no-project-edit rule is instructional in version one. Existing
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

Use one agy account until confirmed quota exhaustion before moving to the next.
Resolve the old attempt before retrying. Preserve its work and supply a continuation
brief. Tool failures and correctness failures are not quota evidence. Repeated
correctness failures escalate to a medium-tier Worker. An unavailable master is
reported to the owner; the package does not replace the Engineering manager
without Shawn's explicit choice. For Workers, Reviewers and Feature leads, apply
the configured availability fallback at the selected tier. The config names
each source and target selection and its options; each chain has exactly one
target. Re-home decision 3 added reverse chains. Any selection, whether a
preference or a chain target, may carry an optional `roles` list: a non-empty
subset of `availabilityFallback.roles`; absent means all of them. A role the
selection does not serve treats it as unavailable and may use that selection's
one fallback, once. Check the target's tier, role eligibility and live
availability. If it is unavailable too, or no chain applies, report to Shawn
(exit 5); never fall back twice or follow the target's own chain. Plan decision 9
supports GLM 5.3 as a medium Worker selection; Shawn chooses its place in config.
Record and report the reason, preserve all quality requirements, and resolve
the original attempt before retrying. Availability fallback grants no extra
permissions.

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
- Review task ID, reviewed base/head, findings, responses and review verdict.
- External operation intent and confirmed result for each push and pull request,
  and for other operations where the repository requires it.
- Owner decisions and next action, with an explicit unknown value where unresolved.

A record identifies a Worker's result by its worktree path and result commit, not
by a branch name, because T3 can rename a child's branch after provisioning (F8).
Read a branch name from the worktree when one is needed.

Each record has exactly one coordinator writer. For direct tasks that is the
manager; for lead-owned tasks it is the assigned feature lead. The lead persists
intent in its own feature record before dispatch, without waiting for a manager
round trip. The manager owns the global index and reads linked feature records
for readiness decisions; it does not rewrite a live lead's record. Workers return
their evidence through T3. Ownership transfer requires reconciling the old writer
and its children first; uncertain liveness blocks that transfer. The nested pilot
must test interrupted record writes and recovery without duplicate dispatches.
Keep runtime records out of tracked source and out of the managed project,
under t3-compose's Git-ignored `.local/state/`, keyed by repository identity. Existing account policy/state files
remain canonical; the new record refers to them rather than copying their contents.

Use a small Python standard-library helper that handles the bounded
Jev request, writes records atomically, validates required fields and checks whether
evidence covers the current commit. It launches no agents and polls nothing. The exact storage format
and command names are builder choices. Preserve historical attempts and reviews.
Missing or invalid records block the affected acceptance decision, not unrelated work.

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
   no other manager or Worker owns the work being assigned.
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
5. On completion, inspect the returned artifacts and commit. A completed T3 turn
   does not prove the brief is complete. Reconcile uncertain dispatches before
   launching another writer; use T3 status/history rather than blind retries.
6. Commission independent review of the exact review base and head. Every review
   round gets a new T3 delegated task containing the brief, prior findings,
   responses and unresolved objections. Never use the child's backing thread to
   start that round.
7. Send Corrections to a Worker with a brief whose start commit is the previous
   head; the review base does not change. Any new commit invalidates prior final
   acceptance until its required checks and review are current. The manager never
   marks an unresolved finding resolved merely because the Worker disagrees. For a
   factual dispute, obtain one second independent review of the original finding
   and evidence. Unresolved acceptance-blocking disputes and product decisions go
   to Shawn.
8. Mark work verified only when all acceptance criteria have evidence, required
   checks passed on the current head and independent review cleared it. Keep
   verified, PR-ready and merged distinct. Integration and publishing follow the
   repository's existing authority and identity rules, and the manager publishes
   (see [Checks and publication](#checks-and-publication)). The package grants no
   new authority.

T3 notifications wake the manager. There are no parallel watcher processes or
polling loops. An interrupted manager reconstructs decisions from the local record
and reconciles actual T3/Git state before continuing.

## Checks and publication

Workers run the fast checks that the brief names. The manager runs a repository's
heavy required check on the final head, or assigns one Session at a time to run
it. The check runs in a worktree whose HEAD is the commit under check and where no
writer is active, normally the Worker's worktree after its turn ends; it never
moves or writes the owner's checkout. Heavy checks run one at a time on the whole
machine, across all manager threads (F6); how managers coordinate this is left to
the implementation plan. A check on another commit does not cover the head. For
the heavy check, this refines step 1 of the
[accepted review policy](#accepted-review-policy); required checks still cover the
final head.

Workers commit on their branch only. Workers and Reviewers do not push, open, edit
or merge pull requests, or post to GitHub. The manager records each publication
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
unpublished commits needed to remove it. The manager fixes its own branch name,
pull request title and body. Shawn decides when an identifier seems necessary.

These rules are instructional. Every child inherits full access and the owner's
local `gh` login, and Codex and agy children run without rapid-ship's hook until
its Codex and agy wiring merges (F5). Plan decision 9 adds Pi Workers; their
hook enforcement is unverified, so treat them as unguarded too. A child can
still push wherever that login can push; the package creates no security
boundary. For t3-compose's `main` ruleset, see [t3-compose repository workflow](#t3-compose-repository-workflow).

## t3-compose repository workflow

t3-compose is the public GitHub repository `proxy-hatch/t3-compose`, created on
2026-10-27 (re-home decision 4), under the MIT License (publication decision 3).
Its build manager publishes from its t3-compose-rooted Session, because a
rapid-ship-rooted Claude Session cannot push to it (F4). Tasks land through pull
requests after independent review of the final head. Workers commit on their
branch only. `AGENTS.md` describes this workflow and, once the helper exists, its
test command.

On t3-compose, only the manager publishes, as the rapid-ship Worker App and never
under Shawn's account (publication decision 1). On a managed repository, that
repository's identity rules apply instead, as
[Checks and publication](#checks-and-publication) describes. To publish to
t3-compose, the manager mints a short-lived installation token for the Worker
App, scoped to t3-compose, and runs `git push` and `gh` with the token only in
their environment. The App's ID, installation account and Keychain item are
personal configuration in `.local/`. An interim token step in `.local/` serves
until the implementation plan decides where the helper provides it. The token
step clears Git's other credential helpers and refuses SSH transport for the
push, so a push cannot fall back to Shawn's stored credentials or SSH key.

When the manager opens a pull request, it names the repository, head branch and
base branch explicitly. Before it reports a Task PR-ready, it also confirms that
GitHub records the Worker App as the pull request's author. If this check fails,
or the pull request's head commit differs from the reviewed head, the manager
reports the failure to Shawn and does not report the Task PR-ready.

The Worker App lacks the workflows permission, so it cannot push changes under
`.github/workflows/`; Shawn pushes those changes. This is known from the same
App's use in rapid-ship and not yet observed on t3-compose.

Shawn merges t3-compose pull requests today, squash only (publication decision
2). A managed repository's own rules decide who merges; rapid-ship's flight
mode lets agents approve and merge pull requests. Workers and Reviewers still
never merge. On 2026-10-28, the manager read the repository settings
through the GitHub API: only squash merges are allowed, and GitHub deletes a
pull request's branch when it merges. `main` has a GitHub ruleset (publication
decision 4): a pull request is required, force
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
publishes, the manager checks the commit messages and the pull request's title
and body against the private identifier list in `.local/`. Commit author and
committer fields do carry the owner's email, which F7 found already public.
On 2026-10-28, Shawn accepted the `Co-authored-by` trailers that GitHub generates
on a squash commit when he merges (plan decision 7). They may carry his commit
email; agents never write it. These trailers are the only exception for
commit-message text. They sit on the base branch, normally
outside the review ranges that privacy checks cover. A match in a squash commit
that a branch update brings into a range goes to Shawn.

## Design refinement

Approved designs are the starting point. A new finding can justify a revision;
record the evidence and the affected decision. Commission difficult software design
work to the configured challenging software-engineering selection, and difficult
non-engineering design work to the configured challenging non-engineering selection.
Use an independent reviewer to check the proposed change.
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
t3-compose design, planning and implementation continue.

At pilot start, reconcile the recorded state with live Git and T3 state before
taking over any work: the old Claude manager's workflow journals, worktrees,
branches, commits, dirty files, PRs, review heads and unanswered decisions.
Quota-blocked or quiet does not prove all its children have stopped. Establish
that old dispatch has ceased and resolve active writers before T3 takes ownership.

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

The pilot runs from the rapid-ship-bound manager thread once t3-compose is built
(re-home decisions 1 and 2). Starting it there is a first manager context
restart: that thread reconstructs state from the records. It then reconciles
existing work, as described under
[Taking over existing Claude work](#taking-over-existing-claude-work), and chooses
one bounded unfinished task whose requirements are approved and whose files have
no active writer. Choose the Worker tier from its risk; do not force a sensitive
merge-guard change onto agy.

The first live pilot must demonstrate:

- A real T3 writer on the intended branch/base, followed by a separate reviewer.
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
configured threshold; no pre-pilot accuracy study is required. Until the pilot
starts, pilot the router with public or synthetic examples. Once the pilot starts
and lifts the rapid-ship hold, router pilots may also use real task inputs,
recorded with each decision. Label synthetic evaluation explicitly.
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
pilots remain to be built. Moving the project does not authorize takeover of
rapid-ship work.

## Accepted review policy

Shawn approved choice 1 on 2026-10-27: independently review every Task.
A Task is the work of one pull request, as defined in rapid-ship's GLOSSARY.md.

1. The Worker implements the Task and runs the required checks.
2. A separate Reviewer reads the diff and relevant callers and tests against the
   approved brief and repository standards.
3. The Worker makes required Corrections. Each finding receives an explicit disposition.
4. The Reviewer checks the final head commit after Corrections. Required checks
   must also cover that commit.
5. The manager checks the review result and check evidence, then reports readiness.
   It investigates unresolved findings and decisions without duplicating every review.

For who runs a repository's heavy required check, see
[Checks and publication](#checks-and-publication).

Use the configurable engineering model preferences for the Reviewer. Document
reviews and scoped Corrections re-reviews use the configured round tiers,
initially medium. First reviews of challenging code follow the Task's tier,
including escalations; other first reviews are at least medium. agy is never
a Reviewer. Review independence requires a separate Session that did not implement
the change; it does not require a different model vendor.

There is no initial quick-scan stage. A quick scan cannot replace independent
review. Merge authority and all repository acceptance requirements remain unchanged.

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

Storage format and helper command names remain builder choices. Runtime capability
and recovery behavior require implementation tests. Takeover of rapid-ship work
now waits for the pilot (re-home decision 2).

## Decisions accepted at the re-home

Shawn made these decisions on 2026-10-27, in the Session that re-homed this work
to manager threads bound to each repository's checkout:

1. Re-home. Build t3-compose from a manager thread bound to the t3-compose root.
   Run the pilot from a manager thread bound to the rapid-ship root.
2. rapid-ship work waits for the pilot. Do not resume, review, check or publish
   any rapid-ship task, and do not choose the pilot task, until t3-compose is
   built and the pilot starts in the rapid-ship-bound manager thread.
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
2. Merge authority. Shawn merges t3-compose pull requests, squash only. Agents
   never merge.

   Owner note, 2026-10-28: Shawn said, "this is not a standing rule. Once used with rapid-ship, there is a flight-mode for agents to auto approve and merge PRs".
   The record above states t3-compose's current rule. A managed repository's
   own rules decide who merges. Workers and Reviewers still never merge.
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
