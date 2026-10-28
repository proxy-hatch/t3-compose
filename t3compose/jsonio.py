"""Canonical UTF-8 JSON and bounded reads; persistence belongs to records."""
import hashlib
import json


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


def read_json(path, max_bytes=16384):
    if max_bytes < 0:
        raise ValueError("invalid JSON read limit")
    with open(path, "rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("JSON read limit exceeded")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant)
    except (UnicodeError, ValueError):
        raise ValueError("invalid JSON data") from None
