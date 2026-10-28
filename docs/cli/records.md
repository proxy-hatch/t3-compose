# Records commands (RC-1b, part 2 of 3)

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
input values and Git output are never echoed. Structured damaged-work lists
are the sole exit-2 result with stdout. Handlers return data/`Outcome` or raise
`CommandError`; they do not print or exit. Exit codes are only 0 and 2.

The revised Engineering manager boundary of **2026-10-28** moves CD-4 history
(review sequence references, finding joins, author/current-lead provenance and
currency) to RC-1c. `review`, `finding-disposition`, `lead-assignment`, `restart`,
`handoff` and `writer-transfer` are refused with a diagnostic naming RC-1c,
including when found in stored history. Schema validation remains available.
`work snapshot`, `work reconcile` and `work repair` are not registered yet.
Dispatch reconciliation that proves stopped-and-preserved work is supported now;
see the [store contract](../records.md#durable-store-api-and-history).
RC-1 dependents and Stage 2 remain gated until RC-1c merges. No live or Task
verification decision is made by these commands.
