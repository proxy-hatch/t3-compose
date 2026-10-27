# t3-compose

A small T3 Code coordination package for an Engineering manager, optional Feature
leads, Workers and independent Reviewers. The first managed project is rapid-ship.

Shawn approved the design on 2026-10-27. A revision made the same day, after the
re-home findings, awaits Shawn's approval. Implementation and live pilots are pending.

- [Design and accepted decisions](docs/superpowers/specs/2026-10-27-t3-compose-design.md)
- [Glossary](GLOSSARY.md)
- [Agent instructions](AGENTS.md)

Personal model preferences, availability fallbacks, Jev settings and account
rotation live in `.local/agent-routing.json`. Operating instructions live beside
it in `.local/agent-routing.md`; observed quota state is separate. Git ignores
the entire `.local/` directory. These files are instructions and proposed helper
configuration; T3 does not automatically load or enforce them.

The next step, after Shawn approves the revision, is an implementation plan.
rapid-ship work waits for the pilot, which starts in a manager thread bound to
the rapid-ship root once t3-compose is built.
