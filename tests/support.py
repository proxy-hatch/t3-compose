"""Offline guard; not a security boundary for arbitrary child programs."""
import os
from pathlib import Path
import runpy
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

FORBIDDEN = {
    "security", "gh", "ssh", "curl", "sh", "bash", "zsh", "dash", "ksh", "fish"
}
ALLOWED_PYTHON_OPTIONS = {"-I", "-S", "-B", "-E", "-s", "-u"}

_original_popen = subprocess.Popen
_original_posix_spawn = getattr(os, "posix_spawn", None)
_original_posix_spawnp = getattr(os, "posix_spawnp", None)


def refuse(*args, **kwargs):
    raise AssertionError("offline guard blocked external access")


def is_python_interpreter(program):
    try:
        prog_path = Path(os.fsdecode(program))
        base = prog_path.name
    except Exception:
        return False
    if base in ("python", "python3"):
        return True
    if base.startswith("python3.") and base[len("python3."):].isdigit():
        return True
    try:
        if prog_path.resolve() == Path(sys.executable).resolve():
            return True
    except Exception:
        pass
    return False


def is_bootstrap(path_str):
    try:
        p = Path(os.fsdecode(path_str))
        if p.resolve() == BOOTSTRAP_PATH:
            return True
        if not p.is_absolute() and (ROOT / p).resolve() == BOOTSTRAP_PATH:
            return True
    except Exception:
        pass
    return False


def check_program(args, executable=None, shell=False):
    if shell or isinstance(args, (str, bytes)) or args is None:
        refuse()
    try:
        argv = [os.fsdecode(arg) for arg in args]
    except Exception:
        refuse()
    if executable is not None:
        try:
            program = os.fsdecode(executable)
        except Exception:
            refuse()
    elif argv:
        program = argv[0]
    else:
        refuse()

    if Path(program).name in FORBIDDEN:
        refuse()
    for arg in argv:
        if Path(arg).name in FORBIDDEN:
            refuse()

    if is_python_interpreter(program):
        options_and_rest = argv[1:] if (argv and is_python_interpreter(argv[0])) else argv
        idx = 0
        while idx < len(options_and_rest) and options_and_rest[idx] in ALLOWED_PYTHON_OPTIONS:
            idx += 1
        if idx >= len(options_and_rest):
            refuse()
        if options_and_rest[idx].startswith("-"):
            refuse()
        if not is_bootstrap(options_and_rest[idx]):
            refuse()


def guarded_popen(args, *rest, **kwargs):
    executable = kwargs.get("executable")
    if executable is None and len(rest) >= 2:
        executable = rest[1]
    shell = kwargs.get("shell", False)
    if not shell and len(rest) >= 7:
        shell = rest[6]
    check_program(args, executable=executable, shell=shell)
    return _original_popen(args, *rest, **kwargs)


def guarded_posix_spawn(path, argv, *rest, **kwargs):
    check_program(argv, executable=path, shell=False)
    return _original_posix_spawn(path, argv, *rest, **kwargs)


def guarded_posix_spawnp(path, argv, *rest, **kwargs):
    check_program(argv, executable=path, shell=False)
    return _original_posix_spawnp(path, argv, *rest, **kwargs)


def install_guard(enter_context=None):
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
        apply_patch(os, "posix_spawn", guarded_posix_spawn)
    if hasattr(os, "posix_spawnp"):
        apply_patch(os, "posix_spawnp", guarded_posix_spawnp)

    apply_patch(subprocess, "Popen", guarded_popen)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.temp = Path(self.enterContextCompat(tempfile.TemporaryDirectory(dir=ROOT)))
        install_guard(self.enterContextCompat)

    def enterContextCompat(self, context):
        """TestCase.enterContext for the supported Python 3.9 interpreter."""
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(2)
    install_guard()
    target = sys.argv[1]
    sys.argv = [target, *sys.argv[2:]]
    runpy.run_path(target, run_name="__main__")
