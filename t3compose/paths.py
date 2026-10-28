"""Paths belong to this helper's real checkout, independent of cwd."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Patched only by the offline test context; never read from the environment.
_default_local_dir = None


def local_dir(override=None):
    if override is not None:
        return Path(override).resolve()
    return _default_local_dir if _default_local_dir is not None else ROOT / ".local"
