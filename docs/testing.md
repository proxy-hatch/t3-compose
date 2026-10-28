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

`OfflineTestCase.setUp` creates `self.temp`, a temporary directory under the
real repository root, then installs the guard. Tests can use that directory
for synthetic fixtures. Its presence can make `version` report a dirty tree.
The base class registers context cleanup for the directory and patches.
`enterContextCompat` supplies this behavior on Python 3.9: enter the context,
then register its exit with `addCleanup`. A test that overrides `setUp` must
call the base implementation to install the guard.

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
explicit executable, the list must contain a program. Invalid argument
decoding is refused. Normal calls use an argument list with `shell=False`.

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
Otherwise it installs a fresh guard, takes its first argument as the target,
sets `sys.argv` to the target and remaining arguments, and calls
`runpy.run_path(target, run_name="__main__")`. The guard is active before the
target runs. A guarded child can start a guarded grandchild by the same rule.

## Limits and live checks

The guard is test hygiene, not a security boundary. It patches the listed
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
