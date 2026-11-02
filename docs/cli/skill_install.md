# Manager skill installation

SK-1 adds two commands that link and inspect the manager skill. Use the
[shared CLI contract](../cli.md) for invocation, global options, output, errors
and registration. Imports and registration perform no I/O.

```text
t3c skill install --skills-dir DIR [--name NAME]
t3c skill status --skills-dir DIR [--name NAME]
```

## What they act on

- The skill is the `skill/` directory of the helper checkout. It holds
  `SKILL.md` and the brief templates. Its installed form is one symlink,
  `DIR/NAME`, whose stored target is the absolute path of `<root>/skill`.
- `NAME` defaults to `t3-compose-manager`, the name in the skill's front
  matter. It matches `[a-z0-9][a-z0-9-]{0,63}`.
- `--skills-dir` is required: the manager Harness's user skill directory. It
  must already be a directory (a link to one is fine) and must not lie inside
  the skill directory. The commands never create it.
- Neither command reads `.local/`, starts a process or uses the network. The
  installed skill is the checkout itself, so there is nothing to copy and no
  update command: updating the checkout updates the skill, and `t3c version`
  shows which commit runs.

## install

`install` creates the symlink and writes nothing else. It writes outside
`.local/`, so the Engineering manager runs it only at Shawn's explicit request
(live step L6):

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <root>/bin/t3c skill install --skills-dir <skills-directory>
"$PY" -I -S <root>/bin/t3c skill status --skills-dir <skills-directory>
```

Then start a new manager Session and check that it lists the skill.

- Nothing at `DIR/NAME`: create the link. Exit 0.
- A link that already resolves to this checkout's `skill/`: no write. Exit 0.
- Anything else at `DIR/NAME` (a file, a directory, a link to another target,
  a dangling link): refuse and change nothing. Exit 1. There is no `--force`;
  remove the old entry yourself, then run `install` again.
- The checkout's `skill/SKILL.md` is missing, `NAME` or `--skills-dir` is
  invalid, or the link cannot be created: exit 2.

Success prints one JSON object with `changed` (`true` when it created the link).

## status

`status` reads `DIR/NAME` without following a final symlink and prints one JSON
object with exactly these fields:

| Field | Value |
|---|---|
| `name`, `path` | The skill name and the full link path. |
| `state` | `installed`, `missing`, `not-a-link`, `foreign-link` or `broken-link`. |
| `target` | The link's stored target, or `null` when `DIR/NAME` is not a link. |
| `expectedTarget` | This checkout's `skill/` path. |
| `skillFile` | Whether `DIR/NAME/SKILL.md` is a file. |

`installed` means the link resolves to this checkout's `skill/`. The command
exits 0 only when `state` is `installed` and `skillFile` is `true`. Any other
result keeps the same data on stdout and exits 1.

## Exits

| Code | Meaning |
|---|---|
| 0 | `install` created or confirmed the link; `status` found a good install. |
| 1 | `install` refused because the name is in use; `status` found no good install (data on stdout). |
| 2 | Invalid `NAME` or `--skills-dir`, an incomplete `skill/`, or a link that cannot be read or created. Stdout is empty and stderr holds a public message. |

Messages never echo a supplied value or a path. The commands append no work
record and read no configuration. `SKILL.md` names every registered command; an
offline test checks that it stays complete.
