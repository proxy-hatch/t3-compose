"""Per-instance/model refusal memory and serialized account activation."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import os
import re

from . import jsonio
from .cli import CommandError, ExitCode
from .config import (DEFAULT_STATE_NAME, REFUSAL_KINDS, ConfigValidationError,
                     load_matrix_config, load_quota_state, thaw_value,
                     validate_quota_state_data)


def utc(value) -> datetime:
    """Parse the strict UTC wire format without accepting offsets or local time."""
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
        raise ValueError('invalid UTC timestamp')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def utc_text(moment) -> str:
    """Format an aware moment in UTC, retaining nonzero microseconds."""
    return moment.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def live_refusals(refusals, minutes, now) -> list[dict]:
    """Validate all times and copy live entries with their retry expiry."""
    result = []
    for entry in refusals:
        observed = utc(entry['observedAt'])
        reset = entry['resetAt']
        if observed > now or (reset is not None and utc(reset) < observed):
            raise ValueError('invalid refusal time')
        try:
            expiry = observed + timedelta(minutes=minutes)
        except OverflowError:
            raise ValueError('invalid refusal expiry') from None
        if now < expiry:
            result.append({**entry, 'expiresAt': utc_text(expiry)})
    return result


def _routable(data, instance, model):
    cells = [data['manager'], *data['helpers'].values()]
    cells.extend(cell for lanes in data['matrix'].values()
                 for stages in lanes.values() for cell in stages.values())
    for cell in cells:
        for selection in (cell['primary'], *cell['quotaFallback'],
                          *([cell['prePass']] if 'prePass' in cell else [])):
            if selection['model'] != model:
                continue
            if selection.get('providerInstanceId') == instance:
                return True
            pool = selection.get('providerPool')
            if pool is not None and instance in data['providerPools'][pool]['providerInstanceIds']:
                return True
    return False


@contextmanager
def _locked(local):
    fd = os.open(local / 'agent-routing-state.lock',
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _state(args, cfg, now):
    try:
        loaded = load_quota_state(local=args.local_dir, config=cfg)
        state = thaw_value(loaded.data)
        live = live_refusals(state.get('refusals', []), cfg.data['quota']['refusalRetryMinutes'], now)
        return loaded, state, live
    except (ConfigValidationError, ValueError, RecursionError):
        raise CommandError('invalid routing config or quota state', ExitCode.INVALID_CONFIG) from None


def command(args):
    now = datetime.now(timezone.utc)
    try:
        cfg = load_matrix_config(local=args.local_dir)
    except (ConfigValidationError, ValueError, RecursionError):
        raise CommandError('invalid routing config or quota state', ExitCode.INVALID_CONFIG) from None
    if args.quota_action == 'show':
        loaded, state, live = _state(args, cfg, now)
        return {'state': state, 'liveRefusals': live, 'configDigest': cfg.digest,
                'stateDigest': loaded.digest}

    try:
        if args.quota_action == 'record':
            entry = {'providerInstanceId': args.instance, 'model': args.model,
                     'kind': args.kind, 'observedAt': args.observed_at,
                     'resetAt': args.reset_at, 'evidence': args.evidence}
            live_refusals([entry], cfg.data['quota']['refusalRetryMinutes'], now)
            if (args.kind not in REFUSAL_KINDS or not args.evidence.strip() or
                    len(args.evidence) > 512 or not _routable(cfg.data, args.instance, args.model)):
                raise ValueError('invalid refusal')
        elif (not args.reason.strip() or not any(args.instance in p['providerInstanceIds']
                                               for p in cfg.data['providerPools'].values())):
            raise ValueError('invalid activation')
    except (ValueError, RecursionError):
        raise CommandError('invalid quota update', ExitCode.INVALID_INPUT) from None

    try:
        with _locked(args.local_dir):
            loaded, state, live = _state(args, cfg, now)
            active_move = None
            if args.quota_action == 'record':
                state['refusals'] = [{k: v for k, v in r.items() if k != 'expiresAt'} for r in live]
                state['refusals'].append(entry)
                previous = state['activeProviderInstanceId']
                if previous == args.instance:
                    for pool in cfg.data['providerPools'].values():
                        members = pool['providerInstanceIds']
                        if args.instance in members:
                            state['activeProviderInstanceId'] = members[(members.index(args.instance) + 1) % len(members)]
                            break
                if state['activeProviderInstanceId'] != previous:
                    active_move = {'from': previous, 'to': state['activeProviderInstanceId']}
            else:
                state['activeProviderInstanceId'], state['note'] = args.instance, args.reason
            try:
                validate_quota_state_data(state, cfg.data)
                encoded = jsonio.canonical_json(state)
                if len(encoded) > 16384:
                    raise ValueError('quota state exceeds loader limit')
                live = live_refusals(state.get('refusals', []), cfg.data['quota']['refusalRetryMinutes'], now)
            except (ConfigValidationError, ValueError, RecursionError):
                raise CommandError('invalid quota update', ExitCode.INVALID_INPUT) from None
            jsonio.atomic_replace(args.local_dir / DEFAULT_STATE_NAME, state)
            result = {'state': state, 'liveRefusals': live, 'configDigest': cfg.digest,
                      'previousStateDigest': loaded.digest,
                      'stateDigest': 'sha256:' + hashlib.sha256(encoded).hexdigest()}
            if args.quota_action == 'record':
                result['activeMove'] = active_move
            return result
    except OSError:
        raise CommandError('quota publication failed; inspect state before retrying',
                           ExitCode.INVALID_INPUT) from None


def register(registry):
    for leaf in ('show', 'record', 'activate'):
        parser = registry.group('quota').add_parser(leaf)
        parser.set_defaults(handler=command, quota_action=leaf)
        if leaf == 'show':
            continue
        parser.add_argument('--instance', required=True)
        if leaf == 'record':
            parser.add_argument('--model', required=True)
            parser.add_argument('--kind', choices=REFUSAL_KINDS, required=True)
            parser.add_argument('--observed-at', required=True)
            parser.add_argument('--reset-at', default=None)
            parser.add_argument('--evidence', required=True)
        else:
            parser.add_argument('--reason', required=True)
