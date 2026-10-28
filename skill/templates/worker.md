# t3c brief v1
Role: worker
Tier: {{TIER}}
Provider: {{PROVIDER}}
Model: {{MODEL}}
Reason: {{REASON}}
Repository: {{REPOSITORY}}
Provisioned: {{PROVISIONED}}
Start: {{START}}
Review-base: {{REVIEW_BASE}}
Round: {{ROUND}}
Privacy-findings: {{PRIVACY_FINDINGS}}
Unpublished-range: {{UNPUBLISHED_RANGE}}

## Role and routing
You are the sole Worker for this Assignment, not its Feature lead or Reviewer.
Use the tier, model and reason above. Stay on this Assignment and this model.
Start no agents, T3 tasks, threads or harnesses. Routing grants no permissions.
Inline-instructions:
{{INLINE_INSTRUCTIONS}}

## Worktree and holds
You are not alone. Preserve all edits and commits that are not yours.
Work only in your own exclusive worktree and the named owned paths.
Never modify the owner's checkout or any other worktree. Respect these holds:
{{HOLDS}}

## Repository and commits
The repository and all three full commits are recorded above. The provisioned
commit is the dispatching checkout's actual HEAD read at dispatch, not an old
record or an assumption that the checkout lags origin. Before edits, inspect
`git rev-parse HEAD`, `git status --short` and the start commit's existence.
Move-rule: three-case-v1
1. HEAD=start => continue.
2. HEAD=provisioned AND clean AND start exists AND fresh own branch => move own branch to start only.
3. Otherwise OR missing start => stop; never fetch.
A branch already holding work is not a fresh branch. Discard no existing work.
Commit on your branch only. Do not push, run gh, post to GitHub, or open, edit
or merge pull requests. The assigned lead publishes its own Task's PR;
otherwise the manager publishes. You cannot act as either publisher.
On t3-compose only, the assigned lead squash-merges its own Task's PR as the
Worker App after exact-final-head review and passed required checks, under
plan decision 19 and the repository's merge conditions. The manager never
merges t3-compose PRs. Other repositories retain their own merge rules.

## Read first
{{READ_FIRST}}
Routing policy (private instructions; never copy identifiers into source):
{{ROUTING_POLICY}}
Repository settings check, if one exists:
{{SETTINGS_CHECK}}
For agy and Pi, applicable rules and required outside content must be supplied
in the inline instructions above. agy's file tool cannot read outside its
worktree; an inaccessible pointer cannot replace required inline content.

## Owned paths
{{OWNED_PATHS}}
Change no other project files. Surface shared-interface or scope changes.

## Objective
{{OBJECTIVE}}

## Interfaces
{{INTERFACES}}

## Acceptance
{{ACCEPTANCE}}

## Test first
{{TEST_FIRST}}
Write meaningful failing tests, verify the expected failure, implement, then
verify green. Preserve the repository's offline guard for Python children.

## Validation
Fast checks:
{{FAST_CHECKS}}
Assigned heavy checks only, through `t3c heavy run`:
{{HEAVY_CHECKS}}
For a public repository, check changed tree text and commit messages against
the private identifier list before returning; report file/line only, never
matched text. Keep keys, tokens and personal runtime identifiers out of source.
Resolve one final CPython 3.15 interpreter from the repository root:
`PY="$(uv python find 3.15)"`. Confirm 3.15.x and releaselevel `final`; reject
pre-release evidence. Use `"$PY" -I -S <root>/bin/t3c` for helper commands and
`"$PY" -I -S -B -m unittest discover -s tests -t . -v` for the full suite.
Python children use `sys.executable` and the repository's guard bootstrap.

## Commits
Use a conventional subject and a body explaining why. Include this accurate
agent attribution trailer; never write the owner's email:
{{ATTRIBUTION}}
{{PRIVACY_CORRECTIONS}}
## Return
Return the current branch (`git branch --show-current`, since T3 can rename
it), absolute worktree path, final head, parent and commit log. Map each
acceptance criterion to evidence. Give every command with its result and log
path. State questions, deviations with reasons, unresolved decisions and what
was not done. Confirm the scope and publication restrictions were respected.
Worker evidence is a claim for coordinator verification, not review approval.
