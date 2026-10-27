# t3-compose

Read the [design](docs/superpowers/specs/2026-10-27-t3-compose-design.md) before
planning or changing the package. Use the terms in [GLOSSARY.md](GLOSSARY.md).
The project contains documentation only; no runtime or test commands exist yet.

Keep this package separate from rapid-ship. Apply a managed repository's rules
when assigning work there. Do not copy its product code, backlog or guardrails
into this package.

Before delegation, read the owner's personal routing policy at
`/Users/shawn/workspace/t3-compose/.local/agent-routing.md`. Include the policy
in child briefs. Keep personal routing, account identifiers and runtime state
under Git-ignored `.local/`; source documents describe the configuration contract.
Because this repository is public, tracked files never hold the owner's email,
keys or tokens, the Jev key file's path, paths under the owner's configuration
directories, agy account or provider-instance names, T3 or Session IDs, or
rapid-ship's task list.

t3-compose is a public GitHub repository. Tasks land through pull requests after
independent review of the final head. The Engineering manager coordinates, checks
evidence and publishes. Workers edit project files and commit on their branch
only. Workers and Reviewers do not push, open, edit or merge pull requests, or
post to GitHub. Implementation and live pilots are pending.

Shawn decided how t3-compose is published on 2026-10-27; see the
[publication decisions](docs/superpowers/specs/2026-10-27-t3-compose-design.md#decisions-on-t3-compose-publication).
Only the Engineering manager publishes to t3-compose. It pushes and opens pull
requests as the rapid-ship Worker App, with a short-lived installation token
scoped to t3-compose, never under Shawn's account. The App lacks the workflows
permission (known from its use in rapid-ship), so Shawn pushes changes under
`.github/workflows/`. Shawn merges t3-compose pull requests, squash only; agents
never merge. On a managed repository, that repository's identity rules apply;
for rapid-ship, its Worker App commands.

These rules are instructional. A child can still push other branches with
Shawn's login. `main` accepts changes only through pull requests: a GitHub
ruleset requires one and blocks force pushes and deletion, with no bypass
actors. Someone holding Shawn's login could still edit the ruleset, so it guards
against mistakes, not deliberate bypass.

The owner's Claude Engineering manager context dump arrived and was verified on
2026-10-27. Shawn then decided that rapid-ship work waits for the pilot: do not
resume, review, check or publish any rapid-ship task, and do not choose the pilot
task, until t3-compose is built and the pilot starts in the rapid-ship-bound
manager thread. t3-compose design, planning and implementation continue.
