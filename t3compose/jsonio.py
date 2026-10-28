"""Canonical UTF-8 JSON, bounded reads and durable atomic publication."""
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid

MAX_INT_DIGITS = 4300


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("non-finite JSON number")


def _parse_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite JSON number")
    return result


def _parse_int(value):
    digits = value[1:] if value.startswith("-") else value
    if len(digits) > MAX_INT_DIGITS:
        raise ValueError("JSON integer exceeds digit limit")
    return int(value)


def _read_and_parse(path, max_bytes):
    if max_bytes < 0:
        raise ValueError("invalid JSON read limit")
    with open(path, "rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("JSON read limit exceeded")
    try:
        parsed = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant,
                            parse_float=_parse_float,
                            parse_int=_parse_int)
        canonical_json(parsed)
        return parsed, data
    except (UnicodeError, ValueError):
        raise ValueError("invalid JSON data") from None


def read_json(path, max_bytes=16384):
    parsed, _ = _read_and_parse(path, max_bytes)
    return parsed


def read_json_with_digest(path, max_bytes=16384):
    parsed, data = _read_and_parse(path, max_bytes)
    return parsed, "sha256:" + hashlib.sha256(data).hexdigest()


# Reserved names and inode locks are shared with records recovery (RC-1c).
# A lock alone cannot prove abandonment during the create-before-lock window.
TEMP_NAME = re.compile(r"\.t3c-tmp-([1-9][0-9]*)-([0-9a-f]{32})\.tmp")
_TEMP_CREATION_LOCK = threading.Lock()


def atomic_replace(path, value, *, dir_fd=None):
    """Replace JSON atomically; parent exists. See docs/records.md for recovery."""
    _atomic_write(path, value, dir_fd, exclusive=False)


def atomic_create(path, value, *, dir_fd=None):
    """Publish JSON exclusively; FileExistsError means collision, never retry."""
    _atomic_write(path, value, dir_fd, exclusive=True)


def _atomic_write(path, value, dir_fd, *, exclusive):
    encoded = canonical_json(value)
    path = Path(path)
    if dir_fd is not None and (len(path.parts) != 1 or path.name in {"", ".", ".."}):
        raise ValueError("atomic descriptor path must be a basename")
    owned_directory = dir_fd is None
    if owned_directory:
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = ".t3c-tmp-{}-{}.tmp".format(os.getpid(), uuid.uuid4().hex)
    fd = None
    try:
        with _TEMP_CREATION_LOCK:
            previous_mask = os.umask(0o077)
            try:
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=dir_fd)
            finally:
                os.umask(previous_mask)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.fchmod(fd, 0o600)
        remaining = memoryview(encoded)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("atomic write made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
        if exclusive:
            os.link(temp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd,
                    follow_symlinks=False)
        else:
            os.replace(temp, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    finally:
        try:
            if fd is not None:
                try:
                    os.unlink(temp, dir_fd=dir_fd)
                except FileNotFoundError:
                    pass
                os.fsync(dir_fd)
        finally:
            if fd is not None:
                os.close(fd)
            if owned_directory:
                os.close(dir_fd)
