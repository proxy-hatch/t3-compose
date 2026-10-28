# t3-compose glossary

Terms for coordinating rapid-ship development. Task, Worker, Reviewer, Session,
Correction and Mode retain their meanings in rapid-ship's GLOSSARY.md.
t3-compose narrows Worker and Reviewer: they do not push, open pull requests, post
to GitHub or merge; an assigned Feature lead publishes its Task's own pull
request, and the Engineering manager publishes for Tasks without a lead.

## Language

**Engineering manager**:
The coordinator who communicates with Shawn and owns priorities across the project.
_Avoid_: master, controller

**Feature lead**:
A coordinator responsible for one feature within the Engineering manager's approved scope, including reviewing its Task's final head and publishing its Task's own pull request.

**Assignment**:
A bounded piece of work delegated to one agent under a brief and role.
A Task can require separate Assignments for implementation, review and Corrections.

**Verified Task**:
A Task whose final commit has passed its required checks and its final-head review.
That review is the Feature lead's own when the Task has a lead, and independent otherwise.
Verification does not mean that its pull request has merged.
