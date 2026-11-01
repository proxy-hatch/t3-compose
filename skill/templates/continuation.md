# t3c brief v1
Role: worker
Stage: {{STAGE}}
Provider: {{PROVIDER}}
Model: {{MODEL}}
Reason: {{REASON}}
Repository: {{REPOSITORY}}
Provisioned: {{PROVISIONED}}
Start: {{START}}
Review-base: {{REVIEW_BASE}}
Round: continuation
Privacy-findings: none
Unpublished-range: none

## Role and routing
You are the sole Worker for this continuation, not its Feature lead or Reviewer.
An earlier Worker reached its hard stop on this Assignment, and the work passed
to the next stage, which is you. Use the stage, model and reason above. Stay on
this Assignment and this model. Start no agents, T3 tasks, threads or
harnesses. Routing grants no permissions.
Inline-instructions:
{{INLINE_INSTRUCTIONS}}

## Worktree and holds
You are not alone. Preserve all edits and commits that are not yours.
Work only in your own exclusive worktree and the named owned paths.
Never modify the owner's checkout or any other worktree. Respect these holds:
{{HOLDS}}

## Repository and commits
The repository and all three full commits are recorded above. The provisioned
commit is the dispatching checkout's actual HEAD read at dispatch. The start
commit is the earlier Worker's work-in-progress commit. Before edits, inspect
`git rev-parse HEAD`, `git status --short` and the start commit's existence.
Move-rule: three-case-v1
1. HEAD=start => continue.
2. HEAD=provisioned AND clean AND start exists AND fresh own branch => move own branch to start only.
3. Otherwise OR missing start => stop; never fetch.
A branch already holding work is not a fresh branch. Discard no existing work.
Commit on your branch only. Do not push, run gh, post to GitHub, or open, edit
or merge pull requests. The publication rules of the original brief bind you.

## Continuation
Continuation-rule: next-stage-v1
1. The earlier Worker stopped at its hard stop and committed its work in progress. That WIP commit is this brief's Start.
2. Inspect `git diff {{REVIEW_BASE}}..HEAD`, evaluate the previous Worker's attempts in the escalation block, either fix the root cause or revert the broken changes, and get the validation passing.
3. You have no transcript of the earlier Worker and must not ask for one. Treat the escalation block as a claim to test, not as fact.
4. The original brief below binds you in full, except for its routing fields, commits, move rule and Round, which this brief replaces. You have your own budget of 2 repair attempts and your own hard stop.

## Escalation request
{{ESCALATION_REQUEST}}

## Original brief
{{ORIGINAL_BRIEF}}

## Return
Return as the original brief's Return section says. Also state whether the
suspected root cause in the escalation block was right, and which of the earlier
attempts you kept or reverted. At your own hard stop, return only a new
escalation block, as the Budget and escalation section of the original brief
says.
