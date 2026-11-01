# Brief commands

The [shared CLI contract](../cli.md) defines invocation, global options and
safe diagnostics. Resolve final CPython 3.15 with `PY="$(uv python find 3.15)"`.
Here `t3c` means `"$PY" -I -S <root>/bin/t3c`.

```text
t3c brief render --template worker|reviewer|reviewer-prepass|PATH --values VALUES.json --out FILE
t3c brief check FILE
```

Both commands return `{"digest":"sha256:<64 lowercase hex>"}` on success
(exit 0). The digest covers the exact UTF-8 artifact bytes, including trailing
newlines, for a dispatch-intent record. Neither command dispatches an agent,
changes Git state, evaluates code, reads credentials or selects a route.

## Rendering and values

`worker`, `reviewer` and `reviewer-prepass` select files under `<root>/skill/templates/`.
Any other `--template` value is a file path, relative to the caller's current
working directory or absolute. Values and output paths follow the same rule.
Parents of `--out` must already exist. Output is raw Markdown, never JSON
encoded Markdown. Repeated `{{NAME}}` tokens receive the same literal value;
values are never recursively expanded. Names match `[A-Z][A-Z0-9_]*`.
Missing values, leftover `{{` or `}}`, and malformed placeholders fail.

VALUES.json is a bounded JSON object of string values. Duplicate keys,
non-string values, invalid names and invalid UTF-8 fail. Extra named strings
are allowed and screened even when unused. Template, raw values JSON, decoded
values and expanded output are privacy-screened. Each read and expanded text
is limited to 1 MiB. Files must be regular files. NUL, control characters other
than LF/tab, DEL/C1 controls, and Unicode line/paragraph separators fail.

Built-in values (all required unless noted):

| Applies to | Names |
|---|---|
| Both | `STAGE`, `PROVIDER`, `MODEL`, `REASON`, `REPOSITORY`, `PROVISIONED`, `START`, `REVIEW_BASE`, `ROUND` |
| Both | `READ_FIRST`, `ROUTING_POLICY`, `SETTINGS_CHECK` |
| Worker | `HOLDS`, `OWNED_PATHS`, `OBJECTIVE`, `INTERFACES`, `ACCEPTANCE`, `TEST_FIRST` |
| Worker | `FAST_CHECKS`, `HEAVY_CHECKS`, `ATTRIBUTION` |
| Reviewer | `REVIEW_HEAD`, `CONTEXT`, `QUESTIONS`, `CHECKS` |
| Optional | `INLINE_INSTRUCTIONS` (both roles, default empty) |
| Worker optional | `PRIVACY_FINDINGS`, `UNPUBLISHED_RANGE` (both default `none`) |

`STAGE` is the matrix stage of the dispatch, `1`, `2` or `3`. Use explicit prose
such as “None assigned” where a required check or hold does not apply. Supply
repository guides, approved designs, dependencies and other necessary context
in the relevant slots. The routing policy slot identifies private instructions
supplied by the coordinator; source templates contain no personal policy.
`PROVIDER` is the generic harness family `agy`, `pi`, `claude` or `codex`, never
an account or provider-instance name. Real routing selection and account
identifiers remain in private records. `MODEL` is a public model label; this
command does not check the live catalog or infer provider family. The caller
must supply the actual selected family.

For agy and Pi Workers and Reviewers, `INLINE_INSTRUCTIONS` must be nonempty and
carry all applicable rules and required outside content inline. An inaccessible
path is not a substitute: agy's file tool cannot read outside its worktree.
Content adequacy needs coordinator review; the checker enforces presence, not
meaning. Any provider family may review; the routing records, not this command,
decide who is eligible.

External templates can use any named string slots and any layout. Plain
external templates are rendered without imposing Worker/Reviewer sections.
They cannot be passed to `check`. An external template beginning exactly
`# t3c brief v1` followed by LF opts into the structured contract below,
including the generated privacy section and structural-injection restrictions.

## Structured check contract

`check` supports only version-1 Worker and Reviewer artifacts. It reads without
rewriting or normalizing bytes. The first line is `# t3c brief v1`, followed by
one `Field: value` per metadata field and a blank line. Required fields are
`Role`, `Stage`, `Provider`, `Model`, `Reason`, `Repository`, `Provisioned`,
`Start`, `Review-base`, `Round`, `Privacy-findings`, `Unpublished-range`, plus
`Review-head` and `Mode` for Reviewer only. `Stage` values are `1`, `2` or `3`.
Unknown/duplicate fields fail. Values are nonempty single lines of at most 512
characters. Commit values are exactly 40 hexadecimal characters; this is format
validation, not object existence. Worker rounds are `implementation` or
`corrections`; Reviewer rounds are `earlier-review`, `corrections-review` or
`final-review`. A non-critical Task's lead reviews its own final head, so no
Reviewer brief exists for it, while a core Task's final head, and any Task
without a lead, gets a `final-review` brief.

Required `##` sections must be nonempty and appear exactly once, in the order
in the corresponding [Worker](../../skill/templates/worker.md),
[Reviewer](../../skill/templates/reviewer.md) or
[Reviewer pre-pass](../../skill/templates/reviewer-prepass.md) source. The
Reviewer sections now include `Review mode`. Every routing section (Worker and
Reviewer) has exactly one `Inline-instructions:` slot; it must contain nonempty
text for agy/Pi. Metadata-like lines, extra headings and duplicate structural
markers inside section content fail. Structured substitution values cannot
introduce headings or reserved field/marker lines (including indentation or case
changes). These checks prevent multiline slots from silently changing role,
provider or commits. They do not establish that arbitrary natural-language
instructions are correct, or create a security boundary.

The repository section must contain this exact contiguous move-rule block:

```text
Move-rule: three-case-v1
1. HEAD=start => continue.
2. HEAD=provisioned AND clean AND start exists AND fresh own branch => move own branch to start only.
3. Otherwise OR missing start => stop; never fetch.
```

Malformed rules fail even with three full hashes. Workers apply the rule only
to their fresh own branch; Reviewers stop for coordinator reprovisioning if a
move is required. Neither command itself moves a branch. Reading reviewed
artifacts uses exact commits; checks on a checkout need that exact review head.

## Reviewer modes

A Reviewer brief sets the `Mode` header to `record` (using the `reviewer`
template) or `pre-pass` (using `reviewer-prepass`). `check` verifies that the
`## Review mode` section contains the exact corresponding rule block:

```text
Review-pass: record-v1
1. You are the review of record: your verdict is the one the lead relies on.
2. You have not seen any pre-pass finding and must not ask for one. Pre-pass findings carry IDs of the form PP-<number>. If one appears anywhere in your context, stop and report it to the coordinator.
3. In the final-review round, review the Task's exact final head: Review-head is the head the lead will merge, and your verdict covers that head only.
```

or:

```text
Review-pass: advisory-v1
1. This is an advisory pre-pass. It is never the review of record and never replaces it.
2. Label every finding with an ID of the form PP-<number>: PP-1, PP-2 and so on.
3. Return exactly one verdict: `blockers found` or `no blockers found`. Never return approve, approve after fixes or changes required.
4. The lead may send blocking findings to a Corrections round. The reviewer of record never sees your findings.
```

The pre-pass is advisory: findings use IDs `PP-<number>`, verdict is `blockers found`
or `no blockers found`, and it cannot be used for `final-review`. A review-of-record
brief is refused if any `PP-<number>` ID appears anywhere in it; in `final-review`,
its `Review-head` is the head the lead will merge. These are format checks: a
pre-pass finding with another ID format would not be caught.

## Bounded privacy Corrections

Ordinary Worker output has no rewrite-authorization section. The caller cannot
supply `PRIVACY_CORRECTIONS`: the renderer generates it only for a Worker with
`ROUND=corrections`, enumerated `PRIVACY_FINDINGS` and `UNPUBLISHED_RANGE` exactly
`REVIEW_BASE..START`, with distinct endpoints. Finding IDs follow `PV-1` style:
uppercase alphanumeric prefix beginning with a letter, hyphen, positive integer;
multiple IDs are comma-separated without spaces. Both scope fields must be
`none` when no exception applies. A Reviewer cannot request the exception.
The generated section permits amending/rebasing only unpublished commits in
that range to remove those matches. `check` requires the exact generated text.
The coordinator must establish that the range is unpublished and the findings
are real; the helper cannot prove either fact from a Markdown artifact.

## Privacy, failure and publication

Each invocation freshly loads `state/private-identifiers.txt` under the global
`--local-dir` (default `<root>/.local`) with the shared PV-1a loader and matcher.
A missing/invalid list, matcher failure or match refuses the operation. No
matched text, pattern, private value or supplied path appears in diagnostics.
All validation/privacy failures exit 2 with empty stdout and a safe stderr
message. Invalid files, unsupported structures and write failures also exit 2.

Rendering validates and screens before publishing. A same-directory mode-0600
temporary file is flushed and fsynced, atomically replaces `--out`, then its
parent directory is fsynced. Pre-publication failures preserve an existing
output and remove the temporary file. A directory-fsync failure after replace
can leave the new artifact present despite exit 2; reconcile it with `check`.
No brief or helper result grants review approval, publication or merge rights.
