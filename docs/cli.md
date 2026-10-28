# t3c CLI

This spec describes the helper implemented by HS-1. The only command is
`version`. Help is also available. The helper uses Python's standard library.
The [implementation plan](superpowers/plans/2026-10-27-t3-compose-implementation.md#hs-1-helper-skeleton-and-offline-harness)
defines HS-1's scope. The [testing spec](testing.md) describes its offline guard.

## Invocation and root

Run the entry point with Python 3.9 or later:

```sh
python3 -I -S <root>/bin/t3c version
```

`bin/t3c` checks `sys.version_info >= (3, 9)`, `sys.flags.isolated` and
`sys.flags.no_site`. Both `-I` and `-S` are required. Isolation excludes the
usual environment and user path influences; `-S` disables site initialization.
Calling the executable through its shebang alone does not supply these flags.
An old interpreter or either missing flag causes exit 2. Stdout is empty.
Stderr contains this line:

```text
Python 3.9+ with isolation and no site required; run: <python> -I -S <entry> version
```

`<python>` is `sys.executable`. `<entry>` is the resolved path of `bin/t3c`.
Each is shell-quoted with `shlex.quote`. The suggestion always ends in `version`.

After the checks, the entry point sets `sys.dont_write_bytecode = True`.
An additional `-B` is not required for the helper. It resolves its own path,
takes the parent of `bin/` as the root, and prepends that root to `sys.path`.
It imports `t3compose.cli.main` and exits with its result. A symlink to the
entry point uses the target checkout. The caller's working directory does not
select the checkout.

## Options and help

The parser's program name is `t3c`. A command is required. Global options
precede the command:

| Option | Default | Effect |
|---|---|---|
| `--text` | false | Print successful data as text instead of JSON. |
| `--local-dir PATH` | `<root>/.local` | Pass a resolved `Path` as `args.local_dir`. This option supports tests. |
| `-h`, `--help` | not requested | Print help for the selected parser and exit 0. |

`--local-dir` does not create a directory or change the helper root. Relative
overrides resolve from the caller's working directory. Symlinks are resolved.
`version` accepts this global option but does not use the local directory.

The root parser and the `version` parser have help. These invocations succeed:

```sh
python3 -I -S <root>/bin/t3c --help
python3 -I -S <root>/bin/t3c version --help
python3 -I -S <root>/bin/t3c --text --help
```

Help normally has the form `{"help":"<argparse help text>"}` on stdout.
Newlines inside the help string are JSON escapes. Text mode prints the help
text directly. Help mode is chosen by the presence of the exact token
`--text` anywhere in the argument list before parsing. This pre-scan also
sets the mode of subparsers. It does not make global options valid after a
command in ordinary command execution. Modules load before arguments are
parsed, so a broken module can prevent help as well as `version`.

## Output and errors

A successful handler's return value is the entire JSON value on stdout.
There is no envelope. The helper uses `canonical_json`, decodes its bytes as
UTF-8, and prints one trailing newline. Object keys are sorted. JSON booleans
are `true` and `false`.

With `--text`, a dictionary becomes one `key: value` line per item in its
insertion order. Values use Python's string conversion, including `True` and
`False`. Any other return value uses `str(data)`. The canonical JSON encoding
still runs first, so text mode also requires JSON-encodable data.

Diagnostics go to stderr. Handled failures print no result on stdout. The
parser replaces its normal argument error with `invalid arguments; use --help`.
It does not echo supplied argument values. Unexpected exceptions caught by
`main` produce `command failed; check local inputs and helper installation`
and exit 2. This includes Git failures in `version` and output encoding errors.
Raw exception details and tracebacks are not printed for these handled failures.

The output contract excludes secrets from both streams. A `CommandError`
message must contain public text, never raw input or a backend exception.
`main` prints that message verbatim; it is not a general secret filter.
Root paths are intentional output in `version` and the invocation refusal.

## Exit codes

| Code | Enum member | Meaning | Emitted by HS-1 commands today |
|---|---|---|---|
| 0 | `SUCCESS` | Success. | Successful `version`; argparse help also exits 0. |
| 1 | `CHECK_FAILED` | Check failed: blocked or not verified. | No. |
| 2 | `INVALID_INPUT` | Invalid input or records. | Invocation refusal, parser errors, module load errors and caught unexpected exceptions. |
| 3 | `INVALID_CONFIG` | Invalid config; routing is blocked. | No. |
| 4 | `JUDGMENT_NEEDED` | Engineering manager judgment needed. | No. |
| 5 | `REPORT` | Report to Shawn. | No. |
| 75 | `BUSY` | Busy. | No. |

`CommandError(message, code=ExitCode.INVALID_INPUT)` converts its code through
`ExitCode`. Registered handlers can use any listed code. The tests inject a
command to exercise them; HS-1 does not implement those later command behaviors.

## version

```sh
python3 -I -S <root>/bin/t3c version
```

`version` has no command-specific options other than help. Its JSON has exactly
these fields, in this serialized order:

```json
{"commit":"<HEAD SHA>","dirty":false,"python":"<Python version>","root":"<resolved checkout root>"}
```

| Field | Type | Computation |
|---|---|---|
| `commit` | string | Stripped stdout from `git -C ROOT rev-parse HEAD`. |
| `dirty` | boolean | Whether stripped stdout from `git -C ROOT status --porcelain` is nonempty. Untracked files can make it true. |
| `python` | string | `platform.python_version()` for the running interpreter. |
| `root` | string | `str(t3compose.paths.ROOT)`. |

Git is found through PATH. Both subprocess calls require success and capture
stdout and stderr as text. Captured Git diagnostics are not forwarded.
The text result uses the handler's order: `root`, `commit`, `dirty`, `python`.

## Command modules

`t3compose.cli.MODULES` fixes this load order:

```text
config, records, evidence, jev, routing, quota, privacy, pullrequest,
github_app, token_run, publish, heavy, briefs, skill_install
```

The registry first installs the built-in `version` command. For each listed
name it looks for `t3compose.<name>` with `importlib.util.find_spec`. A `None`
result skips that module. These optional modules are absent at this head.
A found module is imported and its `register(registry)` is called once per
registry load. Python's import cache still applies.

Any `Exception` or `SystemExit` during lookup, import or registration becomes
`cannot load command module: <name>` and exit 2. A missing dependency, syntax
error, missing `register`, or failed registration is an error, not an absent
module. Exception details are suppressed. `KeyboardInterrupt` propagates;
neither the loader nor `main` catches it. Outside module loading, `main` also
does not catch `SystemExit`; argparse uses it for help.

`registry.commands` is the root's required subparser collection. It stores the
selected name in `args.command`. `registry.group(name)` creates a root command
group on first use and returns its required subparser collection. Later calls
with the same name return that same collection. The selected leaf name goes
in `args.<name>_command`. This lets separate modules extend one group.

Each leaf parser sets its handler:

```python
def register(registry):
    parser = registry.group("sample").add_parser("show")
    parser.set_defaults(handler=show)

def show(args):
    return {"status": "ready"}
```

`main(argv=None)` takes `sys.argv[1:]` by default, or a supplied argument
sequence. It parses once, calls `args.handler(args)`, prints the returned data,
and returns `ExitCode.SUCCESS`. A handler returns JSON-compatible data on
success. It raises `CommandError` with a public message and a listed exit code
for an expected failure. Modules must honor the output contract above.

## Paths and JSON utilities

`t3compose.paths.ROOT` is `Path(__file__).resolve().parents[1]` for `paths.py`.
It refers to the helper's real checkout, independent of the caller's directory.
`local_dir()` returns `ROOT / ".local"`. `local_dir(override)` returns
`Path(override).resolve()`. These functions only compute paths.

`t3compose.jsonio.canonical_json(value)` returns UTF-8 bytes. It uses
`json.dumps` with `sort_keys=True`, separators `(',', ':')`,
`ensure_ascii=False` and `allow_nan=False`. It adds no newline. Non-ASCII
characters remain literal UTF-8. NaN and infinities are refused. Values that
cannot be JSON-serialized or UTF-8-encoded raise the encoder's exception.

`file_digest(path)` returns the lowercase SHA-256 hex digest of the file's raw
bytes. It reads in 65,536-byte blocks. It does not parse or canonicalize JSON.
File access errors propagate.

`read_json(path, max_bytes=16384)` reads at most `max_bytes + 1` bytes in binary
mode. The default limit is 16 KiB. Exactly the limit is allowed. It decodes
UTF-8, parses JSON, checks that the result can be canonically encoded, and
returns the parsed value. It does not require an object or validate a schema.

| Rejection | Result |
|---|---|
| Negative limit | `ValueError("invalid JSON read limit")`, before opening the file. |
| More bytes than the limit | `ValueError("JSON read limit exceeded")`, before parsing. |
| Invalid UTF-8 or malformed JSON, including empty input and trailing non-whitespace data | `ValueError("invalid JSON data")`. |
| Duplicate object key at any depth | `ValueError("invalid JSON data")`. |
| `NaN`, `Infinity` or `-Infinity` | `ValueError("invalid JSON data")`. |
| A decoded float that is not finite, including `1e400` or `-1e400` | `ValueError("invalid JSON data")`. |
| A string or key that cannot be UTF-8-encoded, including an unpaired surrogate at any depth | `ValueError("invalid JSON data")`. |

The parse and encoding failures above suppress their underlying exception
details. File open and read errors propagate as `OSError`. Other failures,
such as invalid limit types or recursion limits, are not normalized by this
function. These utilities perform no persistence writes.
