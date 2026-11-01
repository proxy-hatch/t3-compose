# Reviewed-head push

PB-1a adds `t3c publish push --repo-path P --work ID --head SHA --branch NAME`.
Use stable CPython 3.15 through uv with `-I -S`, as in the
[CLI contract](../cli.md). Global `--local-dir` and `--text` precede `publish`.
This command publishes the configured repository as its App. The assigned
Feature lead runs it for its Task; Workers never publish. No merge command is
provided. PB-1b will add PR creation, reuse, verification and the exact-head
push/PR pair. This command alone does not establish PR readiness.

## Gate and destination

The helper's tracked files must be clean. Untracked files do not block publication.
`cli.helper_info()` supplies the helper commit on operation events.
Inputs require a lowercase full SHA, record work ID, Git
branch spelling, repository root with that exact HEAD, and one origin HTTPS
URL equal to the configured GitHub repository (optional `.git` suffix).
The strict App configuration loader runs before any external publication.
Invalid identity, paths or refs fail closed without echoing supplied values.

The command calls [evidence.check](evidence.md) with `pr_ready=False`, unchanged.
Current exact-head checks, criteria, implementation and CD-4 review provenance
must pass. A Worker claim cannot replace them. For core final heads, the
publishing lead applies the current independent-review policy and records its
lead check under the current evidence convention before invoking publication.

Read-only Git runs through `process` with a controlled PATH, temporary HOME,
no inherited credentials, replacement objects, lazy fetch or user configuration.
Its own inspection settings reset credential helpers, refuse SSH and disable
redirects and hooks. The preflight reuses [GA-3 refusals](token_run.md) on the
same sanitized base as `publish run`, so stored credentials and SSH cannot
perform writes. A private temporary HOME configuration gives the push child
`http.followRedirects=false`, `push.followTags=false`,
`push.recurseSubmodules=no` and `core.hooksPath=/dev/null`. The configuration
that child will see (system, that file, the repository and every include, read
without the inspection settings) may hold no `http.*followRedirects`, `push.*`,
`core.hooksPath` or `protocol.*` entry other than those exact values. Any other
entry exits 2, naming no value, until the coordinator removes it.

All advertised refs, including tags and HEAD, exclude reachable objects from
the publication set. Every advertised object must already exist locally;
shallow or incomplete history, malformed remote facts and read errors stop.
The command never fetches or writes tracking refs, so fetch beforehand, outside
it: an advertised object this checkout lacks stops the push. A bounded adapter
composes PV-1 object readers and the existing identifier-list grep matcher to
screen exactly the selected commit messages, full trees, paths, UTF-8 blob
contents and branch. PV-1 byte bounds, unsupported-entry refusal and `.lock`
screening exception apply. Changes to workflows in selected commits stop with
exit 5: Shawn must push them. Privacy stops name safe next actions without
matched text: Corrections for unpublished history, coordinator branch
correction, or report to Shawn for published history. Rewriting published
history is never authorized.

## Intent, lease and result

After screening, the current branch must be absent, equal the requested head,
or equal the latest confirmed push head for that same URL and full branch ref.
Updates require that prior head to be an ancestor of the reviewed head. Other
movement stops. A durable `op-intent` records the expected prior SHA or literal
`absent`; the branch is queried again before any child. Equality produces a
confirmed `no-op` without child fields. Other cases run in-process from P:

```text
git push --force-with-lease=refs/heads/NAME:EXPECTED https://github.com/OWNER/REPO.git SHA:refs/heads/NAME
```

EXPECTED is empty for absence. The explicit lease protects the observed value
and never authorizes a non-fast-forward update. No `-C`, `-c`, shell, implicit
lease, bare force, branch-derived source or personal-login write is used.
The cwd and environment are restored on every exit. Child descriptors and
Python diagnostics are contained; output never becomes startup evidence.

The retained mode-0600 status file is private, under the work record's
`publication-status` directory, named by the unique operation ID. It is unused
before launch. `token_run.run_command` owns token lifecycle and CD-2 writes.
Actual success requires a valid returned `RunResult`, agreement with durable
status, child started with status zero, and exact remote-head confirmation.
Refusal, nonzero, malformed or missing status never count as success. Failed
revocation retains a confirmed child-zero success, truthfully records
`revocation:failed` and adds a warning to the returned reason (CD-2's stderr
warning, which containment discards). Results join their own intent;
writer/effective work are loaded fresh through RC-1, without a second success
store.

Before any new push, an unresolved attempt is reconciled from the latest
same-target intent, retained status and remote. Completed child-zero status plus
current equality can resolve an interrupted result without another push.
Missing status resolves as no-start; true/null and malformed status remain
unknown, even if the remote equals HEAD, until the coordinator resolves the
retained uncertainty. Another branch, an older intent, or a late result cannot
resolve the current attempt. Record-write failure leaves uncertainty; preserve
files and reconcile before retrying.
See the [CD-6 consumer contract](evidence.md#pr-ready-convention-for-pb-1).

## API, exits and live procedure

`publish.push(args)` takes `local_dir`, `repo_path`, `work`, `head`, `branch` and
returns `Outcome({outcome, reason}, code)`. Outcome is `succeeded`, `no-op`,
`refused`, `failed` or `unknown`. Exit 0 means confirmed success/no-op; 1 means
blocked or unconfirmed; 5 means Shawn must push workflows. Invalid input, Git
reads or persistence raise sanitized `CommandError` (2); invalid App config
uses 3. No secret, backend stream or private path appears in the result.
Imports and registration perform no I/O. There are no new dependencies.

After PB-1a merges, the assigned PB-1b lead prepares valid exact-head records,
required checks and review evidence, then makes the first live push:

```sh
PY="$(uv python find 3.15)"
"$PY" -I -S <helper>/bin/t3c --local-dir <private-state> publish push --repo-path <checkout> --work <work> --head <reviewed-sha> --branch <branch>
```

Check exit 0, exact remote equality and the joined intent/result/status. If this
live push fails, report to the Engineering manager and use the interim tool.
After PB-1b merges, L5 publishes the next Task with BOTH push and PR commands,
using then-current evidence APIs. Only after L5 does the interim tool retire.
These are planned live procedures; offline tests supply no live-pass claim.
