"""Private identifier list loader and bounded grep -E matcher.

Reads ``<local_dir>/state/private-identifiers.txt`` freshly on every call and
evaluates each 1-based pattern line with ``grep -E -f`` so results match the
manual check without placing pattern values in ``argv`` or exposing patterns,
matched text, paths, or raw subprocess diagnostics.

Bounds: ``MAX_LIST_BYTES`` (65,536 bytes), ``MAX_PATTERNS`` (512 LF-delimited
lines), ``MAX_PATTERN_BYTES`` (1,024 UTF-8 bytes/line), ``MAX_TEXT_BYTES``
(1,048,576 UTF-8 bytes), ``GREP_TIMEOUT_SECONDS`` (5.0s per pattern check).
"""
import os
from pathlib import Path
import stat
import tempfile

from . import cli, process

IDENTIFIER_RELPATH = Path("state") / "private-identifiers.txt"
MAX_LIST_BYTES = 65536
MAX_PATTERNS = 512
MAX_PATTERN_BYTES = 1024
MAX_TEXT_BYTES = 1048576
GREP_TIMEOUT_SECONDS = 5.0


def _fail(message):
    raise cli.CommandError(message, code=cli.ExitCode.INVALID_INPUT)


def _split_lf_lines(text):
    if not text:
        return []
    return (text[:-1] if text.endswith("\n") else text).split("\n")


def _read_list_bytes(local_dir):
    if local_dir is None or not isinstance(local_dir, (str, os.PathLike)):
        _fail("invalid local directory")
    try:
        raw_dir = os.fsdecode(local_dir)
    except (TypeError, ValueError):
        _fail("invalid local directory")
    if not raw_dir or "\x00" in raw_dir:
        _fail("invalid local directory")
    target = Path(raw_dir) / IDENTIFIER_RELPATH
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        _fail("identifier list is missing")
    except OSError:
        _fail("identifier list is unreadable")
    try:
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail("identifier list is unreadable")
            data = stream.read(MAX_LIST_BYTES + 1)
    except cli.CommandError:
        raise
    except OSError:
        _fail("identifier list is unreadable")
    if not data:
        _fail("identifier list is empty")
    if len(data) > MAX_LIST_BYTES:
        _fail("identifier list exceeds byte limit")
    if b"\x00" in data:
        _fail("identifier list contains invalid byte")
    return data


def _encode_patterns(identifier_list):
    if not isinstance(identifier_list, (list, tuple)):
        _fail("identifier list must be a list of patterns")
    if not identifier_list:
        _fail("identifier list is empty")
    if len(identifier_list) > MAX_PATTERNS:
        _fail("identifier list exceeds line limit")
    encoded, total_bytes = [], 0
    for line_number, pattern in enumerate(identifier_list, start=1):
        if not isinstance(pattern, str) or "\n" in pattern or "\x00" in pattern:
            _fail("invalid identifier pattern at line {}".format(line_number))
        try:
            pattern_bytes = pattern.encode("utf-8")
        except UnicodeEncodeError:
            _fail("invalid identifier pattern at line {}".format(line_number))
        if len(pattern_bytes) > MAX_PATTERN_BYTES:
            _fail("identifier pattern at line {} exceeds byte limit".format(line_number))
        total_bytes += len(pattern_bytes)
        if total_bytes > MAX_LIST_BYTES:
            _fail("identifier list exceeds byte limit")
        encoded.append(pattern_bytes)
    return encoded


def _encode_text(text):
    if not isinstance(text, str):
        _fail("text must be a string")
    if "\x00" in text:
        _fail("text contains invalid byte")
    try:
        text_bytes = text.encode("utf-8")
    except UnicodeEncodeError:
        _fail("text is not valid UTF-8")
    if len(text_bytes) > MAX_TEXT_BYTES:
        _fail("text exceeds byte limit")
    return text_bytes


def _evaluate_patterns(encoded_patterns, input_bytes):
    matches = []
    try:
        with tempfile.TemporaryDirectory(dir="/tmp", prefix="t3c-id-") as temp_dir:
            pattern_path = Path(temp_dir) / "pattern.txt"
            for line_number, pattern_bytes in enumerate(encoded_patterns, start=1):
                pattern_path.write_bytes(pattern_bytes + b"\n")
                try:
                    result = process.run(
                        ["grep", "-E", "-f", str(pattern_path)],
                        input=input_bytes, capture_output=True, check=False,
                        timeout=GREP_TIMEOUT_SECONDS,
                    )
                except process.ProcessError as error:
                    _fail("identifier check failed at line {}: {}".format(
                        line_number, error.kind))
                code = getattr(result, "returncode", None)
                if isinstance(code, bool) or not isinstance(code, int):
                    _fail("identifier check failed at line {}: exit".format(line_number))
                if code == 0:
                    matches.append(line_number)
                elif code == 2:
                    _fail("invalid identifier pattern at line {}".format(line_number))
                elif code != 1:
                    _fail("identifier check failed at line {}: exit".format(line_number))
    except cli.CommandError:
        raise
    except OSError:
        _fail("identifier check failed: os-error")
    return matches


def load_identifiers(local_dir):
    """Load and validate ``<local_dir>/state/private-identifiers.txt`` with ``grep -E``."""
    raw = _read_list_bytes(local_dir)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        _fail("identifier list is not valid UTF-8")
    patterns = _split_lf_lines(decoded)
    _evaluate_patterns(_encode_patterns(patterns), b"")
    return list(patterns)


def match_identifiers(identifier_list, text):
    """Return 1-based identifier-list line numbers matching ``text`` under ``grep -E``."""
    return _evaluate_patterns(_encode_patterns(identifier_list), _encode_text(text))
