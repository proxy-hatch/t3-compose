# Records commands

Use the [common invocation and global options](../cli.md). All commands use
`args.local_dir`; tests pass `--local-dir` explicitly before the command.
`--repo` means a normalized identity (`host/owner/name` or `synthetic/name`),
an HTTPS/SSH origin URL, or an existing checkout path whose origin is read
locally. HTTPS and SSH origins normalize to the same identity; host is lowercase.
Canonical identity syntax takes precedence over an ambiguous relative checkout
path; use `./` or an absolute checkout path to disambiguate. No network operation
is performed. No state is written into the checkout.

| Command | Required options | Optional options | Exit 0 JSON |
|---|---|---|---|
| `work new` | `--repo REPO --work ID --file WORK.json --writer W` | none | `{"work": <immutable metadata>, "events": [], "effectiveWork": <metadata>}` |
| `work show` | `--repo REPO --work ID` | none | `{"work": <metadata>, "events": [<ordered envelopes>], "effectiveWork": <scope-adjusted metadata>}` |
| `work list` | `--repo REPO` | none | `{"works": [<valid metadata>], "damaged": []}` |
| `work snapshot` | `--repo REPO --work ID` | `--through-seq N` | `{lastSeq, digest}` |
| `work reconcile` | `--repo REPO` | `--work ID` | `{works, damaged}` recovery report |
| `work repair` | `--repo REPO` | `--work ID` | same report with removed temporary names |
| `event add` | `--repo REPO --work ID --kind KIND --file DATA.json --writer W` | `--checkout PATH` | appended envelope, or the existing envelope for an exact intent retry |

`--file` holds a JSON object, not an envelope for `event add`. Work metadata
must match the normalized repository, work ID and writer options exactly.
Files are bounded to 1 MiB, strictly parsed and schema validated. Event sequence,
UTC time and helper commit are supplied by the helper; helper metadata comes
from `cli.helper_info()`. Unknown native facts use the schema's explicit unknown
objects where permitted. A branch is informational, never a result identity.

A valid new work succeeds once; a duplicate, mismatched identity or malformed
work exits 2 without overwriting metadata. Showing a valid work returns its
complete validated history; a missing, damaged or unsupported work exits 2.
Listing an absent repository returns empty arrays. Listing mixed valid/damaged
work returns the valid metadata plus `damaged` entries (`workId`, or null for an
unsafe directory name, and a sanitized `error`) with exit 2; other work remains
usable. A bad repository or unsafe repository path is a message-only exit 2.

Adding a valid supported event succeeds; malformed payload, incorrect writer,
bad history reference, duplicate live writer or exclusive sequence collision
exits 2. Collision never retries at a later sequence. An identical
`clientRequestId`/brief digest/full payload reuses the old intent without a new
event; conflicting reuse exits 2. Exact retries ignore `--checkout` and do not
recheck HEAD, path or start-object existence, even if the checkout changed or is
missing. For a new intent, the dispatching checkout HEAD
must equal `provisionedCommit`. `--checkout` additionally requires that exact
checkout path and an existing `startCommit` Git commit object. Using `--checkout`
for any other kind exits 2. Git reads use argument lists through `process.run`.

Expected failures have empty stdout and sanitized stderr; exception details,
input values and Git output are never echoed. Structured list/recovery reports
may also carry exit 2 with stdout. Handlers return data/`Outcome` or raise
`CommandError`; they do not print or exit. Exit codes are only 0 and 2.

Snapshot defaults to the whole validated history. `--through-seq` selects an
inclusive prefix ending at N, including zero for metadata only; negative,
noninteger or beyond-end values exit 2. This option is accepted only by snapshot,
which requires `--work`. Recompute a saved prefix after later appends using the
same N. Snapshot writes nothing and cannot certify an uncheckpointed history
against an external original; compare the returned digest with saved evidence.

Reconcile and repair scan the selected work or every work under the normalized
identity. An absent identity yields empty arrays; a missing selected work is a
structured damaged report with exit 2. The [recovery API](../records.md#reconciliation-and-repair-api)
defines every report field. Uncertain dispatches/operations and retained temporary
files are reported with exit 0: this is an inventory, not an acceptance decision.
Invalid works or temporary I/O errors yield a partial structured report and exit
2; inspect affected entries while continuing unrelated valid work. Repair reports
its proven removals in the returned JSON after unlink/fsync; handlers never print.
It neither clears dispatches nor edits/deletes events or work metadata.

On refusal: correct malformed input/options; reconcile T3 and Git facts before
retrying unresolved dispatches or transferring ownership; use the current writer;
retain commits and uncommitted artifacts. For checkpoint/history corruption,
preserve the record and compare saved evidence, never renumber or rewrite events.
For a sequence collision, reload and inspect the winning event before deciding a
new action; the helper does not retry. For retained temporaries, establish creator
termination and filesystem ownership/lock facts, then rerun repair; never infer
death from silence or permission failure. Partial creation requires manual
artifact reconciliation, not a second `work new` over the same reservation.

All 21 event kinds now undergo schema and history validation on append and load.
The [store contract](../records.md) defines checkpoint, transfer and CD-4 evidence
requirements. RC-1 dependents and Stage 2 remain gated until RC-1c merges after
lead review. No live or EV-1 Task verification decision is made here.
