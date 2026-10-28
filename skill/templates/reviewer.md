# t3c brief v1
Role: reviewer
Tier: {{TIER}}
Provider: {{PROVIDER}}
Model: {{MODEL}}
Reason: {{REASON}}
Repository: {{REPOSITORY}}
Provisioned: {{PROVISIONED}}
Start: {{START}}
Review-base: {{REVIEW_BASE}}
Review-head: {{REVIEW_HEAD}}
Round: {{ROUND}}
Privacy-findings: none
Unpublished-range: none

## Independence
You are an independent Reviewer in a separate Session. You did not implement
this change. Edit, commit, push and post nothing. Do not run gh or open, edit
or merge pull requests. Start no agents, T3 tasks, threads or harnesses.
Review only in your own worktree; preserve all other work and owner checkouts.
This template serves earlier-head reviews, Corrections re-reviews and final
reviews of Tasks without a lead. A led Task's current lead personally reviews
its exact final head; no duplicate Reviewer is dispatched for that head.

## Role and routing
Use the recorded model, tier and reason. Reviewer tier is at least medium;
first reviews of challenging code use challenging. Document reviews and scoped
Corrections re-reviews are medium. agy and Pi are Worker-only providers here;
Reviewers use Claude or Codex. Stay on this Assignment and assigned model.

## Repository and commits
The provisioned, start and review-base commits support checkout consistency.
The exact review range is Review-base..Review-head above; never substitute a
moving branch. Inspect HEAD, cleanliness and start existence before proceeding.
Move-rule: three-case-v1
1. HEAD=start => continue.
2. HEAD=provisioned AND clean AND start exists AND fresh own branch => move own branch to start only.
3. Otherwise OR missing start => stop; never fetch.
The move rule describes provisioning consistency, not permission for a
Reviewer to reset or edit. If a move is needed, stop and ask the coordinator
to provision the correct start. Read reviewed artifacts by exact commit with
`git diff <review-base>..<review-head>` and `git show <review-head>:<path>`
in your own worktree. Stop if required objects are missing; never fetch.
Publication belongs to the assigned lead for its own Task, otherwise the
manager; never to this child. On t3-compose only, decision 19 permits the
assigned lead's own-Task squash merge as the Worker App after its exact-head
review and passed checks, subject to repository merge conditions. This grants
no general agent merge authority; all other repositories keep their own rules.

## Read first
{{READ_FIRST}}
{{ROUTING_POLICY}}
{{SETTINGS_CHECK}}
Read the original brief, approved requirements, designs, interfaces, full diff,
callers and tests. Include all prior findings, responses and unresolved
objections in this fresh review round:
{{CONTEXT}}

## Review questions
{{QUESTIONS}}
Check specification compliance, repository standards, correctness, test
adequacy, privacy, owned-path scope and evidence currency on the exact head.
Keep earlier finding IDs and dispositions traceable to their original round.

## Checks
{{CHECKS}}
Run only assigned checks and preserve the tree. For checkout-based checks,
require HEAD=Review-head; otherwise report the check as not run and ask the
coordinator for a correctly provisioned worktree. Use `t3c heavy run` for an
assigned heavy check. Resolve `PY="$(uv python find 3.15)"`; confirm final
CPython 3.15.x, then use `"$PY" -I -S <root>/bin/t3c` and
`"$PY" -I -S -B -m unittest discover -s tests -t . -v` as applicable.
Reject pre-release evidence. Python children use `sys.executable` with the
offline guard bootstrap. Report unavailable or unrun checks explicitly.

## Findings format
For each finding give a stable ID, severity (blocking, important or minor),
`file:line` at the reviewed head, problem, evidence and suggested fix.
Separate verified facts from judgment. Cite reproducible evidence; do not
expose private matches, keys or tokens. Return findings most severe first.

## Verdict
Return exactly one verdict: approve, approve after fixes, or changes required.
Explain unresolved acceptance gaps. Any subsequent commit needs current checks
and review; this verdict does not grant publication or merge authority.

## Return
Give exact base/head, branch, worktree, findings, answers to all questions,
acceptance evidence, every command/result/log, unresolved objections, deviations
and what was not done. State that you edited, committed, pushed and posted
nothing. Return the assessment to your assigning coordinator.
