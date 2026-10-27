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
Tracked files never hold the owner's email, keys or tokens, the Jev key file's
path, agy account or provider-instance names, or T3 or session IDs.

t3-compose is a public GitHub repository. Tasks land through pull requests after
independent review of the final head. The Engineering manager coordinates, checks
evidence and publishes. Workers edit project files and commit on their branch
only; they do not push, open, edit or merge pull requests, or post to GitHub.
These rules are instructional; a child can still push. Implementation and live
pilots are pending.

The publication identity and merge authority are
[open decisions](docs/superpowers/specs/2026-10-27-t3-compose-design.md#open-decisions).
Until Shawn decides the identity, nothing agent-authored is pushed. Until Shawn
confirms merge authority, Shawn merges, squash only, and agents do not merge.

The owner's Claude Engineering manager context dump arrived and was verified on
2026-10-27. Shawn then decided that rapid-ship work waits for the pilot: do not
resume, review, check or publish any rapid-ship task, and do not choose the pilot
task, until t3-compose is built and the pilot starts in the rapid-ship-bound
manager thread. t3-compose design, planning and implementation continue.
