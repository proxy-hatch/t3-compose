"""Paths belong to this helper's real checkout, independent of cwd."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def local_dir(override=None):
    return Path(override).resolve() if override is not None else ROOT / ".local"
