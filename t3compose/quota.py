"""Explicit manager-owned quota evidence and account activation."""
from datetime import datetime, timezone
import hashlib

from . import jsonio
from .cli import CommandError, ExitCode
from .config import (DEFAULT_STATE_NAME, ConfigValidationError, load_config,
                     load_quota_state, thaw_value, validate_quota_state_data)
from .routing import evaluate_quota


def quota_status(state, cfg, now):
    """Evaluate every configured account, including those with no observations."""
    return {instance: evaluate_quota(state['quotaObservations'].get(instance, ()), now)
            for pool in cfg.data['providerPools'].values()
            for instance in pool['providerInstanceIds']}


def command(args):
    now = datetime.now(timezone.utc)
    try:
        cfg = load_config(local=args.local_dir)
        loaded = load_quota_state(local=args.local_dir, config=cfg)
        state = thaw_value(loaded.data)
        statuses = quota_status(state, cfg, now)
    except (ConfigValidationError, ValueError, RecursionError):
        raise CommandError('invalid routing config or quota state', ExitCode.INVALID_CONFIG) from None

    result = {'state': state, 'quota': statuses, 'configDigest': cfg.digest,
              'stateDigest': loaded.digest}
    if args.quota_action == 'show':
        return result
    try:
        if args.instance not in statuses:
            raise ValueError('unknown pool member')
        if args.quota_action == 'record':
            entry = {'observedAt': args.observed_at, 'status': args.status,
                     'resetAt': args.reset_at, 'evidence': args.evidence}
            state['quotaObservations'].setdefault(args.instance, []).append(entry)
        else:
            if not isinstance(args.reason, str) or not args.reason.strip():
                raise ValueError('empty activation reason')
            state['activeProviderInstanceId'], state['note'] = args.instance, args.reason
        validate_quota_state_data(state, cfg.data)
        statuses = quota_status(state, cfg, now)
        encoded = jsonio.canonical_json(state)
        if len(encoded) > 16384:
            raise ValueError('quota state exceeds loader limit')
    except (ConfigValidationError, ValueError, RecursionError):
        raise CommandError('invalid quota update', ExitCode.INVALID_INPUT) from None
    try:
        jsonio.atomic_replace(args.local_dir / DEFAULT_STATE_NAME, state)
    except OSError:
        raise CommandError('quota publication failed; inspect state before retrying',
                           ExitCode.INVALID_INPUT) from None
    return {**result, 'quota': statuses, 'previousStateDigest': loaded.digest,
            'stateDigest': 'sha256:' + hashlib.sha256(encoded).hexdigest()}


def register(registry):
    for leaf in ('show', 'record', 'activate'):
        parser = registry.group('quota').add_parser(leaf)
        parser.set_defaults(handler=command, quota_action=leaf)
        if leaf == 'show':
            continue
        parser.add_argument('--instance', required=True)
        if leaf == 'record':
            parser.add_argument('--status', choices=('exhausted', 'available'), required=True)
            parser.add_argument('--observed-at', required=True)
            parser.add_argument('--reset-at', default=None)
            parser.add_argument('--evidence', required=True)
        else:
            parser.add_argument('--reason', required=True)
