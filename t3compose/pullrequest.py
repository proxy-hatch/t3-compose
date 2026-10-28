"""Read-only PR identity and reviewed-head verification, with an injectable reader."""
import json
import re

from . import cli, identifier_list, process

MAX_RESPONSE_BYTES = 1048576
TIMEOUT_SECONDS = 30
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")


def _fail(message):
    raise cli.CommandError(message) from None


def _string(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail("PR field is invalid")
    try:
        if len(value.encode("utf-8")) > 1024:
            _fail("PR field exceeds byte limit")
    except UnicodeError:
        _fail("PR field is invalid")
    return value


def _request(repo, number):
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        _fail("PR repository must be OWNER/NAME")
    if type(number) is not int or not 1 <= number <= 2147483647:
        _fail("PR number must be a positive 32-bit integer")


def _shape(data):
    if not isinstance(data, dict):
        _fail("PR reader response is invalid")
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("PR reader response is invalid")
    if len(encoded) > MAX_RESPONSE_BYTES:
        _fail("PR reader response exceeds byte limit")
    if any(not isinstance(data.get(key), dict) for key in ("head", "base", "user")):
        _fail("PR reader response is invalid")
    try:
        head = _string(data["head"]["sha"])
        base = _string(data["base"]["ref"])
        author = _string(data["user"]["login"])
        state = data["state"]
    except (KeyError, TypeError):
        _fail("PR reader response is invalid")
    if not _SHA.fullmatch(head) or state not in ("open", "closed"):
        _fail("PR reader response is invalid")
    return head.lower(), base, state, author


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("PR API response is invalid")
        result[key] = value
    return result


def _constant(value):
    _fail("PR API response is invalid")


def read_pull_request(repo, number):
    """Single GET; tests replace this function or process.run, never launch gh."""
    _request(repo, number)
    try:
        result = process.run(["gh", "api", "--method", "GET", f"repos/{repo}/pulls/{number}"],
                             capture_output=True, check=False, timeout=TIMEOUT_SECONDS)
    except (process.ProcessError, OSError, ValueError):
        _fail("PR API read failed")
    raw = getattr(result, "stdout", None)
    if type(getattr(result, "returncode", None)) is not int or result.returncode != 0:
        _fail("PR API read failed")
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        _fail("PR API response is invalid or exceeds byte limit")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError):
        _fail("PR API response is invalid")
    _shape(data)
    return data


def verify(args):
    identifier_list.load_identifiers(args.local_dir)
    _request(args.repo, args.number)
    if not isinstance(args.head, str) or not _SHA.fullmatch(args.head):
        _fail("PR expected head must be a full 40-hex SHA")
    for expected in (args.base, args.author):
        if expected is not None:
            _string(expected)
    try:
        data = read_pull_request(args.repo, args.number)
    except Exception:
        _fail("PR reader failed")
    head, base, state, author = _shape(data)
    mismatches = [field for field, actual, expected in (
        ("head", head, args.head.lower()), ("base", base, args.base),
        ("state", state, "open"), ("author", author, args.author))
        if expected is not None and actual != expected]
    result = {"ok": not mismatches, "mismatches": mismatches,
              "action": "report to Shawn; not PR-ready" if mismatches else None}
    return cli.Outcome(result, cli.ExitCode.CHECK_FAILED) if mismatches else result


def register(registry):
    parser = registry.group("pr").add_parser("verify")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--number", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base")
    parser.add_argument("--author")
    parser.set_defaults(handler=verify)
