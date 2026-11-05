# Publication privacy

`t3c privacy check` screens publishable text in any repository against the
private identifier list. It is a read-only check, not permission to publish.
It implements the [workflow's privacy rule](../superpowers/specs/2026-10-27-t3-compose-design.md#checks-and-publication).
`t3c privacy verify` separately evaluates an explicit artifact or public-text policy
over the same selection; see [policy evaluation](#policy-evaluation-and-privacy-verify)
and [public-text dispositions](#public-text-dispositions-public-textv2).

```text
t3c [--local-dir PATH] [--text] privacy check --repo-path P --range BASE..HEAD
    [--branch NAME] [--text-file LABEL=FILE ...] [--privacy-policy FILE]
t3c [--local-dir PATH] [--text] privacy verify --repo-path P --range BASE..HEAD
    [--branch NAME] [--text-file LABEL=FILE ...] --privacy-policy FILE
```

Use the interpreter invocation in the [CLI contract](../cli.md). Global options
precede `privacy`. Imports and registration perform no process or state I/O.

## Inputs and selection

`check` and `verify` resolve this selection through one private helper.

- `--repo-path` is a local checkout or bare repository directory, required.
  Git runs with that working directory; this command never checks out files.
- `--range` is required: exactly two nonempty endpoints separated by `..`.
  Each endpoint starts with an ASCII letter, digit or underscore; its remaining
  name characters are ASCII letters, digits, `_`, `.`, `/`, `-`, optionally
  followed by `~` or `^` ancestry suffixes with decimal digits. A name cannot
  end with `.`. Option-shaped endpoints, three-dot ranges, whitespace, revision
  sets and object-path expressions are invalid. Hashes, named refs, `HEAD`,
  `HEAD~2` and `HEAD^` are supported. Endpoints are passed to `cat-file` on stdin,
  peeled to commits, and resolved to immutable 40-hex SHAs before enumeration.
- `--branch` defaults to the current branch name, read as a full symbolic ref
  and stripped of its exact `refs/heads/` prefix. A colliding tag cannot alter
  the screened name. Other symbolic-ref namespaces and malformed responses are
  refused. Detached HEAD requires an explicit publication branch. The supplied name must be nonempty
  supported text; it need not exist locally and this check does not validate
  its suitability for a later push.
- Repeated `--text-file` values split at the first `=` into nonempty label and
  path. Default: no files. Duplicate labels are allowed and preserve order.
  Each path must open as a regular file (a symlink to a regular text file is
  allowed). A label is also screened. Paths to these external files are never
  returned; labels identify title/body or other supplied text.

After parsing, every invocation freshly loads the list using
`identifier_list.load_identifiers(args.local_dir)`. No personal state path is
hard-coded. The shared core captures one validated pattern sequence for all matches. Missing, empty, unreadable, malformed or oversized lists block the
check, including an empty range. List validation and matching retain PV-1a's
`grep -E` semantics, 64 KiB list, 512 lines, 1 KiB per pattern, and 5-second
per-pattern timeout. Only LF delimits lines; regexes are not rewritten.

The range contains every commit reachable from HEAD but not BASE, including
merge-side commits. Every selected commit's full message body, every path and
blob in its entire recursive tree, the branch and the supplied texts are
screened. An unchanged blob is screened in each selected tree. A later removal
does not erase a prior finding. Author/committer headers are excluded. There
is no message-trailer exemption: a squash commit brought into the range can
block publication and needs Corrections or Shawn's decision.

Paths ending in `.lock` are excluded, case-sensitively, including their names
and contents. NUL-delimited tree records preserve tabs/newlines in filenames.
Directory paths, including empty subtrees, are screened alongside regular and
executable files and symlink blobs. Tree entries are enumerated explicitly;
only supported blobs have contents read. Symlink blobs are read from Git
without following targets. Submodules and other unsupported
tree entries fail closed. Shallow repositories are refused. Replacement
objects, grafts, ambient `GIT_*` selectors, optional lock writes, lazy fetches
and transport protocols are disabled for these reads. No hooks, filters,
textconv, diff tools, working-tree content substitutes or remote reads run.

## Supported data and failure behavior

Only SHA-1 Git repositories are supported. The actual repository object format
is checked explicitly; SHA-256 repositories are refused, including empty ranges.
No origin identity or network access is required for ordinary checks.

Content must be UTF-8 without NUL, at most 1,048,576 bytes per screened value.
Binary/non-UTF-8 files and oversized values are invalid; no truncation or
chunking is used. Each raw commit object (including headers), blob and Git
stdout response also has that byte limit. Object sizes are checked before
reading object contents. Enumeration and tree output limits are checked after
capture through the shared process seam, not by a streaming memory limiter.
Text files are read with a limit-plus-one read. Git calls time out at 30 seconds;
there is no aggregate invocation time limit. All subprocess calls use list
argv, capture both outputs and pass through `process.run`. Neither content nor
patterns, filenames, labels, branch names or range ref names enter Git/grep
argv; Git receives resolved object SHAs. No backend diagnostics are forwarded.

Exit 2, empty stdout and a safe stderr diagnostic mean invalid arguments,
list failure, unreadable/unsupported/oversized input, incomplete history,
malformed Git responses or process failure. Partial findings are not returned
as a completed result when a later read fails.

## Results

Exit 0 means all selected inputs were screened with no matches. Exit 1 means
publication is blocked. Both use the same object (global `--text` uses the
shared text renderer):

```json
{"ok":false,"commits":2,"findings":[{"field":"file","commit":"0123456789abcdef0123456789abcdef01234567","file":"notes.txt","label":null,"lines":[2]}],"action":"publication blocked; Corrections or Shawn must resolve matches"}
```

`commits` counts selected commits. `findings` has one item per matching field,
with a `lines` array of sorted 1-based line numbers from the private identifier
list that matched. These are not line numbers in the checked text, and matched
text is never returned. `field` is `branch`, `label`, `text`, `message`, `path` or
`file`. `commit` is the full SHA for message/tree findings, otherwise null.
`file` is the tree path for path/blob findings, otherwise null. `label` is the
supplied label for label/text findings, otherwise null. Unsafe dynamic metadata,
including a SHA that itself matches, becomes `[redacted]` in its entirety.
Branch text is never returned. Clean results have `ok: true`, `findings: []`
and `action: null`. Even an empty range screens the branch and supplied texts.

Findings are ordered by branch, supplied label/text pairs, then Git's commit
walk and tree order (message before tree; path before blob). This order is not
a chronology guarantee. No exception includes raw input or backend output.
Tests use synthetic state and temporary Git repositories through the real
`bin/t3c` entry point under the offline guard; see [testing](../testing.md).

## Shared strict scanner API

```python
scan_commits(repo, local_dir, commits, *, base, head, branch, texts=(), privacy_policy=None)
```

`t3compose.privacy.scan_commits` returns the raw dictionary described above:
`ok`, `commits`, `findings`, `action`. Findings return `ok: false` directly;
the CLI alone translates that dictionary to an exit-1 `Outcome`. Invalid or
incomplete inputs raise a sanitized `cli.CommandError` and return no completed
report, even if earlier inputs produced findings. This API grants no publication
permission and performs no approval or policy evaluation.

`repo` is a local repository path (`str` or `os.PathLike`), and `local_dir`
explicitly selects private state. The identifier list is freshly validated on
every call, including an empty selection. One captured sequence supplies all
matches. Existing private Git/object/text readers remain available to legacy
callers; the publisher's strict behavior is unchanged.

`commits` is an ordered sequence of unique full lowercase 40-hex SHA-1 commit
object IDs. `base` and `head` must also be full commit IDs. Refs, abbreviations,
revision expressions, tag/tree/blob IDs, unavailable objects, duplicates and
unordered collections or iterators are refused. Selection encoding (41 bytes
per ID including LF) is bounded by the existing 1 MiB enumeration limit.
Endpoints are verified even for an empty selection. The core does not peel tags,
resolve refs, sort commits or derive the selection from the endpoints.

The trusted caller must supply the complete set appropriate to its operation.
A push's exact set can differ from a review range. A clean subset result says
nothing about omitted commits; endpoints do not attest that the supplied set is
complete or reachable from HEAD. CLI `check` continues resolving its refs once
and enumerating every HEAD-not-BASE commit, including merge sides. There is no
arbitrary-subset CLI option.

`branch` is nonempty decoded text. `texts` is an ordered sequence of two-item
sequences `(label, decoded_text)`; labels are nonempty, contents may be empty,
and duplicate labels preserve order. Both values and the branch must be valid
UTF-8 without NUL and at most 1 MiB each, measured after encoding. Byte strings
and malformed pairs are refused. The CLI still reads bounded regular files
for `--text-file` and passes their decoded content to the core. Labels and
contents never enter process argv. Findings retain supplied commit/text order
and the existing tree walk order, redaction and `.lock` exclusion. Every selected
whole tree is screened; inherited binary or oversized blobs still refuse in strict mode.

## Internal artifact provenance foundation

The provenance loader supplies inputs for the explicit scanner mode below.
The publisher continues using strict screening without this opt-in.
Actual owner approval of any real inventory remains separate from implementing
this capability. The following APIs return internal inputs, never a completed
scan, eligibility, `policySatisfied`, or publication permission.

```python
prepare_policy_identifiers(local_dir)
load_artifact_policy(repo, local_dir, policy_path, *, base, patterns)
```

Preparation freshly loads and validates the identifier list and returns exactly
`policyVersion`, `policyDigest`, `listDigest`, `patternCount`. It returns no
patterns. `listDigest` is SHA-256 of canonical JSON of the validated pattern
array, preserving order, duplicates and empty rows with the existing LF rules.
The loader's trusted caller supplies the **same captured validated list/tuple**
used by its matcher. The loader copies it to a tuple, validates its syntax, and
derives its digest without reloading another list. The scanner loads freshly once per operation and uses that snapshot throughout.

`repo` is a local repository path; `local_dir` selects current records state.
`base` is an immutable full lowercase SHA-1 commit ID. `policy_path` may be an
absolute or relative local path. All referenced filenames below resolve relative
to its directory, independently of cwd; absolute references are refused. Paths
are exact, nonblank slash-separated names: no empty, dot or dotdot segments,
backslashes, `*`, `?`, `[` or `]`. No wildcard or prefix interpretation occurs.
Policy snapshots refuse final symlinks; parent directories are trusted local
configuration. There is no network lookup.

Each policy, manifest, receipt, source and verification file is opened once with
`O_RDONLY|O_NONBLOCK|O_NOFOLLOW`, checked as regular by `fstat` on that descriptor,
and read with a 1 MiB plus one limit. Digests hash the same accepted raw bytes,
including whitespace. The raw descriptor is closed if stream setup fails; after
setup the stream owns its closure. FIFOs, devices, directories and oversized
inputs refuse.
JSON is UTF-8, duplicate-key and nonfinite-number rejecting, with maximum nesting
32. NUL, surrogates, missing/unknown keys and wrong scalar types refuse. Every
`schemaVersion` is integer 1, never boolean. Errors carry no input values or
paths. These are private internal inputs and metadata, unsuitable for stdout or
public evidence without the usual privacy check.

All digest strings use `sha256:` plus 64 lowercase hex digits. Git IDs use 40
lowercase hex digits; Git SHA-1 identity and document SHA-256 have different
purposes. `policyVersion` is `artifact-provenance/v1`. `policyDigest` hashes the
canonical descriptor below. It identifies the versioned contract, not code
authenticity. Canonical JSON uses sorted keys, compact separators, UTF-8 without
ASCII escaping, and no nonfinite numbers, as in `jsonio.canonical_json`.

```json
{"authority":"owner","contextVersion":1,"objectFormat":"sha1","policyVersion":"artifact-provenance/v1","schemaVersion":1,"verification":"git-object-inventory"}
```

All schemas below are closed, with exactly the listed keys:

| Document | Keys |
| --- | --- |
| Policy | schemaVersion, policyVersion, manifestFile, manifestDigest, receiptFile, receiptDigest, authorityWorkId |
| Manifest | schemaVersion, repository, objectFormat, baseline, entries |
| Manifest entry | path, mode, blobOid, size |
| Receipt | schemaVersion, receiptId, repository, baseline, manifestDigest, policyVersion, policyDigest, listDigest, authorityWorkId, approvalSeq, sourceFile, sourceDigest, verificationFile, verificationDigest |
| Source | schemaVersion, receiptId, contextDigest, sourceRef, date, quote |
| Verification | schemaVersion, receiptId, contextDigest, verifier, sourceRef, method, baselineEvidence, inventoryDigest, result |
| Verification baselineEvidence | repository, commit, objectFormat, entriesDigest |

Manifest entries have no schemaVersion field. There are 1–4096 entries with
unique exact paths, mode string `100644`, lowercase SHA-1 `blobOid`, and integer
`size` from 0 through 2^63−1 (boolean refused). Executable, symlink and gitlink
modes are unsupported. `objectFormat` is exactly `sha1`. Repository identities
must equal the canonical local origin identity in manifest, receipt and authority
work. Origin is read through hardened `privacy._git`, then its returned URL is
normalized by pure URI/string parsing and `records.canonical_identity`, without
filesystem probing or ambient directory-based Git lookup, even when a local
directory has the same spelling. HTTPS, SSH URI and scp-style SSH origins are
supported; passwords, explicit ports, nonempty queries/fragments and malformed
identities refuse. The repository must be complete and SHA-1. Baseline and BASE
must exist as exact commit objects, and baseline must be an ancestor of BASE.
The loader alone does **not** validate entry tuples against either tree; the
opt-in scanner independently validates them before scanning.

`receiptId` and `authorityWorkId` are 1–128 ASCII characters matching
`[A-Za-z0-9][A-Za-z0-9._-]*`. `approvalSeq` is a positive integer event sequence.
The receipt joins the policy's authority stream and manifest digest and the
current policy/list identifiers. Source `quote` is the exact actual owner answer;
`date` is its real Gregorian `YYYY-MM-DD` date, not a reconstruction date.
`sourceRef` is a concrete nonblank communication locator. Verification `verifier`
and `sourceRef` are concrete nonblank independent-observation attestations;
`method` is exactly `git-object-inventory`, `result` exactly `verified`.
`inventoryDigest` equals the raw manifest digest. Its baselineEvidence must equal
`{repository: manifest.repository, commit: manifest.baseline, objectFormat: "sha1",
entriesDigest: SHA256(canonical_json(manifest.entries))}`. These fields bind the
independently supplied evidence; actual Git entry checks occur in the opt-in scanner.

Two context digests avoid cycles. The inventory context is canonical JSON of
`schemaVersion: 1` plus receipt fields `receiptId`, `repository`, `baseline`,
`manifestDigest`, `policyVersion`, `policyDigest`, `listDigest`, `authorityWorkId`.
Source and verification both bind this digest and receiptId. The full context
adds `sourceDigest` and `verificationDigest` to that same context object. Policy
pins the receipt's raw digest separately. The authority question binds both
receipt identity and this full context; the record recipe is in
[records](../records.md#artifact-provenance-source-record-recipe).

The returned deeply immutable mapping contains `manifest`, `receiptId`, `base`,
`repository`, `authorityWorkId`, `approvalSeq`, `inventoryContextDigest`,
`contextDigest`, `policyFileDigest`, `manifestDigest`, `receiptDigest`,
`sourceDigest`, `verificationDigest`, the four preparation fields,
`authorityEventCount`, `authorityCheckpoint`, `authorityWorkDigest`,
`authorityEventsDigest`, `observedStart`, and `observedEnd`. Manifest entries are
tuples of immutable mappings. It does not return source quotes or raw records.
The checkpoint binds the exact validated work and observed event prefix; the
additional work/events digests hash their canonical parsed snapshots, not raw
record-file formatting. UTC start/end timestamps bracket the observation.

Authority is freshly loaded from the selected current records stream on every
call, never from a detached receipt or a caller-supplied prefix. Revocation is
terminal for receipt identity across contexts. Concurrent append after event
enumeration is possible: this is an observed prefix, not atomic nonrevocation.
Future evaluation/publication gates must re-observe current state. Owner-source
provenance and verifier independence are cooperative attestations, not identity
authentication. Coordinators are responsible for faithfully recording them.

Policy evaluation of these inputs is [specified below](#policy-evaluation-and-privacy-verify),
and [public-text dispositions](#public-text-dispositions-public-textv2) are a separate
policy kind. Publisher integration remains deferred; neither is waived or satisfied by
this scanner.


## Explicit inherited-artifact accounting

`--privacy-policy FILE` (API keyword `privacy_policy`, default `None`) loads the
private policy using the same single validated matcher snapshot. Legacy handler
argument objects without `privacy_policy` still select strict behavior. No
policy is installed or discovered automatically. Implementing this capability
approves no actual inventory; only an actual owner decision can do that.
The provenance schemas and `artifact-provenance/v1` descriptor above remain
unchanged: they bind the inventory and cooperative authority, not eligibility.

Before scanning, every manifest entry must exist at both the immutable baseline
and resolved BASE with the exact path, mode `100644`, blob OID and size. Actual
Git blob type and size are checked, even with an empty selection. Baseline must
remain an ancestor of BASE. Missing or fabricated object IDs cannot authorize
an entry. No path enters Git argv, and working-tree files never substitute for
Git objects. Only SHA-1 repositories are supported.

Every selected whole tree and message is still visited. Supported bounded UTF-8
without NUL is always screened, including inventoried text and new/changed text.
Only unsupported bodies (size above 1 MiB, NUL or invalid UTF-8) may become
`approvedUnscanned`. The occurrence must exactly match an inventoried regular
blob and the same tuple in **every direct parent**, including parents outside
the selection. Parent facts do not expand the claimed selected scan. A root
introduction cannot qualify. Rename, copy to a new path, byte/mode changes,
delete/readd, change/revert and merge-side introductions cannot inherit an
opaque exemption. Deletion is allowed. A clean HEAD cannot hide a failing
intermediate selected tree. Trusted callers remain responsible for completeness
of their ordered selection; endpoints do not attest completeness.

Type/size are checked before reading a body. Above-bound bodies are never loaded;
bounded bodies are read to determine text support. Git/read/parse errors never
become opaque eligibility. There is no extension, binary-format, OCR, EXIF,
archive or secret-discovery claim. Branches, labels, supplied texts, messages,
paths, directory names and symlink targets retain screening. The existing
case-sensitive `.lock` exclusion still excludes both name and body. An inventory
entry under that exclusion cannot establish the required baseline/BASE tuple in
this version; omit excluded entries from the manifest.

A result with any approved-unscanned occurrence has `ok: false`, action exactly
`policy-evaluation-required`, and CLI exit 1, even with no text findings. Findings
are retained when both occur. Findings alone retain the strict action and exit 1.
A valid policy with neither opaque occurrences nor findings may return scoped raw
clean/exit 0. Unsupported unapproved content, invalid provenance, bad list,
missing/corrupt objects or incomplete reads produce sanitized `CommandError`
(CLI exit 2, empty stdout), including failure after earlier findings or approved
occurrences. No completed partial report is emitted.

### Coverage schema: artifact-coverage/v1

Only explicit policy mode adds `coverage` to the existing four result fields.
All keys below are exact; arrays preserve scan/input order.

| Object | Fields |
|---|---|
| coverage | version, repository, base, head, commits, selectionDigest, branchDigest, texts, provenance, screened, approvedUnscanned, excluded |
| texts item | labelDigest, textDigest |
| screened item | field, commit, file, label, contentDigest |
| approvedUnscanned item | commit, file, mode, blobOid, size, receiptId, contextDigest, contentScreened |
| excluded item | commit, file, reason |
| provenance | receiptId, authorityWorkId, approvalSeq, inventoryContextDigest, contextDigest, policyFileDigest, manifestDigest, receiptDigest, sourceDigest, verificationDigest, policyVersion, policyDigest, listDigest, patternCount, authorityEventCount, authorityCheckpoint, authorityWorkDigest, authorityEventsDigest, observedStart, observedEnd, baseline |
| authorityCheckpoint | lastSeq, digest |

`version` is exactly `artifact-coverage/v1`. Repository is the canonical origin
identity. BASE/HEAD/baseline and `commits` bind full immutable SHA-1 IDs; `commits`
is the exact ordered selection, not the parents consulted. `selectionDigest` is
SHA-256 of canonical JSON of that ordered array. `branchDigest`, ordered text
pair digests and each `contentDigest` hash the exact UTF-8 bytes. Every SHA-256
uses the `sha256:` prefix. List digest retains the canonical pattern-array
semantics above. Provenance copies the accepted loader snapshot (excluding its
manifest, repository and base, which are already bound), and adds baseline.
It includes all input-file, policy, list, context and authority bindings and the
UTC authority observation window. The checkpoint retains its existing schema.

`screened` records each branch, label, text, message, path and supported body
actually screened, in finding traversal order, with a content digest even when
no match occurs. Its field names and nullable commit/file/label follow findings.
`approvedUnscanned` records every opaque occurrence with exact immutable tuple
and approval context, always `contentScreened: false`. `excluded` records each
`.lock` entry with reason exactly `lock-suffix`; it claims no name/body screening.
Author/committer headers retain their existing exclusion, without separate
coverage items. Counts are array lengths; `commits` in the outer result remains
an integer count. No source quotes, source locators or private policy-file paths
are returned.

Every dynamic coverage string is screened with the same matcher and replaced
entirely by `[redacted]` if unsafe, including IDs and digests. Consumers must not
treat a redacted binding or editable saved report as verification evidence.
Coverage is raw evidence, never `policySatisfied` or publication permission.
The [artifact evaluator](#policy-evaluation-and-privacy-verify) reruns the shared
core and validates its own approvals; [public-text dispositions](#public-text-dispositions-public-textv2)
are evaluated separately; publisher C must re-observe current authority and exact publication
inputs at the gate. Cooperative source provenance is not authentication; the
observed prefix provides no atomic revocation guarantee after observation. Strict
publisher behavior remains unchanged until those separate integrations land.

## Policy evaluation and `privacy verify`

`evaluate_policy` and `t3c privacy verify` answer one separate question: is the
raw scan's only non-text coverage, inherited opaque bytes accounted as
approved-unscanned, covered by a valid, current, unrevoked owner decision, with
**zero unresolved text findings**? Raw screening and policy satisfaction are different
verdicts. The raw check of the same selection keeps exiting 1 for any
approved-unscanned occurrence; the policy verdict never relabels it.

An artifact-provenance policy is evaluated artifact-only. Every text finding in a
branch, label, text, message, path or file stays unresolved: no public-text
disposition, public snapshot, [literal proof](#private-literal-evidence-foundation) or
saved report is an input, and none can change the verdict. The literal-proof seam is
not called. A `public-text/v2` policy uses the separate
[dispositions](#public-text-dispositions-public-textv2) (`policy-evaluation/v2`);
publisher integration is another Task (C), neither waived nor satisfied here.

```python
evaluate_policy(repo, local_dir, commits, *, base, head, branch, texts=(), privacy_policy)
```

Arguments, bounds and exact-selection rules are those of
[`scan_commits`](#shared-strict-scanner-api): the caller supplies the complete
ordered selection, which is never recomputed from `base..head`. `privacy_policy`
is a required keyword-only path with no default; `None` raises a sanitized
`CommandError`. There is no parameter for a report, pattern list, proof,
disposition, snapshot or verdict: an unknown keyword is a `TypeError`. The
function loads the identifier list freshly **once** and uses that captured
sequence for the shared scan and for both policy observations below; the list is
never reread. It reruns the same scan core as `scan_commits`, so history,
parent, metadata and `.lock` rules are identical, but keeps the facts unredacted
in process. Any invalid, incomplete or changed input raises a sanitized
`CommandError`; no partial or completed result is returned.

### Observation window

1. `observation.start`: the UTC clock, before the list is read.
2. The identifier list is captured once.
3. **Start observation.** The policy, manifest, receipt, source and verification
   files are each read once and pinned by digest, and the authority stream is
   loaded and validated (`authority.start`, with its own UTC window).
4. The exact selection is scanned.
5. **End observation.** The same policy path is read again and the authority
   stream reloaded, with the same captured list. It must validate again and be
   unrevoked, and these facts must equal the start observation exactly:
   `receiptId`, `repository`, `authorityWorkId`, `approvalSeq`,
   `inventoryContextDigest`, `contextDigest`, `policyFileDigest`,
   `manifestDigest`, `receiptDigest`, `sourceDigest`, `verificationDigest`,
   `policyVersion`, `policyDigest`, `listDigest`, `patternCount`,
   `authorityWorkDigest` and `base`. A terminal revocation, a re-serialized or
   replaced file, a substituted receipt or approval, or a replaced authority
   work record refuses. Unrelated events appended to the stream do not; the
   count, checkpoint, events digest and timestamps of both observations stay in
   `authority`. The list-derived facts and `base` are equal by construction
   under the captured list and the one `base` argument, and are kept for
   completeness.
6. `observation.end`: the UTC clock.

Each approved-unscanned occurrence is also compared with the manifest tuple (path,
mode, blob OID, size) and the start receipt and context digests, and its
`contentScreened` must be `false`, rather than trusting the scan's own labels.

This is an observation window, not a guarantee. A change after step 5 is not
detected, and revocation is not atomic across later use or token minting, so the
publication gate must observe again. The list is captured, not rechecked: a later
list change is also the gate's concern. Provenance stays cooperative. The
evaluator verifies that a recorded decision has the exact grammar, the reserved
`owner` label, the joined quote, date and context, and no revocation. It cannot
verify who said it or who recorded it, and a string saying owner is not proof.

The end observation does not require the authority stream to extend the start
prefix. Valid unrelated event edits or truncation and non-data approval metadata
changes can pass; changed observed approval data or a current revoke refuse. Changes
made and undone between observations are invisible. Provenance remains cooperative,
not authentication, atomic revocation or tamper evidence. Replacing the on-disk list
during one operation neither changes its captured input nor retroactively invalidates
that verdict; a publication gate runs a fresh evaluation.

### Private result: `policy-evaluation/v1`

The result is a `PolicyEvaluation`: read-only, neither a mapping nor JSON
serializable, with a constant `repr`, and it refuses pickling and copying. It
holds exact bindings and unredacted facts and is process-private: never write it
to stdout, a record or GitHub. `facts` is a deeply immutable mapping (lists are
tuples), `policy_satisfied` a boolean, and `summary()` the only serializable form.

| Key | Value |
|---|---|
| version | `policy-evaluation/v1` |
| policySatisfied | true only when there is no text finding and every check above holds |
| rawStatus | `findings`; else `policy-evaluation-required` when any approved-unscanned occurrence exists; else `no-text-findings` |
| raw | `ok`, `commits`, `findings`, `action` of the raw scan; findings keep the real commit, file and label |
| coverage | the exact [artifact-coverage/v1](#coverage-schema-artifact-coveragev1) object, unredacted; it binds repository, BASE, HEAD, ordered commits, selection, branch, text and provenance digests |
| authority | `start` and `end`, each with `authorityEventCount`, `authorityCheckpoint`, `authorityWorkDigest`, `authorityEventsDigest`, `observedStart`, `observedEnd` |
| observation | `start` and `end`, UTC ISO timestamps bracketing steps 1 to 6 |

Every digest here is secret-equivalent: an unsalted hash confirms a guess offline,
and the matcher's redaction does not recognize a digest. That is why the summary
carries none, and why a consumer such as the publisher must rerun the evaluator
instead of trusting saved output.

### `privacy verify`

```text
t3c [--local-dir PATH] [--text] privacy verify --repo-path P --range BASE..HEAD
    [--branch NAME] [--text-file LABEL=FILE ...] --privacy-policy FILE
```

Range, branch and `LABEL=FILE` semantics are those of `check`, resolved by one
shared private helper so the two commands cannot disagree; only
`--privacy-policy` differs, being required here. Exit 0: `policySatisfied` is
true. Exit 1: it is false, because text findings are unresolved. Exit 2: invalid
or incomplete, such as a missing option, policy, list or provenance, a bad
selection, a changed or revoked binding, or a late failure; stderr holds one
sanitized line, stdout is empty and no pass is recorded. Global `--text` renders
the same summary.

```json
{"action":null,"commits":1,"coverage":{"approvedUnscanned":1,"excluded":0,"screened":3,"textFindings":0,"texts":0},"policySatisfied":true,"rawExit":1,"rawStatus":"policy-evaluation-required","version":"policy-verify/v1"}
```

| Key | Meaning |
|---|---|
| version | `policy-verify/v1` for an artifact policy; a `public-text/v2` policy returns [`policy-verify/v2`](#public-text-dispositions-public-textv2) |
| policySatisfied | the policy verdict above |
| rawStatus | `no-text-findings`, `findings` (takes precedence) or `policy-evaluation-required` |
| rawExit | 0 or 1: what `privacy check` with the same policy exits for this selection |
| commits | selected commit count |
| coverage | counts only: `screened` values (branch, labels, texts, messages, paths and supported file bodies), supplied `texts`, `textFindings` (one per matching field, all unresolved), `approvedUnscanned` occurrences (never screened) and `excluded` `.lock` entries |
| action | `null` when satisfied, else `policy unsatisfied; Corrections or Shawn must resolve text matches` |

The summary has no `ok` and makes no claim that all content was screened. It never
holds a digest, object ID, commit, path, label, repository, quote, source or
receipt string, policy path, list value or matched text. Approved opaque bytes
appear only in the `approvedUnscanned` count. The verdict is read-only policy
assessment, never permission to publish; the publisher stays strict until a later
Task integrates this evaluator at its gate.

A work adopting an approved inventory requires the exact `privacy verify`
invocation as its policy check and keeps the raw `privacy check` observation as
failed evidence; see the [records recipe](../records.md#work-epochs-and-required-checks).

## Private literal evidence foundation

`_literal_proofs(repo, patterns, text, *, public_commit)` is an internal seam;
there is no CLI, policy evaluator or publisher integration for this evidence.
The trusted caller supplies the same captured validated pattern list/tuple as
its scanner. The helper copies it, uses existing list/text bounds, and never
reloads private state. `text` is decoded UTF-8 without NUL, at most 1 MiB.
`public_commit` must be a full lowercase SHA-1 naming an actual local commit
object in a nonshallow SHA-1 repository. No refs, tag peeling or network reads.
The parameter name does **not** attest that the commit has ever been public.

`GREP_OPTIONS` presence refuses proof before any matcher runs. The unchanged
shared matcher then runs on the full captured list and the original text. Its
row numbers must be sorted unique exact integers within the list; anything else
refuses `unaccounted-match`. Only the rows it reports as matching are held to
the conservative proof grammar: a nonempty literal, optionally anchored by `^`
at its start and `$` at its end, or `|`-separated such branches. Escapes,
groups, classes, wildcard/quantifier syntax, internal anchors and ASCII control
characters are unsupported. Limits are 64 total branches and 8,192 literal
UTF-8 bytes across the matching rows. Duplicate or contained alternatives and
any proper suffix/prefix overlap (including self overlap) among them refuse the
whole proof, even when anchors or actual input would prevent overlap. For
example, a literal with the same first and last byte is conservatively
unsupported. This recognition proves non-nullability and excludes matches hidden
by greedy nonoverlapping extraction; it does not replace ERE matching with
Python regexes.

A row the shared matcher reports as not matching has no occurrence and needs no
proof; its fit to the proof grammar, literal size and overlap do not matter.
Full captured-list validation and shared-matcher acceptance still apply. Matching rows
keep their original 1-based numbers in `patternLine`, and `listDigest` covers the
full captured list. A matching row outside the grammar refuses the whole proof as
`unsupported-grammar`, including every empty-capable row it reports.
An unanchored empty-capable row, such as an empty row or `a*`, matches every
nonempty input. A context-dependent one, such as `^$`, matches only where its
context occurs: for `^$`, an empty line. Elsewhere it has no occurrence. A row the shared matcher itself rejects is a `proof-read-failed`
refusal. The raw scanner is unchanged.

Each matching row runs separately through system `grep -E -b -o`, under captured
inherited locale settings, with LF boundaries. Output offsets are byte offsets,
validated against original bytes and branch anchors. A second ERE extraction over
input with extracted spans replaced by NUL (outside the supported grammar,
preserving LF and byte positions) refuses omitted occurrences. After extraction a
second, independent shared raw match must equal the first and the extracted row
set, or the proof refuses `unaccounted-match`. Output and occurrences are bounded
at 1 MiB per command and 4,096 respectively. No empty, overlapping, malformed or
unaccounted proof completes. Relevant inherited `LANG`, `LANGUAGE`, `LC_*`, and
grep environment variables are canonically digested; changes across the operation
refuse. This detects changes, without promising atomic environment stability.
Callers must not concurrently mutate process environment.

Witness search reads the pinned recursive tree through hardened Git seams. The
whole listing is read and validated before any witness is sought, even for input
with no occurrence. A gitlink or other unsupported entry, a non-UTF-8 name, or a
listing over 1 MiB refuses as `proof-read-failed` wherever the witness would sort.
Only modes `100644`/`100755`, supported bounded UTF-8/no-NUL blobs can witness.
Symlinks, directories and excluded `.lock` paths cannot witness; opaque and
oversized bodies cannot witness. Size is checked before reading. At most 128
distinct regular candidates and 8 MiB aggregate bounded body reads are allowed.
Candidates are visited in Git tree order. Oversized regular candidates count
toward the 128 without being read. Opaque candidates within the size bound are
read so they can be classified; they count toward both the 128 and the 8 MiB
budget, and cannot witness. A witness that sorts late can therefore refuse as
`proof-limit` after little or no reading. Once all distinct
matched literals have witnesses, search stops reading bodies; the limits apply
to candidates examined before that point, not the total snapshot size. Later
candidates are not claimed read or screened; unresolved literals still require
continued search or a bounded refusal.
Object type, size and content identity are checked; command/read/malformed-object
failures cannot yield completed partial evidence. Search is existential, not a
claim that every byte in that snapshot was screened. Fixed-string grep uses
private pattern files, forced byte locale and case-sensitive matching, plus an
independent exact byte containment check. An unknown literal gets no witness.

Patterns go only in atomic mode-0600 files inside a mode-0700 temporary directory;
text goes on stdin. Private output files capture grep stdout/stderr and are read
with bounds after exit; this is not a streaming disk-write bound. Scratch is
removed on success and failure. Grep has a five-second timeout per call; Git
retains its 30-second timeout and existing response bounds. No aggregate runtime
bound is claimed. No literal, pattern, source word, path or backend diagnostic is
returned or sent to stdout/stderr or argv.

The private result is a dictionary with exactly:

- `proofVersion`: string `literal-proof/v1`.
- `publicCommit`: supplied validated full lowercase SHA-1 string.
- `listDigest`: `sha256:` plus 64 lowercase hex digits, over canonical JSON of
  the full captured validated pattern array, matching rows or not, preserving
  row order.
- `inputDigest`: the same digest format, over original UTF-8 bytes.
- `localeDigest`: the same digest format, over canonical JSON of the relevant
  inherited environment mapping, including presence/absence.
- `complete`: boolean describing literal evidence only.
- `occurrences`: array in pattern-row then byte-offset order. Each object has
  exactly integer `patternLine` (1-based), `byteOffset` (0-based), positive integer
  `byteLength`, `literalDigest` (SHA-256 of exact bytes), `publicBlobOid` (actual
  full lowercase SHA-1 or null) and `publicContentDigest` (SHA-256 of witness
  bytes or null). Both witness fields are null together.
- `reason`: null on completion; otherwise a fixed refusal code. Codes are
  `unsupported-grammar`, `unsupported-environment`, `invalid-match-output`,
  `unaccounted-match`, `overlapping-match`, `proof-limit`, `invalid-object`,
  `invalid-witness-output`, `environment-changed`, `proof-read-failed`, or
  `missing-witness`.

Invalid arguments, non-UTF-8-encodable relevant environment data, or snapshot
preconditions raise sanitized `CommandError`.
Operational/unsupported refusals return no partial occurrences. `missing-witness`
retains complete extraction with nullable witnesses and `complete: false`.
`proof-read-failed` covers every deterministic bound or shape refusal that has no
code of its own, as well as read and command failures: unsupported snapshot
entries, non-UTF-8 names, a listing or grep output over 1 MiB, or a row the shared
matcher rejects. It is not automatically transient and must not be retried blindly.
A supported input with no occurrences can complete vacuously; this grants no
approval and says nothing about omitted fields.

The whole result, including `literalDigest`, `listDigest` and `inputDigest`, is
secret-equivalent private evidence. These unsalted hashes let anyone holding them
confirm a guess of a private value offline. Never write the result to stdout, to
public or shared evidence, or to GitHub, even after the matcher-based redaction
used for coverage, which does not recognize a digest. The public-text evaluator and
publisher C keep the exact proof facts in private state and emit nonsecret summaries only.

A witness proves only that the exact bytes occur in one supported regular blob,
possibly as a substring of unrelated longer text. It does not prove identifier
identity, field, context or approved scope.

The [public-text evaluator](#public-text-dispositions-public-textv2) reruns shared
scanning with the same captured list and locale, collects all actual field
occurrences, and joins standing owner authority, approved public-product scope,
independently attested prior publication and current nonrevocation. Local object
presence or literal proof cannot declassify credentials or unrelated private data.
The [artifact-only evaluator](#policy-evaluation-and-privacy-verify) never calls this
seam. C owns publisher integration and publication-gate rechecks. Raw scan results,
history coverage, defaults and publisher behavior remain unchanged. No real opaque
approval or receipt is created by this foundation; complete literal proof is never
policy satisfaction or permission to publish.

The public-text evaluator keeps the candidate bounds and Git-tree-order witness search
unchanged: a late witness yields `proof-limit`, which leaves its findings unresolved.
Raising a bound or accepting a caller-supplied witness path needs its own reviewed change.

## Public-text dispositions (`public-text/v2`)

`--privacy-policy FILE` whose `policyVersion` is `public-text/v2` asks a different
question from artifact accounting: is every text finding covered by the owner's
standing public-by-design rule, and proven public? No new command or flag exists;
`artifact-provenance/v1` files keep their own mode, `policy-evaluation/v1` and
`policy-verify/v1` unchanged. This is never a global identifier exception, and
`t3compose.privacy_dispositions` joins current facts the facade supplies. It reads
no policy file, runs no process, scans nothing and never imports the facade; its
only I/O is observing the records stream. Version 2 binds each review to the exact
selection and to each finding's place in it; `public-text/v1`, which did not, is refused.

```python
observe(policy, read, *, local_dir, repository, list_digest)    # evidence and current authority
resolve(observed, findings, prove, *, locale_digest, selection_context)   # one private verdict per finding
```

Callers' obligations, which the module checks by refusing rather than trusting:
`repository` is the canonical origin identity (pure parsing; a path is refused before
any record or process is touched); `list_digest` and `locale_digest` come from the one
captured list and environment; `read(name, digest)` is the facade's callback, which reads
an exact relative name once, pins its raw digest and returns strict JSON; `findings` are
the scan's own `{field, lines (a list), inputDigest, context}`; `prove(inputDigest)`
returns the shared-list literal proof and is called only for a finding whose field is in
scope, whose review is for this selection and that has an entry (the facade memoizes it
per input). Invalid, stale or revoked evidence, a malformed name or
digest, a lone surrogate or a value that is not JSON raises `Refusal`, a `ValueError`
whose message and `repr` are constant; the facade turns it into a sanitized `CommandError`.
All closed documents carry integer `schemaVersion` 2.

| Document | Keys |
| --- | --- |
| Policy | schemaVersion, policyVersion, authorityWorkId, ruleFile, ruleDigest, publicationFile, publicationDigest, dispositionsFile, dispositionsDigest, artifactPolicyFile, artifactPolicyDigest |
| Rule | schemaVersion, ruleId, repository, scope, fields, policyVersion, policyDigest, authorityWorkId, approvalSeq, source |
| Rule source | sourceRef, date, quote |
| Publication | schemaVersion, ruleId, ruleContext, verifier, sourceRef, method, result, repository, publicCommit |
| Dispositions | schemaVersion, ruleId, ruleContext, verifier, sourceRef, method, result, publicCommit, listDigest, selectionContext, entries |
| Entry | field, context, inputDigest, occurrences (nonempty; at most 256 entries and 4,096 occurrences in all) |
| Occurrence | patternLine, byteOffset, byteLength, literalDigest, publicBlobOid, publicContentDigest, scope |

The two artifact keys are both `null` (pure public text, which invents no opaque
inventory) or both set to an unchanged artifact policy file and its raw digest; the
evaluator refuses a different file. `scope` is `public-product-text`; `fields` is a
strictly sorted, unique, nonempty subset of `branch`, `file`, `label`, `message`,
`path`, `text`. `policyDigest` hashes the versioned descriptor `{authority: owner,
contextVersion: 1, policyVersion, schemaVersion: 2, scope, verification:
occurrence-attestation}`. `ruleContext` is the canonical digest of the rule without
`approvalSeq`. The rule's repository, and the publication's, equal the canonical
origin identity.

**Authority.** The existing `decision` kind in the policy's authority work records
`privacy-public-text/v1 approve <ruleId> <ruleContext>`, with `answer` the exact
`source.quote`, `date` its date and `decidedBy` `owner`, at `approvalSeq`. A revoke
(`... revoke <ruleId> <ruleContext>`) is terminal for the rule, even naming another
context; an approval of another context is ambiguous; a malformed, unresolved or
nonowner decision in the `privacy-public-text` namespace refuses. A near-miss spelling
of the namespace (another letter case, surrounding whitespace) counts as in it and
refuses, so a mistyped revocation is never ignored; the `privacy-artifact` authority
reads its namespace the same way. This is conservative detection, not normalization:
the grammar stays exact. Nothing in the `privacy-artifact` namespace, a generic
decision, prior publication or an assignment grants this authority. See the
[records recipe](../records.md#public-text-standing-authority-recipe).

**Evidence.** The publication attests, independently, that `publicCommit` (a full
SHA-1 commit, not a ref) was actually public; a local object or caller SHA proves
nothing. Dispositions bind that commit and the one captured list's digest and
attest each matched occurrence by exact `(field, context, inputDigest)`: the digest is
of the input's UTF-8 bytes (a blob, message, path, branch, label or text). An
occurrence's scope is the reviewer's verified class: `public-product-text`, or an
exclusion (`credential`, `private-path`, `unrelated`, `ambiguous`) that leaves it
unresolved. `method` is `prior-publication` or `occurrence-review`; `result` is
`verified`. Verifier independence and locators are cooperative attestations.

**Selection and contexts.** A review is made for one selection and for one place per
finding; identical bytes elsewhere are not reviewed. `selectionContext` is the digest of
canonical JSON `{version: selection-context/v1, repository, base, head, commits,
branchDigest, texts}`: the canonical origin identity, the immutable endpoints, the commit
IDs in the order supplied (never sorted), the branch digest, and every supplied text as
`{labelDigest, textDigest}` in order, duplicates and positions included. An entry's
`context` is the digest of `{version: finding-context/v1, field, commit, pathDigest,
index}` for the finding it covers: `commit` for message, path and file findings,
`pathDigest` for path and file findings, the text's position `index` for label and text
findings, else null. Digests hash UTF-8 bytes. The facade builds both from the one scan's
field trace, never from a saved report or stdout. A different commit, order, endpoint,
branch, text, path or position is another selection or place, so the review does not
carry over (reasons `selection`, `unattested`). The raw report is bound finding by
finding: each finding of the current scan needs its own entry, a finding without one is
unresolved, and an entry that no finding uses is ignored. An entry exists per place: a
blob that is unchanged across the selected commits needs one for each, within the bounds
above. To author a review, evaluate once with no entries and any well-formed
`selectionContext`: every finding is unresolved, and the private result names the real
`selectionContext` and each finding's `context` and input digest.

**Join.** The facade reruns the shared scan once, from one fresh captured list and
locale. For each finding in scan order it then requests `_literal_proofs` for the
exact input, once per distinct input, only when the finding's field is in `fields`,
the review is for this selection and an entry exists for its place. A finding
resolves only if its proof is `literal-proof/v1`,
complete, with the pinned `publicCommit`, the captured list's digest, the input
digest and the captured locale digest; its rows equal the finding's `lines`; every
occurrence has a public witness; its occurrences equal the entry's exactly (every
repeat, none extra); and each scope is `public-product-text`. A witness is
necessary and never sufficient. Unknown or uncovered literals, unsupported, empty
or overlapping proofs, stale inputs and exclusions leave findings unresolved,
whatever the snapshot holds. Reasons stay private: `out-of-scope`, `selection`,
`unattested`, `proof`, `mismatch`, `excluded`.

**Observation.** The start observation reads and validates the evidence and
authority; the scan and proofs follow; the end observation repeats the first and
must give equal bindings (every pin, rule, repository, context, snapshot, list,
selection and work digest, the artifact pair and the policy file's digest) and an equal
locale digest. A changed or revoked binding, a trace that does not account for every
finding, or any invalid file raises a sanitized `CommandError`. The
[window limits above](#observation-window) apply unchanged: no stream-prefix continuity
is checked and changes made and undone between observations are invisible. It is one
captured list, so a fresh gate evaluates again. The module cannot recompute the
fingerprints it is given; the facade's tests pin them, and provenance stays cooperative.

**Raw scan.** `scan_commits` and `privacy check` with this policy observe its evidence
as above (an invalid, stale or revoked policy refuses: exit 2), then return the raw
scanner's result (findings or approved-unscanned bytes exit 1; unsupported bytes exit
2). Dispositions never change that result, and only the optional artifact part adds coverage.

**Result `policy-evaluation/v2`** is a `PolicyEvaluation` as above (never printed):
`version`, `policySatisfied`, `rawStatus` and `raw` as v1; `selection` (the
`selection-context/v1` facts, plus `selectionContext`, `listDigest`, `patternCount`,
`localeDigest`); `coverage` and `authority` (the v1 objects, or `null` without an
artifact part); `publicText` (`bindings`, `authority.start` and `.end`, `verdicts`
aligned with the raw findings as `field`, `context`, `inputDigest`, `resolved`,
`reason`, `occurrences`, plus `resolved` and `unresolved`); and `observation`.
`policySatisfied` is true only when every finding is resolved. A reviewer reads each
place's `context` and the `selectionContext` here.

**Summary `policy-verify/v2`** has `version`, `policySatisfied`, `rawStatus`,
`rawExit`, `commits`, `action`, `coverage` (`texts`, `textFindings`,
`approvedUnscanned`) and `publicText` (`resolved`, `unresolved`, `occurrences`): counts
only, no digest, ID, path, repository, quote, source, policy path or matched text.
Exit 0 means satisfied; 1 means unresolved; 2 means invalid or incomplete, with empty
stdout. Raw status and exit stay honest: satisfied policy leaves `rawExit` 1.

Publisher integration is separate work: it must run the evaluator on its own push
set or review range and metadata, require `result.policy_satisfied` (never truthiness
of the result), and never format `result.facts` or raw coverage.
