# t3c CLI

This spec describes the helper implemented by HS-1. The only built-in command
is `version`. Help is also available. The helper uses Python's standard
library. The
[implementation plan](superpowers/plans/2026-10-27-t3-compose-implementation.md#hs-1-helper-skeleton-and-offline-harness)
defines HS-1's scope. The [testing spec](testing.md) describes its offline
guard.

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

## Options, parsing and help

The root parser's program name is `t3c`. A command is required. Every parser
disables option abbreviation (`allow_abbrev=False`). Global options precede the
command:

| Option | Destination | Default | Effect |
|---|---|---|---|
| `--text` | `t3c_text` | `False` | Print structured output or help as text instead of JSON. |
| `--local-dir PATH` | `t3c_local_dir` | `<root>/.local` | Resolve an override path and expose the selected directory on `args.local_dir`. |
| `-h`, `--help` | help action | not requested | Print help for the selected parser and exit 0. |

Global options use reserved destinations so module options cannot collide with
them:

- `t3c_text` stores the root `--text` flag (`bool`, default `False`).
- `t3c_local_dir` stores the `--local-dir` `Path` (`t3compose.paths.local_dir()`,
  or `local_dir(override)` when supplied).
- `t3c_command` stores the selected root command or group name.
- `t3c_<group>_command` stores the selected leaf name in group `<group>`.
- `t3c_path` stores the command-path tuple (`tuple` of strings, such as
  `("version",)` or `("sample", "run")`).
- `local_dir` is assigned on the parsed `argparse.Namespace` after parsing
  from `args.t3c_local_dir`.

Command modules must not register an argument destination or `set_defaults` key
named `local_dir` or starting with `t3c_`. Registry-owned actions are identified
by identity, and registry-owned metadata defaults are recorded separately.
`set_defaults` rejects reserved keys even when a module uses the same value.
Argument actions also must not target `handler`; callable `handler` defaults
remain the required way to register leaves. A leaf may declare its own `--text`
option (for example with destination `text`) or child-argument destination
(such as `child_argv`), and leaf defaults cannot overwrite global state.

`--local-dir` does not create a directory or change the helper root. Explicit
overrides resolve relative to the caller's working directory and resolve
symlinks (`Path(override).resolve()`). The default `ROOT / ".local"` is
already absolute because `ROOT` is resolved, without an extra `resolve()` call
on `.local` itself. In offline test contexts, the default is a temporary
state directory instead (see [paths](#paths-and-json-utilities)). Handlers take
local state paths only from `args.local_dir`.
`version` accepts `--local-dir` as a global option but does not read `.local`.

The root parser, group parsers and leaf parsers have help. These invocations
succeed:

```sh
python3 -I -S <root>/bin/t3c --help
python3 -I -S <root>/bin/t3c version --help
python3 -I -S <root>/bin/t3c --text --help
python3 -I -S <root>/bin/t3c --text version --help
```

Help normally has the form `{"help":"<argparse help text>"}` on stdout.
Newlines inside the help string are JSON escapes. When the root `--text` global
option is parsed before the command, help prints the plain argparse help text
directly. There is no `--text` token pre-scan: only the actual parsed root
`--text` option selects text mode, including for subcommand help. A leaf
`--text` option or an option value equal to `"--text"` does not change the
helper's output mode. Passing global `--text` after the command name is invalid
unless that leaf command separately declares `--text`. Modules load before
arguments are parsed, so a broken module fails `--help` as well as `version`.

## Results, output and errors

Handlers return or raise one of four forms:

| Form | Meaning | Stdout | Stderr | Exit code |
|---|---|---|---|---|
| Plain JSON-compatible data | Success with data. | One JSON value (or text rendering with global `--text`). | Public warnings only, if any. | `ExitCode.SUCCESS` (`0`). |
| `Outcome(data, code=ExitCode.SUCCESS)` | Structured result with an explicit listed `ExitCode` (`0`, `1`, `2`, `3`, `4`, `5` or `75`). | Same rendering as plain data. | Public warnings only, if any. | `int(outcome.code)`. |
| `CommandError(message, code=ExitCode.INVALID_INPUT)` | Message-only expected failure with a listed nonzero `ExitCode`. | Empty. | `message`. | `int(error.code)`. |
| `PassThrough(status)` | Child streams already passed through unchanged; valid only for `("publish", "run")`. | Wrapper prints nothing. | Wrapper prints nothing. | Normalized `status` (`0`..`255`). |

`Outcome(data, code=ExitCode.SUCCESS)` validates `code` against `ExitCode` and
rejects booleans and unlisted integers with `ValueError`. A handler returning
`None` produces JSON `null` (and `null` in text mode). `Outcome` and plain
returns validate and encode the result with `canonical_json` before printing, so
unencodable data fails before anything is written to stdout.

With default JSON output, the helper decodes `canonical_json(data)` as UTF-8
and prints one trailing newline. There is no wrapper envelope. Object keys are
sorted, and JSON booleans are `true` and `false`.

With global `--text`, a `dict` result prints one `key: <json-value>` line per
item in insertion order. Keys use `json.dumps(str(key), ensure_ascii=True)`
without its surrounding quotes; control characters, quotes, backslashes and
non-ASCII characters are escaped. Simple labels such as `root` stay readable.
Values use `canonical_json(value).decode("utf-8")`. Escaped keys and values
cannot insert extra physical output lines. An empty dictionary prints an empty
line (`""` followed by the trailing
newline from `print`). Any non-`dict` value (such as a list, string, number,
boolean or `None`) prints its single `canonical_json(data).decode("utf-8")`
line. Plain returns and `Outcome` share this exact rendering path.

`CommandError(message, code=ExitCode.INVALID_INPUT)` requires a listed nonzero
`ExitCode` (`1`, `2`, `3`, `4`, `5` or `75`); it rejects `0` (`ExitCode.SUCCESS`),
booleans and unknown codes with `ValueError`. Its message must be public
diagnostic text, never supplied argument values, file contents, secrets or
raw backend exception messages.

`PassThrough(status)` represents a child process whose stdout and stderr were
already streamed directly. It rejects booleans and requires an integer status:
negative values `-signal` (with `1 <= signal <= 127`) normalize to
`128 + signal`, and nonnegative values must be in `0..255`; other values or
types raise `ValueError`. `main` accepts `PassThrough` only when the executed
command's declared path is `("publish", "run")`, and returns the normalized
integer status without writing to stdout or stderr. If any other command returns
`PassThrough`, `main` treats it as an unexpected helper failure (exit 2 with
empty stdout and the sanitized unexpected-failure diagnostic on stderr). Later
command specs define `publish run` (including its required `--status-file`
contract and token revocation) and `heavy run` (which returns structured JSON
summaries rather than `PassThrough`); HS-1 only provides the `cli` result type
and dispatch rule.

Registered handlers must never call `sys.exit` or write to stdout directly.
Handlers may write safe public warnings to stderr (for example when a cleanup
step after a child process fails). Outside module loading, `main` does not
catch `SystemExit` or `KeyboardInterrupt`.

Diagnostics go to stderr, and handled failures write nothing to stdout:

- **Parse errors**: `Parser.error` raises `CommandError` with exit 2 and
  `invalid arguments for <prog>; use --help`, where `<prog>` is the parser's
  declared public program path (such as `t3c`, `t3c version` or
  `t3c sample run`). It never echoes raw supplied argument values.
- **Unexpected helper failures**: Any uncaught `Exception` during argument
  parsing, handler execution or output encoding is caught by `main`, which
  prints `<command-path>: command failed (<ExceptionClass>); check local inputs and helper installation`
  to stderr and returns `ExitCode.INVALID_INPUT` (`2`). `<command-path>` comes
  from the parser or registry declarations (such as `t3c version` or
  `t3c sample run`, or `t3c` when no subcommand was selected), never from raw
  `argv` values. `<ExceptionClass>` is `type(error).__name__`. Neither
  `str(error)`, `repr(error)`, exception arguments nor tracebacks are printed.

## Exit codes

| Code | Enum member | Meaning | Emitted by HS-1 commands today |
|---|---|---|---|
| 0 | `SUCCESS` | Success. | Successful `version`; argparse help also exits 0. |
| 1 | `CHECK_FAILED` | Check failed: blocked or not verified. | No (supported via `Outcome` and `CommandError`). |
| 2 | `INVALID_INPUT` | Invalid input, records or unexpected helper failure. | Invocation refusal, parser errors, module load errors and caught unexpected exceptions. |
| 3 | `INVALID_CONFIG` | Invalid config; routing is blocked. | No (supported via `Outcome` and `CommandError`). |
| 4 | `JUDGMENT_NEEDED` | Engineering manager judgment needed. | No (supported via `Outcome` and `CommandError`). |
| 5 | `REPORT` | Report to Shawn. | No (supported via `Outcome` and `CommandError`). |
| 75 | `BUSY` | Busy. | No (supported via `Outcome` and `CommandError`). |

Only `publish run` may exit with an arbitrary child status (`0`..`255`) via
`PassThrough`. All other commands exit with a listed `ExitCode`.

## version and helper_info

```sh
python3 -I -S <root>/bin/t3c version
```

`t3compose.cli.helper_info()` returns a new dictionary with the helper's
checkout and interpreter facts, and `version(args)` delegates directly to
`helper_info()`. Its JSON has exactly these fields, in this serialized order:

```json
{"commit":"<HEAD SHA>","dirty":false,"python":"<Python version>","root":"<resolved checkout root>"}
```

| Field | Type | Computation |
|---|---|---|
| `commit` | string | Stripped stdout from `git -C ROOT rev-parse HEAD`. |
| `dirty` | boolean | Whether stripped stdout from `git -C ROOT --no-optional-locks status --porcelain` is nonempty. Untracked files make it true; tracked-only cleanliness checks in later commands are separate. |
| `python` | string | `platform.python_version()` for the running interpreter. |
| `root` | string | `str(t3compose.paths.ROOT)`. |

Git is found through PATH. Both reads call `process.run` through the module,
require success (`check=True`) and capture stdout and stderr as text. Passing `--no-optional-locks` keeps
`status` read-only so checking `version` or `helper_info()` does not refresh or
rewrite `.git/index`. Captured Git diagnostics are not forwarded; a Git failure
raises `ProcessError`, which `main` sanitizes as an unexpected helper
failure (`t3c version: command failed (ProcessError); ...`, exit 2).
The text result uses the dictionary insertion order: `root`, `commit`, `dirty`,
`python`.

## Command modules, parser construction and command listing

`t3compose.cli.MODULES` fixes this load order:

```text
config, records, evidence, jev, routing, quota, privacy, pullrequest,
github_app, token_run, publish, heavy, briefs, skill_install
```

`build_parser(text_output=False)` constructs the root `Parser`, registers the
built-in `version` command, creates the `Registry`, loads all present modules
from `MODULES`, validates every registered group and leaf parser, and returns
the root `Parser`. `command_names()` uses the same construction/registration
path and returns a sorted tuple of command-path tuples for all executable leaf
commands, such as `(("sample", "run"), ("version",))`. It includes executable
leaves only, never group parsers or help, and never scrapes help text.

For each module name in `MODULES`, `Registry.load()` checks
`importlib.util.find_spec("t3compose." + name)`. A `None` result skips that
module. Found modules are imported and their `register(registry)` function is
called once per parser build. Module imports and `register(registry)` must be
cheap, deterministic, compatible with Python 3.9+, and free of side effects:
they must not read configuration or state files, start subprocesses, write
files or perform network/external calls. Every invocation (including `version`
and `--help`) loads all present modules.

`Registry` enforces deterministic registration checks across Python 3.9 and
later interpreters:

- Duplicate root command names, duplicate leaf names within a group, a group
  colliding with `version` (or an existing leaf), or a root leaf colliding with
  an existing group are rejected. Command aliases are not supported: passing
  the `aliases` keyword to `add_parser` is rejected at root and group levels.
  Each executable parser has exactly one canonical command path.
- Every leaf parser must set a callable `handler` default (`callable(handler)`).
  Missing or noncallable handlers are rejected when the registry validates
  registration.
- No root, group or leaf parser may register an argument destination or
  `set_defaults` key named `local_dir` or prefixed with `t3c_`. Only recorded
  registry-owned action objects and metadata defaults are exempt; matching a
  reserved name does not grant an exemption. Argument actions targeting
  `handler` are rejected at every level, while callable handler defaults are
  supported.

Any `Exception` or `SystemExit` during module lookup, import, `register` or
registration validation becomes `CommandError("cannot load command module: <name>")`
(exit 2). Exception details are suppressed. `KeyboardInterrupt` propagates.

Shared groups allow multiple modules to add leaves under one root group name:

```python
def register(registry):
    parser = registry.group("route").add_parser("select")
    parser.add_argument("--work", required=True)
    parser.set_defaults(handler=select_route)
```

Calling `registry.group("route")` returns the shared subparser collection for
`route`, creating the group on first use. Calling `registry.commands.add_parser(name)`
adds a top-level leaf command.

When a command accepts a child command line after `--` (such as
`t3c publish run ... -- CMD [ARGS...]` or `t3c heavy run ... -- CMD [ARGS...]`),
the leaf parser can capture the child arguments with `argparse.REMAINDER` into a
non-reserved destination such as `child_argv`:

```python
def register(registry):
    parser = registry.group("publish").add_parser("run")
    parser.add_argument("--status-file", required=True)
    parser.add_argument("child_argv", nargs=argparse.REMAINDER)
    parser.set_defaults(handler=run_with_token)
```

`argparse.REMAINDER` preserves all remaining tokens without losing `--text` or
option-like child flags, and it retains the leading `"--"` token at index 0 of
`args.child_argv` when `"--"` is supplied. The command module is responsible for
requiring and stripping that leading `"--"` separator before launching the
child command.

Shared core files (`t3compose/cli.py`, `t3compose/jsonio.py`,
`t3compose/paths.py`, `t3compose/process.py` and `tests/support.py`) and the
`MODULES` tuple are modified only when a Task brief explicitly assigns that
edit. Each command module documents its own commands, options, output and exit
codes in `docs/cli/<module>.md`; this file specifies what all commands share.

## Shared option contracts for later modules

The future publication privacy command uses `--text-file LABEL=FILE` for text
inputs, leaving global `--text` as output mode. `--repo` is contextual: work,
event, routing and evidence commands use a repository identity or checkout
path; `pr verify` uses GitHub `OWNER/NAME`. Each module spec defines its
command's interpretation. These are extension contracts, not commands that
HS-1 implements.

## Process API

`t3compose.process.run(argv, **kwargs) -> subprocess.CompletedProcess` is the
shared standard-library launch seam. `argv` must be a nonempty list of
filesystem arguments. Strings, bytes, other containers, empty lists and shell
execution are refused before launch. It forwards normal `subprocess.run`
controls including `check`, `capture_output`, `text`, `input`, `cwd`, `env`
and `timeout`; `shell=False` is accepted. No shell is used. Modules call
`process.run` through the module, not a captured function import.

`ProcessError(kind, returncode=None)` carries a public kind and optional child
return code. The wrapper uses `invalid-command`, `exit`, `not-found`,
`timeout`, `os-error` and `invalid-options`. Its message and representation
contain no argv, environment, captured output or backend exception message;
underlying exception display is suppressed. A nonzero child with `check=True`
raises kind `exit`; with `check=False` its `CompletedProcess` is returned.
Missing executables, timeouts, other OS errors and invalid subprocess options
are sanitized. Successful captured stdout and stderr are returned unchanged,
never automatically logged. Callers remain responsible for handling those
values privately and for public diagnostics. Guard `AssertionError` refusals
are not converted into process failures.

`helper_command(*args)` constructs, without launching:
`[sys.executable, '-I', '-S', '-B', str(ROOT / 'bin/t3c'), *args]`.
It preserves the real helper root and current interpreter. Call it through the
`process` module so tests can substitute the guarded bootstrap form; see the
[testing spec](testing.md#process-fakes-and-helper-relaunches). This seam adds
no agent runtime, publication command or heavy-check executor.

## Paths and JSON utilities

`t3compose.paths.ROOT` is `Path(__file__).resolve().parents[1]` for `paths.py`.
It refers to the helper's real checkout, independent of the caller's directory.
`local_dir()` normally returns `ROOT / ".local"`; the offline harness sets
`paths._default_local_dir` to a temporary `Path` for its context only. There
is no production environment-variable override. `local_dir(override)` returns
`Path(override).resolve()`. These functions only compute paths.

`t3compose.jsonio.canonical_json(value)` returns UTF-8 bytes. It uses
`json.dumps` with `sort_keys=True`, separators `(',', ':')`,
`ensure_ascii=False` and `allow_nan=False`. It adds no newline. Non-ASCII
characters remain literal UTF-8. NaN and infinities are refused. Values that
cannot be JSON-serialized or UTF-8-encoded raise the encoder's exception.

`file_digest(path)` returns the bare lowercase SHA-256 hex digest of the file's
raw bytes. It reads in 65,536-byte blocks. It does not parse or canonicalize
JSON. File access errors propagate.

`read_json_with_digest(path, max_bytes=16384)` opens `path` once in binary mode,
reads at most `max_bytes + 1` bytes, validates and parses the JSON payload, and
returns `(parsed_value, "sha256:<lowercase hex>")`. The digest is computed over
the exact raw bytes read from that single open, not over re-encoded canonical
JSON, so formatting and whitespace differences produce different digests and a
file replaced after the read cannot mismatch the validated value.

`read_json(path, max_bytes=16384)` shares the same bounded single-read and
validation implementation and returns only `parsed_value`. The default limit for
both functions is 16 KiB (`16384` bytes), and a file of exactly `max_bytes`
bytes is allowed. Neither function requires a top-level JSON object or validates
a higher-level schema.

To ensure deterministic behavior across Python 3.9 and Python 3.14+ without
changing the process-wide Python integer string conversion limit, both JSON read
functions reject any JSON integer token whose decimal digit count (excluding an
optional leading `-` sign) exceeds 4,300 digits before converting it to `int`.

| Rejection | Result |
|---|---|
| Negative limit | `ValueError("invalid JSON read limit")`, before opening the file. |
| More bytes than the limit | `ValueError("JSON read limit exceeded")`, before parsing. |
| Invalid UTF-8 or malformed JSON, including empty input and trailing non-whitespace data | `ValueError("invalid JSON data")`. |
| Integer token with more than 4,300 decimal digits (sign excluded) | `ValueError("invalid JSON data")`, before `int` conversion. |
| Duplicate object key at any depth | `ValueError("invalid JSON data")`. |
| `NaN`, `Infinity` or `-Infinity` | `ValueError("invalid JSON data")`. |
| A decoded float that is not finite, including `1e400` or `-1e400` | `ValueError("invalid JSON data")`. |
| A string or key that cannot be UTF-8-encoded, including an unpaired surrogate at any depth | `ValueError("invalid JSON data")`. |

The parse and encoding failures above suppress their underlying exception
details. File open and read errors propagate as `OSError`. Other failures,
such as invalid limit types or recursion limits, are not normalized by these
functions. These utilities perform no persistence writes.
