# t3-compose

A small T3 Code coordination package for an Engineering manager, optional Feature
leads, Workers and independent Reviewers. The first managed project is rapid-ship.

The design is approved. Implementation and live pilots are pending.

- [Design and accepted decisions](docs/superpowers/specs/2026-10-27-t3-compose-design.md)
- [Glossary](GLOSSARY.md)
- [Agent instructions](AGENTS.md)

Personal model preferences, availability fallbacks, Jev settings and account
rotation live in `.local/agent-routing.json`. Operating instructions live beside
it in `.local/agent-routing.md`; observed quota state is separate. Git ignores
the entire `.local/` directory. These files are instructions and proposed helper
configuration; T3 does not automatically load or enforce them.

The next step is an implementation plan for the approved design. rapid-ship
takeover and its first development pilot still wait for the owner's Claude
Engineering manager context dump.
