"""An allowlist for offline test hygiene, not a security boundary.

Tests that start another program must allow it explicitly. On supported launch
interfaces, Python children run through the absolute path of this guard's
bootstrap. The guard does
not see what an allowed program starts, and a bootstrap child takes its
default git from its own PATH.
"""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_PATH = Path(__file__).resolve()

# The agent harnesses that T3 runs. The helper never starts an agent.
HARNESSES = ("claude", "codex", "agy", "pi")
# Credential, network and shell programs, and the harnesses: tests can never
# allow these.
FORBIDDEN = {
    "security", "gh", "ssh", "curl", "sh", "bash", "zsh", "dash", "ksh", "fish",
    *HARNESSES,
}
ALLOWED_PYTHON_OPTIONS = {"-I", "-S", "-B", "-E", "-s", "-u"}

_original_popen = subprocess.Popen
_original_posix_spawn = getattr(os, "posix_spawn", None)
_original_posix_spawnp = getattr(os, "posix_spawnp", None)


def refuse(*args, **kwargs):
    raise AssertionError("offline guard blocked external access")


def is_python_interpreter(program):
    path = Path(os.fsdecode(program))
    return (path.name.lower().startswith(("python", "pypy")) or
            path.name.lower() in {"jython", "ipy"} or
            path == Path(sys.executable).resolve())


def resolve_program(program, cwd=None, env=None, search_path=True):
    """Resolve the file the launch API will execute, including child cwd/PATH."""
    try:
        program = os.fsdecode(program)
        if not program:
            refuse()
        directory = Path(os.fsdecode(cwd)).resolve() if cwd is not None else Path.cwd()
        if search_path and not os.path.dirname(program):
            path = os.pathsep.join(str(directory / entry) for entry in os.get_exec_path(env))
            program = shutil.which(program, path=path)
            if program is None:
                refuse()
        return (directory / program).resolve()
    except (TypeError, ValueError, OSError, RuntimeError):
        refuse()


def harness_paths():
    """Real paths of the harnesses on the current PATH; looked up, never run."""
    paths = set()
    for name in HARNESSES:
        try:
            paths.add(resolve_program(name))
        except AssertionError:
            pass
    return paths


class AllowedPrograms:
    """Programs a test may start. A harness never counts: neither one found on
    PATH when the guard was installed, nor one that PATH shows later."""

    def __init__(self):
        self.programs = {resolve_program("git")}
        self.harnesses = harness_paths()

    def add(self, program):
        self.programs.add(program)

    def is_harness(self, program):
        self.harnesses |= harness_paths()
        return program in self.harnesses

    def __contains__(self, program):
        return program in self.programs and not self.is_harness(program)


def is_bootstrap(path_str):
    try:
        p = Path(os.fsdecode(path_str))
        return p.is_absolute() and p.resolve() == BOOTSTRAP_PATH
    except (TypeError, ValueError, OSError, RuntimeError):
        return False


def check_program(args, allowed_programs, executable=None, shell=False,
                  cwd=None, env=None, search_path=True):
    if shell or isinstance(args, (str, bytes)) or args is None:
        refuse()
    try:
        argv = [os.fsdecode(arg) for arg in args]
    except (TypeError, ValueError):
        refuse()
    if executable is not None:
        try:
            program = os.fsdecode(executable)
        except (TypeError, ValueError):
            refuse()
    elif argv:
        program = argv[0]
    else:
        refuse()

    resolved = resolve_program(program, cwd=cwd, env=env, search_path=search_path)
    if Path(program).name in FORBIDDEN or resolved.name in FORBIDDEN:
        refuse()
    for arg in argv:
        if Path(arg).name in FORBIDDEN:
            refuse()

    if resolved == Path(sys.executable).resolve():
        # Python always discards argv[0], even with an explicit executable.
        options_and_rest = argv[1:]
        idx = 0
        while idx < len(options_and_rest) and options_and_rest[idx] in ALLOWED_PYTHON_OPTIONS:
            idx += 1
        if idx + 1 >= len(options_and_rest) or not is_bootstrap(options_and_rest[idx]):
            refuse()
        return
    if (Path(program).name == "env" or resolved.name == "env" or
            is_python_interpreter(program) or is_python_interpreter(resolved) or
            resolved not in allowed_programs):
        refuse()


def guarded_popen(allowed_programs, baseline, args, *rest, **kwargs):
    executable = kwargs.get("executable")
    if executable is None and len(rest) >= 2:
        executable = rest[1]
    shell = kwargs.get("shell", False)
    if not shell and len(rest) >= 8:
        shell = rest[7]
    cwd = kwargs.get("cwd", rest[8] if len(rest) >= 9 else None)
    env = kwargs.get("env", rest[9] if len(rest) >= 10 else None)
    if env is not None:
        env = {**baseline, **env}
        if len(rest) >= 10:
            rest = (*rest[:9], env, *rest[10:])
        else:
            kwargs["env"] = env
    check_program(args, allowed_programs, executable=executable, shell=shell, cwd=cwd, env=env)
    return _original_popen(args, *rest, **kwargs)


def guarded_posix_spawn(allowed_programs, baseline, path, argv, env, **kwargs):
    # posix_spawn takes a file path; it does not search PATH.
    check_program(argv, allowed_programs, executable=path, search_path=False)
    return _original_posix_spawn(path, argv, {**baseline, **env}, **kwargs)


def guarded_posix_spawnp(allowed_programs, baseline, path, argv, env, **kwargs):
    # posix_spawnp searches the parent's PATH, not the supplied child environment.
    check_program(argv, allowed_programs, executable=path)
    return _original_posix_spawnp(path, argv, {**baseline, **env}, **kwargs)


def install_guard(enter_context=None, baseline=None):
    baseline = {} if baseline is None else baseline
    allowed_programs = AllowedPrograms()

    def allow_program(program):
        resolved = resolve_program(program)
        if (Path(os.fsdecode(program)).name in FORBIDDEN | {"env"} or
                resolved.name in FORBIDDEN | {"env"} or
                allowed_programs.is_harness(resolved) or
                is_python_interpreter(program) or is_python_interpreter(resolved)):
            refuse()
        allowed_programs.add(resolved)

    def apply_patch(target_or_obj, attr=None, new=None):
        if attr is None:
            p = patch(target_or_obj, new)
        else:
            p = patch.object(target_or_obj, attr, new)
        if enter_context is not None:
            enter_context(p)
        else:
            p.start()

    for name in ("connect", "connect_ex", "send", "sendall", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            apply_patch(socket.socket, name, refuse)

    for name in ("socket", "SocketType", "socketpair", "create_connection", "getaddrinfo",
                 "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "getnameinfo"):
        if hasattr(socket, name):
            apply_patch(socket, name, refuse)

    for target in ("urllib.request.urlopen", "urllib.request.OpenerDirector.open",
                   "os.system", "os.popen"):
        apply_patch(target, new=refuse)

    for name in ("spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
                 "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe"):
        if hasattr(os, name):
            apply_patch(os, name, refuse)

    if hasattr(os, "posix_spawn"):
        apply_patch(os, "posix_spawn", lambda *args, **kwargs:
                    guarded_posix_spawn(allowed_programs, baseline, *args, **kwargs))
    if hasattr(os, "posix_spawnp"):
        apply_patch(os, "posix_spawnp", lambda *args, **kwargs:
                    guarded_posix_spawnp(allowed_programs, baseline, *args, **kwargs))

    apply_patch(subprocess, "Popen", lambda *args, **kwargs:
                guarded_popen(allowed_programs, baseline, *args, **kwargs))
    return allow_program


class FakeRunner:
    """Basename-keyed in-process fixtures; no fallback to a real executable."""
    def __init__(self, fixtures):
        self.fixtures = dict(fixtures)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        fixture = self.fixtures.get(Path(os.fsdecode(argv[0])).name)
        if fixture is None:
            raise AssertionError("unconfigured process fixture")
        return fixture(argv, **kwargs)


def guarded_helper_command(*args):
    """Test substitution for process.helper_command, with a fresh child guard."""
    return [sys.executable, "-I", "-S", "-B", str(BOOTSTRAP_PATH),
            str(ROOT / "bin/t3c"), *args]


@contextmanager
def offline_context():
    """One good-faith test/child sandbox; restore cwd before removing its tree."""
    from t3compose import paths

    with ExitStack() as stack:
        # Use the system temporary root, not a caller-controlled TMPDIR or cwd.
        temp = Path(stack.enter_context(tempfile.TemporaryDirectory(dir="/tmp"))).resolve()
        home, xdg, work = (temp / name for name in ("home", "xdg-config", "work"))
        for directory in (home, xdg, work):
            directory.mkdir()
        global_config = temp / "empty-git-config"
        global_config.write_text("")
        baseline = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(xdg),
            "GIT_CONFIG_GLOBAL": str(global_config), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
            "GIT_CEILING_DIRECTORIES": str(temp),
        }
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_") and key not in
                       {"SSH_AUTH_SOCK", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE"}}
        environment.update(baseline)
        stack.enter_context(patch.dict(os.environ, environment, clear=True))
        previous_cwd = Path.cwd()
        stack.callback(os.chdir, previous_cwd)
        os.chdir(work)
        stack.enter_context(patch.object(paths, "_default_local_dir", temp / ".local"))
        allow = install_guard(stack.enter_context, baseline)
        yield temp, allow


class OfflineTestCase(unittest.TestCase):
    def run(self, result=None):
        with offline_context() as (self.temp, self._allow_program):
            return super().run(result)

    def allow_program(self, program):
        """Allow a program for this test; never a blocked name, env or Python."""
        self._allow_program(program)

    def enterContextCompat(self, context):
        """TestCase.enterContext for the supported Python 3.9 interpreter."""
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    target = str(Path(sys.argv[1]).resolve())
    sys.path.insert(0, str(ROOT))
    sys.argv = [target, *sys.argv[2:]]
    with offline_context():
        runpy.run_path(target, run_name="__main__")
