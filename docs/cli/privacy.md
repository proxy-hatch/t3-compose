# Publication privacy

`t3c privacy check` screens publishable text in any repository against the
private identifier list. It is a read-only check, not permission to publish.
It implements the [workflow's privacy rule](../superpowers/specs/2026-10-27-t3-compose-design.md#checks-and-publication).

```text
t3c [--local-dir PATH] [--text] privacy check --repo-path P --range BASE..HEAD
    [--branch NAME] [--text-file LABEL=FILE ...]
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
- `--branch` defaults to the current symbolic branch name. Detached HEAD
  requires an explicit publication branch. The supplied name must be nonempty
  supported text; it need not exist locally and this check does not validate
  its suitability for a later push.
- Repeated `--text-file` values split at the first `=` into nonempty label and
  path. Default: no files. Duplicate labels are allowed and preserve order.
  Each path must open as a regular file (a symlink to a regular text file is
  allowed). A label is also screened. Paths to these external files are never
  returned; labels identify title/body or other supplied text.

After parsing, every invocation freshly loads the list using
`identifier_list.load_identifiers(args.local_dir)`. No personal state path is
hard-coded. Missing, empty, unreadable, malformed or oversized lists block the
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
Regular and executable files and symlink blobs are supported. Symlink blobs
are read from Git without following targets. Submodules and other unsupported
tree entries fail closed. Shallow repositories are refused. Replacement
objects, grafts, ambient `GIT_*` selectors, optional lock writes, lazy fetches
and transport protocols are disabled for these reads. No hooks, filters,
textconv, diff tools, working-tree content substitutes or remote reads run.

## Supported data and failure behavior

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
with sorted 1-based **identifier-list** line numbers, never input line numbers
or matched text. `field` is `branch`, `label`, `text`, `message`, `path` or
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
