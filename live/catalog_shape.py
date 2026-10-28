"""Validate a supplied saved catalog; never capture it or contact a provider."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from t3compose.jsonio import read_json_with_digest
from t3compose.routing import parse_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('catalog', type=Path)
    parser.add_argument('--synthetic', action='store_true', help='label synthetic evidence')
    args = parser.parse_args()
    label = 'LIVE synthetic' if args.synthetic else 'LIVE'
    if sys.implementation.name != 'cpython' or sys.version_info < (3, 15) or sys.version_info.releaselevel != 'final':
        print(label + ': stable CPython 3.15 or later required', file=sys.stderr)
        return 2
    try:
        raw, digest = read_json_with_digest(args.catalog, max_bytes=1024 * 1024)
        providers = parse_catalog(raw)
    except (OSError, ValueError, RecursionError):
        print(label + ': saved catalog has invalid or unreadable shape', file=sys.stderr)
        return 2
    print(json.dumps({'label': label, 'validShape': True, 'providerCount': len(providers),
                      'catalogDigest': digest}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
