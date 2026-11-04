# Publication privacy

`t3c privacy check` screens publishable text in any repository against the
private identifier list. It is a read-only check, not permission to publish.
It implements the [workflow's privacy rule](../superpowers/specs/2026-10-27-t3-compose-design.md#checks-and-publication).

```text
t3c [--local-dir PATH] [--text] privacy check --repo-path P --range BASE..HEAD
    [--branch NAME] [--text-file LABEL=FILE ...] [--privacy-policy FILE]
```

Use the interpreter invocation in the [CLI contract](../cli.md). Global options
precede `privacy`. Imports and registration perform no process or state I/O.

## Inputs and selection

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

Public-text disposition evaluation and publisher integration remain deferred.
Neither is waived or satisfied by this scanner.


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
Future evaluator B must rerun the shared core and validate its own scope/evidence;
publisher C must re-observe current authority and exact publication inputs at
the gate. Cooperative source provenance is not authentication; the observed
prefix provides no atomic revocation guarantee after observation. Strict
publisher behavior remains unchanged until those separate integrations land.
