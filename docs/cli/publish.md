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
it: an advertised object this checkout lacks stops the push. The publisher calls
`privacy.scan_commits` with that exact ordered publication set, the work's
immutable review-base/head endpoints and the intended branch. The endpoints do
not replace the push selection with a review range. The shared strict scanner
screens selected commit messages, full trees, paths, UTF-8 blob contents and
branch, including intermediate and merge-side history. Its SHA-1-only,
complete-history, byte bounds, unsupported-entry and binary refusals and `.lock`
screening exception apply. There is no publisher policy option or policy verdict
consumption. Raw findings refuse publication.

An independent `diff-tree --root -m` walk checks workflow transitions for every
selected commit against every parent, including roots. It does not exclude
`.lock` paths. Changes to workflows in selected commits stop with
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

After exact-head evidence passes, `privacy.scan_commits` screens the entire
immutable work review-base-to-head range and intended branch, with ordered
labeled texts `[("title", captured_title), ("body", captured_body)]`. PR screening
uses that review range even when push's advertised exclusions make its set
empty. The independent workflow walk also applies to this range. This includes already
published commits after Corrections. A stale review/check stops before intent;
a published privacy match requires a report to Shawn. Only unpublished history
may receive authorized Corrections. The coordinator fixes its own text/branch.
The title is captured once and the body is copied into a private mode-0600
snapshot inside a mode-0700 directory. Creation uses only those captured bytes;
a persistent change to either original text file during preparation refuses
before token mint rather than silently replacing the payload. Snapshots are
removed on return.

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


## Final strict write gate

New writes run a second strict gate after earlier preparation: intent persistence,
push lease reread or PR discovery, status-path creation and PR body snapshot
creation. Existing recovery and verified no-op paths retain their reconciliation
rules and do not mint a token.

The gate checks watched configuration, reruns the shared strict scan and independent
workflow walk on the captured selection and texts, checks current exact-head
evidence, then reloads the work. Original work, effective work and the complete
event prefix must agree; only this operation's new intent may have been added.
This catches changes to requirements, review or assignment even if replacement
evidence could otherwise pass. Push rereads all advertised refs and compares the
complete map and ordered publication set, preserving the original target lease.
PR re-enumerates its immutable review range. Both compare the intended head and
branch; PR rereads title/body and compares base and captured text bytes. Actual
checkout HEAD is read at the end, before entering the token runner.

Changed inputs refuse without launching a child or inventing a status summary.
Read errors remain safe invalid-input failures; retained intent/status support
reconciliation. The token runner retains its own configuration preconditions,
App identity, token lifecycle and revocation rules. These are sequential reads,
not an atomic transaction across inspection, record writes, mint and publication;
concurrent changes after the relevant final observation are outside this guarantee.
Policy integration remains a separate follow-up; this foundation grants no policy
permission and does not change `publish run`.

## Dormant canonical verification helpers

Two private helpers provide a directly tested foundation for later integration.
Neither has a production caller or changes the strict gates above. There is no
publisher policy flag, permission or saved-report input. Storage is a separate
dormant helper below.

`_policy_verify_command(local, repo, base, head, branch, policy, *, text_files=())`
returns this exact token array, never a shell command:

```text
["t3c", "--local-dir", LOCAL, "privacy", "verify", "--repo-path", REPO,
 "--range", BASE + ".." + HEAD, "--branch", BRANCH, "--privacy-policy", POLICY]
```

Paths accept strings or `Path` objects and become absolute lexical normalized
paths, without opening files, resolving symlinks or requiring existence. Endpoints
are full lowercase SHA-1 values. Branches must have valid lexical Git branch
syntax. Path and branch strings are nonempty UTF-8 without ASCII controls, each
at most 4096 bytes; encoded command JSON is at most 16384 bytes. Text inputs are
an empty tuple/list or precisely ordered pairs `title`, `body`; the PR array adds
`["--text-file", "title=" + TITLE, "--text-file", "body=" + BODY]`.
Malformed inputs raise sanitized `CommandError` (2).

`_policy_verify_check(local, identity, work, head, command)` accepts only the
canonical list, loads current validated records itself and returns a Boolean.
It binds local directory, current effective review base and requested head,
requires an exact current required-check command and applicable trusted pass
with concrete evidence, and also requires the complete evidence comparison.
Malformed inputs or unavailable records raise sanitized `CommandError` (2).
The future caller must construct the expected command from its actual captured
repository, branch, policy and ordered text paths; record labels cannot supply
those invocation arguments. No detached bundle, check or report is an input.

This is cooperative provenance, not authentication. Review-range verification
cannot attest the actual remote-excluded push set. The dormant projection below
adds no activation. Actual selection evaluation, fresh policy/list/authority
observations and final write gates remain mandatory before policy publication.

## Dormant private observation projection

`_policy_observation(result, *, operation, repository, base, head, commits,
branch, texts, check_command, identifiers, list_observed_at)` returns a fresh
closed dictionary. It performs no I/O. The input must be the current in-process
`privacy.PolicyEvaluation` with `policy_satisfied is True`; a saved summary or
truthy object refuses. Callers supply their captured canonical repository,
immutable endpoints, ordered selection, intended branch, decoded ordered
`title`/`body` texts for `pr` (none for `push`), verification token array and
fresh `prepare_policy_identifiers` result. These arguments are trusted caller
context, not independently recovered publication authority.

Artifact v1 binds coverage context and all four fresh identifier fields through
provenance; repository/base come from coverage, not its provenance mapping.
Public-only v2 binds selection/context/list/count and the public policy descriptor.
Combined v2 additionally binds artifact provenance and its pinned policy file
name/digest pair. The two policy versions have separate descriptors.

The private `publication-policy-observation/v1` result has exactly these keys:
`version`, `operation`, `base`, `head`, `selectionDigest`, `contextDigest`,
`checkCommandDigest`, `listDigest`, `patternCount`, `policyBindingDigest`,
`artifactAuthorityStartDigest`, `artifactAuthorityEndDigest`,
`publicAuthorityStartDigest`, `publicAuthorityEndDigest`, `rawStatus`, `rawExit`,
`commitCount`, `findingCount`, `approvedUnscannedCount`, `policySatisfied`,
`observedStart`, `observedEnd`, `listObservedAt`. Absent authorities are null.
Digests are SHA-256; commit IDs are full SHA-1. Counts are exact nonnegative
integers bounded by 2^31-1; booleans are never counts. Canonical encoded output
is at most 16384 UTF-8 bytes. Malformed input raises constant `CommandError(2)`.

Both authority groups use six-field start/end observations: event count,
checkpoint (sequence/digest), work digest, events digest and nested UTC start/end
timestamps. Each digest includes all six validated fields. `policyBindingDigest`
is the stable comparison seam: it binds explicit policy/source/approval/list/work
and exact captured context, excluding volatile authority counts, checkpoints,
event digests and observation times. Allowed unrelated authority appends can
change observation hashes without changing that binding. Timestamps are bounded
aware UTC ISO strings; each start is no later than its end, and the list observation
is no earlier than evaluation end. These sequential observations prove neither
prefix continuity nor atomic revocation.

Only typed selected fields are hashed/exported. Findings, verdicts, proofs,
quotes and whole facts/coverage are never serialized or hashed. Treat private
digests as secret-equivalent. The constructible Python type is cooperative
provenance, not authentication; saved observations grant no permission.
There is no CLI option, production caller or policy activation. Complete fresh
evaluator/current list/current authority/pre-mint integration remains mandatory.


## Dormant immutable observation writer

`_write_policy_observation(local, identity, work, op_id, observation) -> Path`
is private and has no production caller. `_encode_policy_observation` independently
validates the exact flat 23-field projection output and encodes canonical UTF-8
JSON before any filesystem creation, with a separate 16384-byte cap. It accepts
no evaluation facts, saved report, proof or permission. Exact scalar types,
SHA-1 endpoints, SHA-256 digests, paired nullable authorities, bounded counts,
raw status/exit/count relations and ordered aware UTC times are required.
Findings require public authority; approved-unscanned counts require artifact
authority. At least one authority pair is present. All producer v1, public-only
v2 and combined output forms are supported. The closed fixed-size schema normally
encodes below 2 KiB; the byte cap is an independent encoding guard.

The canonical repository identity and existing work select
`state/records/<identity>/work/<work>/publication-policy/<op_id>.json` under local.
Work and operation components start with an ASCII alphanumeric, contain only
ASCII alphanumerics, dot, underscore or hyphen, and are at most 128 characters.
The selected local root, existing record ancestors, work and dedicated parent
must be owned directories with mode 0700. Descriptor-pinned traversal rejects
symlinks below the trusted selected root; unsafe existing modes are never repaired.
Each opened directory is registered for cleanup before traversal continues and
remains pinned until stack cleanup. Root-acquisition failure unwinds registered
cleanup; a close error is never retried by the writer. Close uncertainty is
not an atomic or hostile-same-user guarantee.
Only the dedicated parent may be created. New final files use descriptor-relative
exclusive nofollow creation and mode 0600; existing files, links and other entries
are never overwritten or reused. Regular-file ownership, single link, size,
readback and pinned name/inode identities are checked before success.

The writer loops over short writes, fsyncs the file, reads back exact bytes,
closes the file and fsyncs the containing directory before returning its private
path. The dedicated parent entry is synced through its work directory even when
already present, because an earlier creator may have failed that sync. Partial bytes
can be visible while writing. Failures attempt to unlink only the created inode
and sync cleanup; failed cleanup can retain partial or complete bytes. A directory
close error after completion retains the complete file and still reports failure.
Any retained file blocks reuse. All failures raise constant `CommandError` (2):
`publication: invalid private policy observation storage`. Paths and payloads are
never diagnostics or public output. There is no atomic revocation, authentication
or hostile-same-user guarantee. Later C1b integration must compose current actual
evaluation, list/authority reads, check evidence and final publication gates;
stored observations never act as a cached pass, receipt or token permission.

## Dormant current-policy context seal

`_seal_policy_context(result, *, repo, local, policy, operation, repository,
base, head, commits, branch, texts, check_command)` is a private dormant seam.
It accepts an actual explicit satisfied evaluator result and the trusted caller's
exact context. It returns the existing 23-field minimal observation, never a
publication permission. No command, flag, production caller or writer activation
uses it. Strict publication behavior is unchanged.

It captures validated patterns, independently rereads identifiers and compares
all four identifier fields, then uses the existing public evidence loader and
locale digest. Public bindings must exactly equal the actual result's bindings.
The optional artifact loader validates every artifact binding against actual
coverage provenance and the trusted repository/base, including current authority
and the public policy's artifact pin. A final identifier read must still equal
the captured patterns and first snapshot. The existing projection validates the
exact selection, metadata, result shape and current list. Refusals use only
`publication: invalid current policy context` (exit 2).

The seal performs no scan, evaluator, literal proof, cache or persistence. Reads
retain the existing loaders' bounds. Public authority is current at its loader's
read; artifact authority is current at its later loader read; the last list read
precedes projection. Evaluation and authority timestamps remain those of the
actual evaluation; only `listObservedAt` describes the seal's fresh list read.
This cooperative sequence gives no atomic revocation or changes-made-and-undone
protection. Future activation must seal **both** contexts after **all** proofwork,
and seal both again after persistence, preserving every final publication gate.
Sealing one context does not seal another. Full activation remains separate.

## Dormant policy lifecycle composition

Private `_prepare_policy_gate(args, repo, identity, loaded, selected, branch, *,
operation, target, refs=None, texts=(), text_files=())` returns `(state, refusal)`.
`operation` is `push` or `pr`; target is the canonical HTTPS push ref or PR
collection for that branch. Push supplies the complete captured advertised refs;
PR supplies ordered title/body texts and their labeled files. This interface is
inert: no installed command calls it, no policy flag is registered, and current
publication remains strict.

Missing optional Namespace policy attributes mean `None`. Absent policy returns
`(None, None)`, meaning strict mode, never permission. A separate CHECK policy
requires a publication policy and is push-only. Omission selects the publication
policy independently for CHECK. Each capture keeps original spelling, lexical
absolute path and bounded nofollow raw-byte digest, even for equal inputs.
PR file captures additionally bind ordered labels and exact text. Preparation
validates current records, repository identity/origin/root/HEAD and selections,
then joins actual canonical current check evidence. It evaluates CHECK range
without texts followed by the remote-excluded push selection, or the single PR
range with metadata. Initial seals retain stable projections in private state.
There is no result deduplication, saved verdict input or observation cache.

`_finalize_policy_gate(state, *, intent)` consumes the preparation once. The
supplied current real UUID intent must be the sole addition to the captured work
stream, with matching operation, target, head and expected prior ref. It joins
current canonical check evidence, finishes fresh CHECK then publication proof
work, checks captures, seals BOTH contexts and compares stable policy/list,
context/selection/command and endpoint bindings. Volatile authority observations
remain distinct; seal times never replace actual evaluation/proof times.

The unchanged writer stores publication under actual `opId` FIRST and push
CHECK under `opId-review` SECOND, including identical policy inputs. PR writes
one file. Both files use the unchanged closed 23-field, 16 KiB schema. Both
contexts are resealed after storage, with independent capture checks and a final
canonical check join. The suffix relates storage to the SAME intent/result; it
creates no second operation or event.

Explicit unsatisfied policy or missing current canonical check returns a
`refused` Outcome, exit 1. Invalid inputs, result types/non-boolean satisfaction,
changed bindings/captures, seals, intent or storage raise constant
`publication: invalid policy lifecycle`, exit 2. Successful finalization returns
`None`; it grants no token permission. Neither helper appends events, invents
child facts or handles public status. Failed/partial/complete files remain under
the writer's existing retention rules. Reusing a consumed preparation fails;
fresh preparation and an actual new intent are required for retry. Existing
files refuse without overwrite and never become cached authority.

The state has a private-safe repr but remains cooperative process-private state,
not authentication. Current public authority is observed inside `_text_policy`,
artifact authority in the later artifact loader and identifiers at the seal's
last list read. Both seals after all proofs and both reseals after writes catch
persistent changes before those reads. Later changes, including during another
context's seal, remain possible; this is neither atomicity nor detection of
changes made and undone between reads. A future active caller still owes ALL C0
workflow/configuration/evidence/work-prefix/ref/selection/lease/metadata and
HEAD-last gates, honest refusal/unknown result handling, and full integration
acceptance. Direct lifecycle tests do not establish successful public publishing.
