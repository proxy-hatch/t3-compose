# t3-compose

Read the [design](docs/superpowers/specs/2026-10-27-t3-compose-design.md) before
planning or changing the package. Use the terms in [GLOSSARY.md](GLOSSARY.md).
Resolve the stable CPython 3.15 interpreter selected by uv's global pin and run
`SUITE` from the repository root:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S -B -m unittest discover -s tests -t . -v
```

The [CLI spec](docs/cli.md) and [testing spec](docs/testing.md) describe the
helper and its test harness.
Every test class derives from the offline guard in `tests/support.py` and is
named `Offline…`, or `Simulated…` when it injects quota responses. Only the
Engineering manager runs `live/` checks; offline and simulated tests do not
count as live passes.
Tests that start another program must allow it through the guard.
For the supported launch interfaces (`subprocess.Popen`, `os.posix_spawn` and
`os.posix_spawnp`), the guard accepts Python only through its bootstrap.
Tests must use that form for Python children; other process-creation mechanisms
are outside the guard's guarantee (see the testing spec).

Keep this package separate from rapid-ship. Apply a managed repository's rules
when assigning work there. Do not copy its product code, backlog or guardrails
into this package.

Before delegation, read the owner's personal routing policy at
`/Users/shawn/workspace/t3-compose/.local/agent-routing.md`. Include the policy
in child briefs. Keep personal routing, account identifiers and runtime state
under Git-ignored `.local/`; source documents describe the configuration contract.
Because this repository is public, tracked files, commit messages and GitHub
posts, such as pull request titles, bodies and comments, never hold the owner's
email, keys or tokens, the Jev key file's path, paths under the owner's
configuration directories, agy account or provider-instance names, T3 or Session
IDs, or rapid-ship's task list. The one exception is the `Co-authored-by`
trailer that GitHub generates on a squash commit when Shawn merges: it may
carry his commit email ([plan decision 7](docs/superpowers/plans/2026-10-27-t3-compose-implementation.md#decisions)),
and agents never write it. Before it publishes, the coordinator checks
the commit messages and the pull request's title and body against the private
identifier list in `.local/`.

t3-compose is a public GitHub repository. Tasks land through pull requests after
review and required check evidence on the final head. When a Task has a Feature
lead, the lead's own review is the final-head review, with no separate Reviewer
on that head, and the lead owns final-head review coordination and check
evidence. Earlier-head Worker and Corrections reviews use independent Reviewers;
a Task without a Feature lead also uses an independent Reviewer on its final
head. The Engineering manager owns priorities across Tasks, shared interfaces,
and delivery, operational and integration readiness. Workers edit project files
and commit on their branch only. Workers and Reviewers do not push, open, edit
or merge pull requests, or post to GitHub. Implementation and live pilots are
pending.

Shawn decided how t3-compose is published on 2026-10-27; see the
[publication decisions](docs/superpowers/specs/2026-10-27-t3-compose-design.md#decisions-on-t3-compose-publication).
Only the Engineering manager publishes to t3-compose. It pushes and opens pull
requests as the rapid-ship Worker App, with a short-lived installation token
scoped to t3-compose, never under Shawn's account. It confirms that GitHub
records the App as each pull request's author. The App lacks the workflows
permission (known from its use in rapid-ship), so Shawn pushes changes under
`.github/workflows/`. Shawn merges t3-compose pull requests today, squash only.
That is not a standing rule: a managed repository's own rules decide who
merges, and rapid-ship's flight mode lets agents approve and merge pull
requests. On a managed repository, that repository's identity rules apply;
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
