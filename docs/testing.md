# Offline test harness

This spec describes HS-1's `tests/support.py` and test conventions. The
[CLI spec](cli.md) describes the helper. Tests use the standard library.

## Suite and labels

`SUITE` means this command, run from the repository root:

```sh
python3 -I -S -B -m unittest discover -s tests -t . -v
```

Run it under both interpreters:

```sh
/usr/bin/python3 -I -S -B -m unittest discover -s tests -t . -v
/opt/homebrew/bin/python3 -I -S -B -m unittest discover -s tests -t . -v
```

The supported floor is Python 3.9. `SUITE -p 'test_x.py'` selects one file.
`-B` disables bytecode writes from startup. Importing `tests.support` also sets
`sys.dont_write_bytecode = True`. Test runs leave no `__pycache__` directories.

Every test class derives from `tests.support.OfflineTestCase`. Its name starts
with `Offline`, or `Simulated` when it injects quota responses. Tests cover
success, failure and refusal. The label test recursively collects the classes
in unittest's discovered suite. It rejects any class missing either the base
class or one of the two prefixes. It also tests those rejection rules with
synthetic classes. It checks the prefix, not whether quota data was injected.

## Per-test isolation and cleanup

`OfflineTestCase.run(result=None)` encloses the complete per-test execution in
an `ExitStack`: temporary tree, environment, working directory, default state
path and guard. This includes `setUp`, the test, `tearDown` and registered
cleanups, even when `setUp` omits `super()` or raises. Existing subclasses may
still call `super().setUp()`. Nested runs get independent contexts and restore
the outer guard, allowlist, environment, directory and default state on exit.
`enterContextCompat(context)` remains the Python 3.9 equivalent of
`TestCase.enterContext`: it registers a context exit with `addCleanup`.

`self.temp` is a resolved directory created under the system `/tmp`, outside
the checkout. Execution starts in its `work/` subdirectory. The harness
restores the prior working directory before deleting the temporary tree.
Fixtures use this tree, never the real checkout's Git index/config for induced
writes. The dirty flag in `version` is a boolean, independent of fixtures.

Each context removes inherited `GIT_*` variables and SSH authentication/prompt
overrides (`SSH_AUTH_SOCK`, `SSH_ASKPASS`, `SSH_ASKPASS_REQUIRE`) before setting:

| Variable | Sandbox value |
|---|---|
| `HOME`, `XDG_CONFIG_HOME` | Separate directories inside the temporary tree. |
| `GIT_CONFIG_GLOBAL` | An empty synthetic file inside the tree. |
| `GIT_CONFIG_NOSYSTEM` | `1` |
| `GIT_TERMINAL_PROMPT` | `0` |
| `GIT_ALLOW_PROTOCOL` | `file` |
| `GIT_CEILING_DIRECTORIES` | The resolved temporary tree root. |

The ceiling prevents an uninitialized fixture under `work/` from discovering
an enclosing repository above that boundary. It does not prevent discovery
of a scratch repository deliberately initialized below the boundary. Git's
protocol allowlist refuses non-file transports before transport execution.
Explicit child environments on the supported launch surfaces receive missing
baseline keys, even when given an otherwise empty mapping. Explicit values
win: tests may deliberately supply synthetic decoy homes, Git config and
repository overrides, or patch the per-test environment. Such overrides must
stay inside synthetic fixtures and must never enable live access. This is an
intentional test-writing surface, not protection against hostile code.

`t3compose.paths._default_local_dir` is normally `None`. The harness temporarily
sets it to `self.temp / ".local"`; no environment variable selects production
state. `local_dir()` and therefore the CLI's no-argument state default use this
path. Explicit `--local-dir` still resolves the supplied path. `ROOT` remains
the helper's real checkout. Tests may create/read synthetic state at the
default and it is removed on context exit.

Per-test contexts do not protect module import, `setUpClass`, `tearDownClass`,
or arbitrary overrides of `run`. Test authors must keep those surfaces free
of external access. The label check enforces inheritance and naming, not a
proof that arbitrary test code obeys these conventions.

## Blocked Python APIs

Every guard refusal raises
`AssertionError("offline guard blocked external access")`.
The guard patches these APIs when they exist:

| Surface | Blocked operations |
|---|---|
| `socket.socket` methods | `connect`, `connect_ex`, `send`, `sendall`, `sendto`, `sendmsg`. |
| `socket` module | `socket`, `SocketType`, `socketpair`, `create_connection`. |
| Name lookups in `socket` | `getaddrinfo`, `gethostbyname`, `gethostbyname_ex`, `gethostbyaddr`, `getnameinfo`. |
| `urllib.request` | `urlopen`, `OpenerDirector.open`, including non-network URLs. |
| Shell entry points | `os.system`, `os.popen`. |
| `os.spawn*` | `spawnl`, `spawnle`, `spawnlp`, `spawnlpe`, `spawnv`, `spawnve`, `spawnvp`, `spawnvpe`. |
| `os.exec*` | `execl`, `execle`, `execlp`, `execlpe`, `execv`, `execve`, `execvp`, `execvpe`. |

The guard wraps `subprocess.Popen`, so `subprocess.run` and other helpers that
use it also pass through the check. It wraps `os.posix_spawn` and
`os.posix_spawnp` where available. These three launch paths share
`check_program`; allowed calls continue through the original launch function.

## Program resolution and launch checks

The default program allowlist contains only the resolved `git` found on PATH
when the guard is installed. The guarded form of the running Python interpreter
has a separate rule below. Other programs require `self.allow_program(program)`.
Each test has its own allowlist. Extra allowances do not pass to bootstrap
children.

`resolve_program` uses `os.fsdecode` for filesystem arguments. An empty program
is refused. An explicit child `cwd` resolves from the parent's directory;
without it, resolution uses the current directory. A bare program name searches
`os.get_exec_path(env)` with `shutil.which`. Relative PATH entries resolve
against that working directory. A program containing a directory component
resolves directly against it. The result is a real path with symlinks resolved.
Explicit paths are resolved without the PATH executable lookup. A failed lookup
or a `TypeError`, `ValueError`, `OSError` or `RuntimeError` during resolution
causes a refusal.

For `Popen`, resolution uses the supplied child `cwd` and environment. The
wrapper reads `executable`, `shell`, `cwd` and `env` from keyword or positional
arguments. An explicit `executable` selects the actual program instead of
`argv[0]`. For `os.posix_spawn`, the explicit path is resolved without a PATH
search. For `os.posix_spawnp`, the check searches the parent's PATH, regardless
of the supplied child environment.

The check refuses `shell=True`, a string or bytes command, and `args=None`.
Arguments must be an iterable of filesystem-decodable values. Without an
explicit executable, the list must contain a program. Values that make
`os.fsdecode` raise `TypeError` or `ValueError` are refused.
On this platform undecodable filesystem bytes can survive as surrogateescape
characters; they are not rejected merely for being non-UTF-8. A later program
resolution failure is still refused. Normal calls use an argument list with
`shell=False`.

These exact basenames are forbidden:

```text
security, gh, ssh, curl, sh, bash, zsh, dash, ksh, fish,
claude, codex, agy, pi
```

The check compares both the supplied program's basename and the resolved
program's basename. It also refuses any argument whose basename is in that
set, including arguments to an otherwise allowed program. These comparisons
are case-sensitive. The program `env` is refused by supplied or resolved
basename. It cannot be used as a launcher.

For an ordinary program, the resolved file must be in the allowlist and must
not be a known harness path. Another file named `git` is not allowed merely
because it has that name. A symlink to an allowed file resolves to the same
allowance, subject to the forbidden-name checks.

## Adding a program and excluding harnesses

`self.allow_program(program)` resolves the program with the current directory
and environment. It refuses a supplied or resolved forbidden basename, `env`,
a known harness real path, or a recognized Python interpreter. Otherwise it
adds the real path for this test. The Python-name check lowercases the basename and recognizes
prefixes `python` and `pypy`, exact names `jython` and `ipy`, and the real path
of `sys.executable`. Both supplied and resolved paths receive that check.

```python
self.allow_program("/bin/echo")
result = subprocess.run(["/bin/echo", "synthetic"],
                        capture_output=True, text=True)
```

The harness names are `claude`, `codex`, `agy` and `pi`. The guard also
recognizes their targets when a symlink loses the harness basename.
`AllowedPrograms` records the real paths found for those names on the guard's
PATH at installation. `is_harness` unions in the paths found on the current
PATH each time it is called. It runs during an allowance check and before
accepting a stored allowlist member at launch. Previously found paths stay
forbidden after PATH hides them. A file allowed before PATH exposes it as a
harness is refused on a later launch once that exposure is seen.
Harness lookup tolerates absent names. It resolves paths only; it never runs
the harnesses or prints their paths. Lookup uses the guard process's current
PATH, not a proposed child's environment.

## Guarded Python children

The running Python interpreter has a special launch rule. Its resolved path
must equal the real path of `sys.executable`. It must run the bootstrap through
an absolute path that resolves to this checkout's `tests/support.py`.
Relative bootstrap paths are refused, even when
they would reach that file from the parent's directory.

The check always discards `argv[0]`, including when `executable` overrides it.
It then consumes only these separate option tokens before the bootstrap:

```text
-I  -S  -B  -E  -s  -u
```

These options are allowed, not required by the guard. Combined options and
other interpreter modes such as `-c` and `-m` are not accepted in that position.
The first remaining argument must be the absolute bootstrap path. At least one
argument must follow it: the target script. The forbidden-basename check still
applies to every argument, including the target and its arguments.
Programs recognized as other Python interpreters by the name check above are
refused and cannot be added through `allow_program`.

For example, using `sys.executable` and `ROOT` from `tests.support`:

```python
subprocess.run([sys.executable, "-I", "-S", "-B",
                str(ROOT / "tests/support.py"),
                str(ROOT / "bin/t3c"), "version"], check=True)
```

When executed as a script, `tests/support.py` exits 2 if it has no target.
Otherwise it resolves the target against the incoming directory, makes the
helper checkout importable, and enters the same sandbox context before running
the target. It sets `sys.argv` to the resolved target and remaining arguments
and calls `runpy.run_path(target, run_name="__main__")`. Each child has its own
temporary tree, default state, environment, working directory, allowlist and
harness-path history. A guarded child can start a guarded grandchild by the
same rule. Contexts restore state and remove temporary trees on normal exit
and Python exception unwinding, including `SystemExit`; abrupt termination
cannot guarantee cleanup.

## Process fakes and helper relaunches

Production modules use `from . import process` and call `process.run(...)` and
`process.helper_command(...)` through that module, so an in-process test can
patch the actual lookup. `FakeRunner(fixtures)` in `tests.support` is callable
as `run(argv, **kwargs)`: `fixtures` maps a program basename to a callable
receiving the argument list and keyword controls. It records `(argv, kwargs)`
in `calls` and fails an unconfigured call with `AssertionError`; it never
falls through to subprocess. Fixtures return `subprocess.CompletedProcess`
or raise a synthetic `ProcessError`. Assert the consumer's parsed result and
its dispatch arguments, not only that the fake ran. Forbidden-named executables
and stubs stay forbidden; a fake `gh` or `security` call launches nothing.

```python
from unittest.mock import patch
from t3compose import cli, process
from tests.support import FakeRunner
import subprocess

fake = FakeRunner({"git": lambda argv, **kw: subprocess.CompletedProcess(
    argv, 0, "synthetic-head\n" if argv[-1] == "HEAD" else "", "")})
with patch.object(process, "run", fake):
    info = cli.helper_info()
assert info["commit"] == "synthetic-head"
assert info["dirty"] is False
```

Call `main()` or a handler in-process for fake external services and process
results. A subprocess cannot inherit Python mocks. For entry-point flags,
real harmless Git, and descendant guard behavior, use the bootstrap.
`guarded_helper_command(*args)` returns the test-side invocation
`[sys.executable, '-I', '-S', '-B', <absolute support.py>, <ROOT>/bin/t3c, *args]`.
Patch `process.helper_command` to it when testing a consumer that relaunches
the helper. The production command deliberately has no bootstrap; it is
refused by the test guard unless the test supplies this substitution. Mocks
still do not propagate into that launched helper.

## Limits and live checks

The guard assumes test code written in good faith and catches accidental use
of the listed interfaces. It is test hygiene, not a security boundary.
Deliberate evasion, native extensions (including `ctypes`), `fork` or
multiprocessing children, unpatched file access and descendants of allowed
native programs are outside its guarantee. Case-sensitive name comparisons
are not a cross-platform security promise. It patches the listed
Python APIs. It does not see what an allowed program starts. An explicitly
allowed launcher can start descendants that the guard does not check.
A bootstrap child takes its default `git` from its own PATH. It installs its
own allowlist and harness-path history rather than inheriting the parent's.

Tests use synthetic fixtures and inert programs for refusal checks. They do
not run real agent harnesses. Offline and simulated results are labeled as such.
Live checks belong in `live/`, outside `SUITE`, and print `LIVE`. Only the
Engineering manager runs them, after review. Synthetic live inputs are labeled
synthetic. No offline or simulated result counts as a live pass. Live quota
failover stays unverified until an actual exhaustion event. There are no live
check scripts at this head.
