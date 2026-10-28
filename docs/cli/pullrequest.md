# Pull request verification

`t3c pr verify` reads a pull request once and checks its reviewed head, open
state, and any supplied base/author expectations. It applies to every
repository and implements the [publication head/identity rule](../superpowers/specs/2026-10-27-t3-compose-design.md#checks-and-publication).

```text
t3c [--local-dir PATH] [--text] pr verify --repo OWNER/NAME --number N --head SHA
    [--base BRANCH] [--author LOGIN]
```

Use the interpreter invocation in the [CLI contract](../cli.md). Global options
precede `pr`. Imports and registration perform no process or state I/O.

## Inputs, read and validation

`--repo` is a GitHub repository identity, never a checkout path. The owner is
1–100 ASCII letters/digits/hyphens starting with a letter or digit. The name
is 1–100 ASCII letters/digits/underscores/dots/hyphens starting with a letter,
digit or underscore. Exactly one slash separates them. `--number` is required,
an integer from 1 through 2,147,483,647. `--head` is required, exactly 40 hex
digits; SHA comparisons ignore hex letter case. These inputs are validated
before constructing an API endpoint.

`--base` and `--author` default to absent: the corresponding comparison is
skipped. Supplied values must be nonempty UTF-8 strings of at most 1,024 bytes,
without ASCII controls/DEL. Comparisons are exact and case-sensitive. Supply
the bot login (including `[bot]` where applicable) where repository identity
rules require an App author. No repository-specific login is hard-coded.

After parsing, every invocation freshly calls
`identifier_list.load_identifiers(args.local_dir)`, even though this command
returns no external text. Any list validation failure blocks the API read with
exit 2. The same PV-1a list bounds and grep validation described in
[privacy](privacy.md) apply; there is no alternate state path or list cache.

The injectable module function `read_pull_request(repo, number)` returns a
parsed API object. Its production implementation uses exactly one read-only
call, through `process.run`, with list argv:

```text
gh api --method GET repos/OWNER/NAME/pulls/N
```

It captures stdout/stderr, uses a 30-second timeout, and never invokes a shell,
pagination, mutations, credential retrieval or agent harnesses. `gh` uses its
existing authentication. Headers, tokens, environment values and API error
bodies never appear in results or diagnostics. Tests replace the reader or
patch the module's `process.run` lookup with `tests.support.FakeRunner`; they
never launch `gh`, even a stub.

API stdout must be UTF-8 JSON, at most 1,048,576 bytes after capture. Duplicate
keys, non-finite numbers, malformed JSON, unpaired surrogates, excessive nesting
and non-object responses are invalid. The parsed object, including injected
reader results, must also serialize as UTF-8 compact JSON within 1,048,576
bytes. This is validation after capture/serialization, not a streaming memory
bound. Extra API fields are permitted but are never returned.

Required shape: `head`, `base` and `user` are objects; `head.sha` is full
40-hex; `base.ref` and `user.login` are strings satisfying the expected-value
text bounds; `state` is `open` or `closed`. All fields are required and validated
even when optional comparisons are omitted. Closed/merged PRs refuse with
exit 1; this check never treats a merged PR as ready. The read is a snapshot,
not a lock against later PR changes or evidence that CI/review gates passed.

## Results and exits

Exit 0 means required and supplied comparisons passed:

```json
{"ok":true,"mismatches":[],"action":null}
```

Exit 1 means at least one valid field differs:

```json
{"ok":false,"mismatches":["head","state"],"action":"report to Shawn; not PR-ready"}
```

`mismatches` contains field names in `head`, `base`, `state`, `author` order.
Neither expected nor actual values, repository names, API contents or supplied
text are echoed. Global `--text` renders the same data via the shared CLI.
The caller reports the failure to Shawn; the helper sends no messages.

Exit 2 produces empty stdout and a safe stderr diagnostic for invalid inputs,
list failures, process/HTTP failures, invalid or oversized API/reader data and
reader exceptions. Exception display contexts are suppressed, including when
an injected reader raises a `CommandError`. None of those errors is a clean
verification. No other exit code is intentionally returned.

## Read-only post-merge probe

The Engineering manager may run the command on a known merged PR using its
actual head SHA and the repository's expected base and author. The expected
result is exit **1**, `state` in `mismatches`, and
`report to Shawn; not PR-ready`. It must not be changed to accept closed PRs
for that probe. Offline results are not live verification evidence.
