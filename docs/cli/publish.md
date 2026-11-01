# Reviewed-head publication

PB-1a adds `t3c publish push --repo-path P --work ID --head SHA --branch NAME`.
Use stable CPython 3.15 through uv with `-I -S`, as in the
[CLI contract](../cli.md). Global `--local-dir` and `--text` precede `publish`.
This command publishes the configured repository as its App. The assigned
Feature lead runs it for its Task; Workers never publish. No merge command is
provided. `publish pr` adds PR creation, reuse, verification and the exact-head
push/PR pair. The push command alone does not establish PR readiness.

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

PB-1b uses the interim publication tool because the helper on main cannot
mint with private HOME until this fix merges. After PB-1b merges, L5 is the
next assigned pull request: its lead performs the first live helper push AND
PR creation, with exact-head checks and independent review recorded first and
the bounded current lead check last under the interim evidence convention.
Verify exit 0, remote equality, App author/base/head/open state, joined durable
intent/result/status and `evidence check --pr-ready`. Then link the PR and
follow the repository merge procedure. Only after L5 does the interim tool
retire. Offline tests supply no live-pass claim.

## Pull request command

```text
t3c --local-dir PATH publish pr --work ID --head SHA --branch NAME --base main --title-file F --body-file F
```

Run from the target repository root. There is no repository-path option for
this command. The helper cleanliness, exact HEAD, configured HTTPS origin,
App loader and Git configuration gates above apply unchanged. `--base` must
be `main`. Both text files must be regular bounded UTF-8 text without NUL,
at most 1 MiB each. The title additionally must be nonblank, single-line,
without ASCII controls or DEL, and at most 256 UTF-8 bytes. File contents and
backend errors are never printed. Relative text paths refer to the starting
repository root. Pass an explicit absolute global local directory.

After exact-head evidence passes, PV-1 screens the entire immutable work
review-base-to-head range, branch, title and body. This includes already
published commits after Corrections. A stale review/check stops before intent;
a published privacy match requires a report to Shawn. Only unpublished history
may receive authorized Corrections. The coordinator fixes its own text/branch.
The title is captured once and the body is copied into a private mode-0600
snapshot inside a mode-0700 directory; creation uses those screened bytes even
if the original files change. Snapshots are removed on return.

A durable `pull-request` intent precedes discovery or creation. Discovery uses
a bounded read through `process.run`:

```text
gh api --hostname github.com --method GET repos/OWNER/REPO/pulls?state=all&head=OWNER%3ANAME&per_page=2
```

Query components are URL-encoded. At most one result is accepted; two rows,
incomplete or malformed responses, mismatched repository/branch, failed reads
or invalid PR numbers preserve uncertainty and never authorize creation.
Closed PRs are discovered too, preventing an accidental duplicate. A single
existing PR is reused only after verification passes.

Creation calls `token_run.run_command` in-process from the repository with an
unused retained status path, under the private publication environment:

```text
gh pr create --repo OWNER/REPO --head NAME --base main --title=TITLE --body-file SNAPSHOT
```

`--title-file` is a helper input, not a gh flag. Only the scoped App token may
perform this write; no implicit push, shell or alternate process path is used.
Child output and descriptors are contained and restored as for push. Neither
output nor exit code proves startup or success. The actual `RunResult` must
agree with retained CD-2 status, including child started and status zero, for
creation to succeed. Revocation failure is retained and reported in the reason.

Discovery and [PR verification](pullrequest.md) use the caller's existing
read-only GitHub authentication with pinned host and executable PATH, captured
responses and no token mint. This narrow reader context restores the private
publication environment before any write. Verification calls the existing API
with exact head, base and configured `config.bot_login`; only its complete
passing dictionary counts. A second bounded GET through the same reader
context must explicitly report Boolean `draft: false` and independently match
that exact head, base, open state and author. Draft, missing or malformed draft
facts cannot clear creation, reuse or recovery. Exceptions, skipped calls,
malformed returns and mismatches record failure and a safe verification gap.
The temporary publication HOME therefore cannot hide the reader's authentication.

After creation, discovery supplies the PR number independently of child output.
Successful creation or verified reuse returns exactly `{outcome, reason, number,
url}`. `number` is a validated positive 32-bit integer; `url` is constructed as
`https://github.com/OWNER/REPO/pull/N`, never copied from external text. Other
results contain exactly `{outcome, reason}`. Outcomes and exits follow push:
0 for `succeeded`/`no-op`, 1 for `failed`/`refused`/`unknown`, 2 for invalid
input or persistence, 3 for invalid App configuration. Verification failure's
reason is `PR verification failed; report to Shawn; not PR-ready`. Discovery
uncertainty asks for repository-state/status reconciliation before retry.

Interrupted operations reconcile only their latest matching operation, target,
commit and intent. Missing status means no start; true/null or malformed status
remains unknown even if a PR exists. Completed retained child-zero status plus
fresh passing verification can resolve interruption without a second creation.
Other branches, late results and reopened older uncertainty cannot resolve a
newer attempt. Persistence failures leave intent/status available for recovery.
Reuse records `no-op` without invented child evidence.

The real [PR-ready comparison](evidence.md#pr-ready-convention-for-pb-1) consumes
both exact-head push and PR results. Failed/skipped verification, wrong heads,
nonzero children, refusals and uncertainty cannot supply that pair. This helper
performs no automatic T3 linking, watching, posting or merging. After both
commands pass, the lead separately links the canonical PR URL and applies the
repository's merge procedure. L5 verifies the pair with `evidence check
--pr-ready`; only that live procedure retires the interim tool.

## Preparing advertised objects

The coordinator must prepare a complete local object graph before using push.
Every advertised object remains an exclusion, including HEAD, branch tips,
pull head/merge refs, annotated tag objects and peeled tag targets. A missing
object refuses before intent; the helper never fetches or skips exclusions.
Outside this helper, from the target repository root, the coordinator can fetch
one bounded local snapshot namespace from the configured explicit HTTPS URL:

```sh
git fetch --no-tags --no-recurse-submodules https://github.com/OWNER/REPO.git '+refs/*:refs/t3c-publication-snapshot/*'
```

Replace OWNER/REPO with the configured repository. This obtains advertised
refs/* (including refs/pull/* and tag objects) without changing origin
configuration. Use a fresh snapshot namespace or clean up stale snapshot refs
under coordinator control. HEAD's object must also be available locally; for
an unusual symbolic HEAD outside refs/*, prepare its advertised object
separately. Re-run preparation if refs move: a newly advertised ref can race
the snapshot, and push still refuses any unavailable object. Complete history
is required; this command alone does not repair a shallow repository.

A legitimate negative ancestry check is exit 1/refused with a
non-fast-forward reason; malformed inspection or read failures remain exit 2.
Watched settings (including includes) are checked again before calling the
in-process token runner. A refusal records no launch and invents no child
status. This narrows the configuration window; arbitrary concurrent local
mutation remains outside the guarantee.
Refused child results show only a validated closed-vocabulary refusal kind
and a coordinator repair action, never supplied summary text or backend
diagnostics. Unknown outcomes still require reconciliation; failures stay
failed. Durable operation/status truth is unchanged.
