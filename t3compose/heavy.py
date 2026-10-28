"""Stable-inode heavy-check exclusion; see docs/cli/heavy.md for boundaries."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess

from . import jsonio, process
from .cli import CommandError, ExitCode, Outcome

LOCK = "heavy-check.lock"
STATUS = "heavy-check-status.json"
SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def register(registry):
    group = registry.group("heavy")
    parser = group.add_parser("run")
    for option in ("repo-path", "commit", "label", "log"):
        parser.add_argument("--" + option, required=True)
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--_reply-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("child_argv", nargs=argparse.REMAINDER)
    parser.set_defaults(handler=run)
    group.add_parser("status").set_defaults(handler=status)


def _refuse():
    raise CommandError("heavy: invalid inputs or unsafe filesystem state")


def _git(repo, *args):
    # Repository-discovery overrides must not redirect reads to another index.
    env = {k: v for k, v in os.environ.items() if k not in {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"}}
    return process.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                       env=env, check=True, capture_output=True, text=True).stdout.strip()


def _preflight(args):
    if (not re.fullmatch(r"[0-9a-f]{40}", args.commit) or
            not 1 <= len(args.label) <= 200 or not args.label.isprintable() or
            not args.label.strip() or not args.log or not args.repo_path or
            len(args.child_argv) < 2 or args.child_argv[0] != "--" or
            not args.child_argv[1] or any("\0" in x for x in args.child_argv)):
        _refuse()
    repo = Path(args.repo_path).resolve(strict=True)
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    common = Path(_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    if (Path(_git(repo, "rev-parse", "--show-toplevel")) != repo or
            gitdir == common or not (repo / ".git").is_file() or
            _git(repo, "rev-parse", "HEAD") != args.commit or
            _git(repo, "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none")):
        _refuse()
    # Resolve parents, never resolve an existing log leaf into a write target.
    log = Path(args.log).absolute()
    log = log.parent.resolve(strict=True) / log.name
    local = Path(args.local_dir)
    for path in (log, local):
        if any(path.is_relative_to(base) for base in (repo, common, gitdir)):
            _refuse()
    if os.path.lexists(log) or log.is_relative_to(local):
        _refuse()
    args.repo_path, args.log = str(repo), str(log)


@contextmanager
def _directory(path, create=False):
    """Pin every traversal step; never follow symlink directory components."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in Path(path).absolute().parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


def _regular(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        _refuse()


def _same_lock(directory, fd):
    current = os.stat(LOCK, dir_fd=directory, follow_symlinks=False)
    _regular(current)
    held = os.fstat(fd)
    if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
        _refuse()


@contextmanager
def _state(local):
    with _directory(Path(local) / "state/locks", create=True) as directory:
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        try:
            _regular(os.fstat(fd))
            _same_lock(directory, fd)
            yield directory, fd
        finally:
            os.close(fd)


def _metadata_file(directory):
    try:
        info = os.stat(STATUS, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    _regular(info)
    return True


def _metadata(directory):
    if not _metadata_file(directory):
        return None, "missing"
    fd = os.open(STATUS, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        with os.fdopen(fd, "rb") as stream:
            _regular(os.fstat(stream.fileno()))
            # read_json owns its input descriptor; keep our validated pin open.
            holder = jsonio.read_json(os.dup(stream.fileno()))
        if (not isinstance(holder, dict) or set(holder) != {
                "label", "commit", "runnerPid", "checkPid", "startedAt", "summary"} or
                not isinstance(holder["label"], str) or not holder["label"].isprintable() or
                not 1 <= len(holder["label"]) <= 200 or
                not isinstance(holder["commit"], str) or
                not re.fullmatch(r"[0-9a-f]{40}", holder["commit"]) or
                not isinstance(holder["startedAt"], str) or
                not holder["startedAt"].endswith("Z")):
            raise ValueError
        datetime.fromisoformat(holder["startedAt"])
        for key in ("runnerPid", "checkPid"):
            pid = holder[key]
            if key == "checkPid" and pid is None:
                continue
            if type(pid) is not int or not 0 < pid <= 2147483647:
                raise ValueError
        summary = holder["summary"]
        if summary is not None:
            if (not isinstance(summary, dict) or set(summary) != {
                    "label", "commit", "log", "childStatus", "headUnchanged",
                    "treeUnchanged", "startedAt", "endedAt"} or
                    summary["label"] != holder["label"] or summary["commit"] != holder["commit"] or
                    type(summary["childStatus"]) is not int or
                    any(type(summary[k]) is not bool for k in ("headUnchanged", "treeUnchanged")) or
                    any(not isinstance(summary[k], str) for k in ("log", "startedAt", "endedAt"))):
                raise ValueError
        return holder, "present"
    except (ValueError, TypeError, OverflowError):
        return None, "malformed"


def _pid_state(pid):
    if pid is None:
        return "dead"
    try:
        os.kill(pid, 0)
        return "present"
    except ProcessLookupError:
        return "dead"
    except OSError:
        return "unknown"


def _observation(directory, locked):
    holder, metadata = _metadata(directory)
    owner = "unknown"
    if not locked:
        owner = "not-held"
        if holder is not None:
            metadata = "stale"
    elif holder is not None:
        states = [_pid_state(holder[k]) for k in ("runnerPid", "checkPid")]
        if states == ["dead", "dead"]:
            owner = "recorded-owners-dead"
        elif "present" in states:
            owner = "recorded-owner-present"
    return {"locked": locked, "metadata": metadata, "holder": holder, "ownerObservation": owner}


def status(args):
    with _state(args.local_dir) as (directory, fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return _observation(directory, True)
        try:
            _same_lock(directory, fd)
            return _observation(directory, False)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


class _ForwardSignals:
    """Queue signals across launch assignment; cleanup never abandons a child."""
    def __init__(self):
        self.child = None
        self.pending = []
        self.old = {}

    def receive(self, signum, frame):
        self.pending.append(signum)
        self.forward()

    def forward(self):
        if self.child is not None:
            while self.pending:
                signum = self.pending.pop(0)
                try:
                    self.child.send_signal(signum)
                except ProcessLookupError:
                    pass

    def __enter__(self):
        for signum in SIGNALS:
            self.old[signum] = signal.signal(signum, self.receive)
        return self

    def __exit__(self, *exc):
        try:
            if self.child is not None:
                self.forward()
                self.child.wait()
        finally:
            for signum, previous in self.old.items():
                signal.signal(signum, previous)


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _publish(directory, fd, holder):
    _same_lock(directory, fd)
    _metadata_file(directory)
    jsonio.atomic_replace(STATUS, holder, dir_fd=directory)


def _runner(args):
    with _ForwardSignals() as forwarding, _state(args.local_dir) as (directory, fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if args.wait else fcntl.LOCK_NB))
        except BlockingIOError:
            return Outcome(_observation(directory, True), ExitCode.BUSY)
        child = None
        try:
            _same_lock(directory, fd)
            _preflight(args)
            if forwarding.pending:
                _refuse()
            holder = {"label": args.label, "commit": args.commit, "runnerPid": os.getpid(),
                      "checkPid": None, "startedAt": _now(), "summary": None}
            _publish(directory, fd, holder)
            with _directory(Path(args.log).parent) as logdir:
                logfd = os.open(Path(args.log).name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                os.O_NOFOLLOW, 0o600, dir_fd=logdir)
            with os.fdopen(logfd, "wb") as log:
                child = process.start(args.child_argv[1:], cwd=args.repo_path,
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                      pass_fds=(fd,), start_new_session=True)
                forwarding.child = child
                forwarding.forward()
                holder["checkPid"] = child.pid
                _publish(directory, fd, holder)
                child_status = child.wait()
            try:
                head = _git(args.repo_path, "rev-parse", "HEAD") == args.commit
            except process.ProcessError:
                head = False
            try:
                tree = not _git(args.repo_path, "status", "--porcelain", "--untracked-files=all",
                                "--ignore-submodules=none")
            except process.ProcessError:
                tree = False
            summary = {"label": args.label, "commit": args.commit, "log": args.log,
                       "childStatus": child_status, "headUnchanged": head, "treeUnchanged": tree,
                       "startedAt": holder["startedAt"], "endedAt": _now()}
            holder["summary"] = summary
            _publish(directory, fd, holder)
            code = ExitCode.SUCCESS if child_status == 0 and head and tree else ExitCode.CHECK_FAILED
            return Outcome(summary, code)
        finally:
            # Explicit unlock affects all inherited duplicates, including a
            # grandchild's. Never do this until the direct check is reaped.
            if child is not None:
                child.wait()
            fcntl.flock(fd, fcntl.LOCK_UN)


def _reply(args):
    fd = args._reply_fd
    if fd < 3 or not stat.S_ISFIFO(os.fstat(fd).st_mode):
        _refuse()
    try:
        try:
            result = _runner(args)
            reply = {"code": int(result.code), "data": result.data}
        except Exception:
            reply = {"code": 2, "data": None}
        # Response is sent only after the check exited and the lock released.
        with os.fdopen(fd, "wb") as stream:
            stream.write(jsonio.canonical_json(reply))
    except BrokenPipeError:
        pass
    return None


def run(args):
    if args._reply_fd is not None:
        return _reply(args)
    _preflight(args)
    # Preflight state aliases before starting the runner. This probes no lock.
    with _state(args.local_dir) as (directory, fd):
        _metadata_file(directory)
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as reply, _ForwardSignals() as forwarding:
        try:
            argv = process.helper_command("--local-dir", str(args.local_dir), "heavy", "run",
                "--repo-path", args.repo_path, "--commit", args.commit, "--label", args.label,
                "--log", args.log, "--_reply-fd", str(write_fd),
                *(["--wait"] if args.wait else []), *args.child_argv)
            forwarding.child = process.start(argv, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                pass_fds=(write_fd,), start_new_session=True)
            forwarding.forward()
        finally:
            os.close(write_fd)
        data = reply.read(65537)
        forwarding.child.wait()
        try:
            result = json.loads(data) if len(data) <= 65536 else None
            if not isinstance(result, dict) or result.get("code") not in (0, 1, 75):
                _refuse()
            return Outcome(result["data"], ExitCode(result["code"]))
        except (ValueError, KeyError):
            _refuse()
