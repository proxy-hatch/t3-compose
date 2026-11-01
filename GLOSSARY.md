# t3-compose glossary

Terms for coordinating rapid-ship development. Task, Worker, Reviewer, Session,
Correction and Mode retain their meanings in rapid-ship's GLOSSARY.md.
t3-compose narrows Worker and Reviewer: they do not push, open or edit pull
requests, post to GitHub or merge. An assigned Feature lead publishes its Task's own pull
request; in t3-compose, every Task has a lead, who also squash-merges its own
Task's pull request as the Worker App after final-head review and required
checks. In managed repositories, the manager publishes for Tasks without a lead,
and merge authority follows that repository's rules.

## Language

**Engineering manager**:
The coordinator who communicates with Shawn and owns priorities across the project.
_Avoid_: master, controller

**Feature lead**:
A coordinator responsible for one feature within the Engineering manager's approved scope, including reviewing its Task's final head, publishing its Task's own pull request, and in t3-compose merging its Task's own pull request.

**Assignment**:
A bounded piece of work delegated to one agent under a brief and role.
A Task can require separate Assignments for implementation, review and Corrections.

**Verified Task**:
A Task whose final commit has passed its required checks and its final-head review.
A core Task's final-head review is the review of record's at the Task's reviewer stage; a non-critical Task's is its lead's own; a Task without a lead gets an independent Reviewer.
Verification does not mean that its pull request has merged.

**Lane**:
The routing matrix's worker axis: backend-logic or ui-frontend. A `MIXED` classification maps to backend-logic.

**Pre-pass**:
The stage-1 core review's first pass. It has no fallback, is skipped when refused or unavailable, and is never the review of record.

**Quota fallback**:
The ordered list in a matrix cell, walked once when a target refuses for quota. Not a capability escalation.

**Refusal memory**:
The 30-minute record of quota refusals, keyed by provider instance and model, or by pool account and model.

**Review of record**:
The reviewer whose verdict clears a core Task's final head, selected from the core reviewer row at the Task's reviewer stage.

**Role block**:
A driver kind unavailable for a role because its capability probe failed, with recorded reason and evidence.

**Stage**:
The capability level of a dispatch: 1, 2 or 3. Standard scope starts at stage 1; complex scope at stage 2. Escalation is vertical, to the next stage, per issue.
