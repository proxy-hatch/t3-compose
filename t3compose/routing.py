"""Routing input recording, saved-catalog validation and read-only quota evaluation."""
from datetime import datetime
import re

from . import records, schema
from .cli import CommandError, ExitCode
from .config import ConfigValidationError, load_config
from .jsonio import read_json_with_digest


class CatalogError(ValueError):
    """Bounded diagnostic about the needed saved-catalog shape or freshness."""


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
        raise ValueError('invalid UTC timestamp')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def parse_catalog(raw):
    """Validate needed fields; return providers by ID, including unavailable ones."""
    def require(condition, field):
        if not condition:
            raise CatalogError('malformed or missing catalog ' + field)

    def text(value):
        return isinstance(value, str) and bool(value.strip())

    def indexed(items, key, field):
        require(isinstance(items, list), field)
        result = {}
        for item in items:
            require(isinstance(item, dict) and text(item.get(key)), field + '.' + key)
            require(item[key] not in result, field + ' duplicate identifier')
            result[item[key]] = item
        return result

    require(isinstance(raw, dict), 'object')
    providers = indexed(raw.get('providers'), 'providerInstanceId', 'providers')
    for provider in providers.values():
        require(text(provider.get('driverKind')), 'driverKind')
        for flag in ('canRunChildTask', 'canRunCrossProviderChildTask'):
            require(type(provider.get(flag)) is bool, flag)
        constraints = provider.get('constraints')
        require(isinstance(constraints, list) and all(text(c) for c in constraints), 'constraints')
        for model in indexed(provider.get('models'), 'id', 'models').values():
            for option in indexed(model.get('options', []), 'id', 'options').values():
                values = option.get('values')
                require(isinstance(values, list) and all(type(v) in (str, bool) for v in values),
                        'option.values')
    return providers


def load_catalog(path, read_at, now):
    """Read one bounded saved catalog; its supplied capture time is at most 15 minutes old."""
    try:
        age = (now - utc(read_at)).total_seconds()
        if not 0 <= age <= 900:
            raise CatalogError('catalog read time is stale or in the future')
        raw, digest = read_json_with_digest(path, max_bytes=1024 * 1024)
    except (OSError, ValueError, RecursionError) as error:
        if isinstance(error, CatalogError):
            raise
        raise CatalogError('catalog unreadable, malformed or invalid read time') from None
    return parse_catalog(raw), digest


def evaluate_quota(observations, now):
    """Validated per-account observations -> unknown/exhausted/available, without mutation.

    Latest observation time wins (last entry breaks ties). An elapsed exhaustion
    reset restores unknown quota. Invalid or future observation times raise ValueError.
    """
    latest = None
    for entry in observations:
        observed = utc(entry['observedAt'])
        reset = utc(entry['resetAt']) if entry['resetAt'] is not None else None
        if observed > now or (reset is not None and reset < observed):
            raise ValueError('invalid quota observation time')
        if latest is None or observed >= latest[0]:
            latest = observed, entry['status'], reset
    if latest is None:
        return 'unknown'
    _, status, reset = latest
    if status == 'exhausted' and reset is not None and reset < now:
        return 'unknown'
    return status


def command(args):
    try:
        cfg = load_config(local=args.local_dir)
        loaded = records.load_work(args.local_dir, args.repo, args.work)
        task, _ = read_json_with_digest(args.input, max_bytes=1024 * 1024)
        return records.record_routing_input(args.local_dir, args.repo, args.work, task=task, context=None,
                                            policy_digest=cfg.policy_digest,
                                            writer=loaded['effectiveWork']['writer'])
    except ConfigValidationError:
        raise CommandError('invalid routing config', ExitCode.INVALID_CONFIG) from None
    except (records.RecordError, schema.SchemaError, OSError, ValueError):
        raise CommandError('invalid routing input or records', ExitCode.INVALID_INPUT) from None


def register(registry):
    parser = registry.group('route').add_parser('input')
    parser.set_defaults(handler=command)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--work', required=True)
    parser.add_argument('--input', required=True)
