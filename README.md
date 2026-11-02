# t3-compose

A small T3 Code coordination package for an Engineering manager, optional Feature
leads, Workers and independent Reviewers. The first managed project is rapid-ship.

Shawn approved the design on 2026-10-27. The `t3c` helper and the manager skill
(`skill/SKILL.md`) are built. Installing the skill and the live pilots wait for
Shawn's request.

- [Design and accepted decisions](docs/superpowers/specs/2026-10-27-t3-compose-design.md)
- [Implementation plan](docs/superpowers/plans/2026-10-27-t3-compose-implementation.md)
- [Glossary](GLOSSARY.md)
- [Agent instructions](AGENTS.md)

Personal model preferences, availability fallbacks, Jev settings and account
rotation live in `.local/agent-routing.json`. Operating instructions live beside
it in `.local/agent-routing.md`; observed quota state is separate. Git ignores
the entire `.local/` directory. These files are instructions and proposed helper
configuration; T3 does not automatically load or enforce them.

rapid-ship work waits for the pilot, which starts in a manager thread bound to
the rapid-ship root once t3-compose is built.

## Installation, updates and status

The manager skill is [`skill/SKILL.md`](skill/SKILL.md). It installs at user level
as a symlink to this checkout's `skill/` directory, so the checkout is the
installed version. Installing writes outside `.local/`; run it only when Shawn
asks ([spec](docs/cli/skill_install.md)):

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c skill install --skills-dir <skills-directory>
"$PY" -I -S <root>/bin/t3c skill status --skills-dir <skills-directory>
```

To update, read the checkout's HEAD after a merge. Run `git pull --ff-only` only
on a clean `main` that T3 has not already fast-forwarded. A dirty, divergent or
non-`main` checkout goes to Shawn. `t3c version` shows which commit runs.

t3-compose is released under the [MIT License](LICENSE).
