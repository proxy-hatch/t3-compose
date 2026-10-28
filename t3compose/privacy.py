"""Read-only publication screening of every selected commit and supplied text."""
import os
import re
import stat

from . import cli, identifier_list, process

MAX_BYTES = identifier_list.MAX_TEXT_BYTES
TIMEOUT_SECONDS = 30
_SHA = re.compile(r"[0-9a-f]{40}")
_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*(?:[~^][0-9]*)*")


def _fail(message):
    raise cli.CommandError(message) from None


def _text(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES or b"\0" in raw:
        _fail("privacy input must be bounded UTF-8 text without NUL")
    try:
        return raw.decode("utf-8")
    except UnicodeError:
        _fail("privacy input must be bounded UTF-8 text without NUL")


def _git(repo, *arguments, input=None, allow_detached=False):
    # Ignore ambient repository selectors, replacement objects and lazy fetches.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_GRAFT_FILE=os.devnull, GIT_OPTIONAL_LOCKS="0",
               GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="")
    try:
        result = process.run(["git", "--no-optional-locks", *arguments], cwd=repo,
                             env=env, input=input, capture_output=True, check=False,
                             timeout=TIMEOUT_SECONDS)
    except (process.ProcessError, OSError, ValueError):
        _fail("privacy Git read failed")
    code, data = getattr(result, "returncode", None), getattr(result, "stdout", None)
    if type(code) is not int or code not in ({0, 1} if allow_detached else {0}):
        _fail("privacy Git read failed")
    if code == 1:
        _fail("privacy requires --branch for a detached head")
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        _fail("privacy Git response is invalid or exceeds byte limit")
    return data


def _resolve(repo, ref):
    # Ref names go on stdin, never argv. Peel tags and require a commit.
    raw = _git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
               input=(ref + "^{commit}\n").encode("utf-8"))
    if not re.fullmatch(rb"[0-9a-f]{40} commit\n", raw):
        _fail("privacy range endpoint is not a commit")
    return raw[:40].decode("ascii")


def _object(repo, kind, sha):
    size = _git(repo, "cat-file", "-s", sha)
    if not re.fullmatch(rb"[0-9]{1,10}\n", size) or int(size) > MAX_BYTES:
        _fail("privacy object exceeds byte limit or has invalid size")
    raw = _git(repo, "cat-file", kind, sha)
    if len(raw) != int(size):
        _fail("privacy object size differs from its contents")
    return raw


def _file(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail("privacy text file is not a regular file")
            return _text(stream.read(MAX_BYTES + 1))
    except (OSError, ValueError):
        _fail("privacy text file is unreadable")


def check(args):
    patterns = identifier_list.load_identifiers(args.local_dir)
    value = args.range
    if not isinstance(value, str) or value.count("..") != 1:
        _fail("privacy requires a BASE..HEAD range")
    refs = value.split("..")
    if any(not _REF.fullmatch(ref) or ref.endswith(".") or ref.startswith(".") for ref in refs):
        _fail("privacy range endpoint is invalid")
    if not args.repo_path or "\0" in args.repo_path:
        _fail("privacy repository path is invalid")
    texts = []
    for spec in args.text_file:
        label, sep, path = spec.partition("=")
        if not sep or not label or not path or "\0" in spec:
            _fail("privacy text file requires LABEL=FILE")
        texts.append((label, path))
    repo = args.repo_path
    if _git(repo, "rev-parse", "--is-shallow-repository") != b"false\n":
        _fail("privacy requires complete repository history")
    base, head = (_resolve(repo, ref) for ref in refs)
    raw = _git(repo, "rev-list", base + ".." + head, "--")
    commits = _text(raw).split("\n") if raw else []
    if commits:
        if commits.pop() != "" or any(not _SHA.fullmatch(sha) for sha in commits):
            _fail("privacy commit enumeration is invalid")
    branch = args.branch
    if branch is None:
        raw = _git(repo, "symbolic-ref", "--quiet", "HEAD", allow_detached=True)
        prefix = b"refs/heads/"
        if not raw.startswith(prefix) or not raw.endswith(b"\n"):
            _fail("privacy branch response is invalid")
        branch = _text(raw[len(prefix):-1])
    if not branch:
        _fail("privacy branch is empty")
    findings = []

    def match(text):
        return identifier_list.match_identifiers(patterns, text)

    def safe(value):
        return "[redacted]" if value is not None and match(value) else value

    def screen(field, text, commit=None, file=None, label=None):
        lines = match(text)
        if lines:
            findings.append({"field": field, "commit": safe(commit), "file": safe(file),
                             "label": safe(label), "lines": lines})

    screen("branch", branch)
    for label, path in texts:
        screen("label", label, label=label)
        screen("text", _file(path), label=label)
    for sha in commits:
        raw = _object(repo, "commit", sha)
        _, separator, message = raw.partition(b"\n\n")
        if not separator:
            _fail("privacy commit has no message separator")
        screen("message", _text(message), commit=sha)
        tree = _git(repo, "ls-tree", "-r", "-t", "-z", "--full-tree", sha, "--")
        if tree and not tree.endswith(b"\0"):
            _fail("privacy tree response is invalid")
        for entry in tree[:-1].split(b"\0") if tree else []:
            metadata, tab, path = entry.partition(b"\t")
            if not tab or not path:
                _fail("privacy tree entry is invalid")
            filename = _text(path)
            if filename.endswith(".lock"):
                continue
            if not re.fullmatch(rb"(?:(?:100644|100755|120000) blob|040000 tree) [0-9a-f]{40}", metadata):
                _fail("privacy tree contains an unsupported entry")
            screen("path", filename, commit=sha, file=filename)
            if metadata.startswith(b"040000 tree "):
                continue
            blob = metadata[-40:].decode("ascii")
            screen("file", _text(_object(repo, "blob", blob)), commit=sha, file=filename)
    data = {"ok": not findings, "commits": len(commits), "findings": findings,
            "action": "publication blocked; Corrections or Shawn must resolve matches" if findings else None}
    return cli.Outcome(data, cli.ExitCode.CHECK_FAILED) if findings else data


def register(registry):
    parser = registry.group("privacy").add_parser("check")
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--range", required=True)
    parser.add_argument("--branch")
    parser.add_argument("--text-file", action="append", default=[])
    parser.set_defaults(handler=check)
