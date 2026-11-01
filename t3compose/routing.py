"""Version-3 matrix selection with saved capabilities and refusal memory."""
from datetime import datetime, timezone
import re

from . import records, schema
from .cli import CommandError, ExitCode, Outcome
from .config import ConfigValidationError, load_matrix_config, load_quota_state, thaw_value
from .quota import utc, live_refusals
from .jsonio import read_json_with_digest


class CatalogError(ValueError):
    """Bounded diagnostic about the needed saved-catalog shape or freshness."""


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


def _needed(reason, **fields):
    return Outcome({'reason': reason, **fields}, ExitCode.JUDGMENT_NEEDED)


def _current(args, events, cfg):
    if args.input_ref <= 0 or not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', args.policy_digest):
        raise CommandError('invalid routing reference or digest')
    source = next((e for e in reversed(events) if e['kind'] == 'routing-input'), None)
    if source is None or source['seq'] != args.input_ref:
        return None, _needed('record a current routing input')
    if args.policy_digest != cfg.policy_digest or source['data']['policyDigest'] != cfg.policy_digest:
        return None, _needed('routing policy changed; record a current routing input')
    return source, None


def _bounded(reason):
    return ''.join(c if c.isprintable() else ' ' for c in reason)[:512]


def _availability(provider, selection):
    if provider is None:
        return 'missing provider in catalog'
    for flag in ('canRunChildTask', 'canRunCrossProviderChildTask'):
        if not provider[flag]:
            return flag + ' is false'
    if provider['constraints']:
        return 'constraints: ' + '; '.join(provider['constraints'])
    model = next((m for m in provider['models'] if m['id'] == selection['model']), None)
    if model is None:
        return 'model not offered: ' + selection['model']
    options = {o['id']: o['values'] for o in model.get('options', [])}
    for key, value in selection.get('options', {}).items():
        if not any(type(value) is type(v) and value == v for v in options.get(key, [])):
            return 'option not offered: ' + key
    return None


def _select(args, cfg, loaded, source):
    data = cfg.data
    refs = {k: source['data'][k] for k in ('inputDigest', 'policyDigest')}
    refs['inputRef'] = source['seq']
    decision = next((e for e in reversed(loaded['events'])
                     if e['kind'] in ('routing-classification', 'routing-judgment')
                     and e['data'].get('configVersion') == 3
                     and all(e['data'][k] == v for k, v in refs.items())), None)
    if decision is None:
        return _needed('record a judgment or classification for the current input')
    facts = decision['data']
    if decision['kind'] == 'routing-classification':
        if facts['outcome'] != 'accepted' or any(
                a['confidence'] < data['jev']['minConfidence'] for a in facts['answers'].values()):
            return _needed('record a judgment')
        lane, criticality, scope = (facts['answers'][q]['choice'] for q in ('lane', 'criticality', 'scope'))
    else:
        lane, criticality, scope = (facts[q] for q in ('lane', 'criticality', 'scope'))
    floors = []
    for fact in source['data']['task']['riskFacts']:
        if fact not in data['riskFacts']:
            return _needed('unknown declared risk fact')
        floor = data['riskFacts'][fact]
        if floor.get('scope') == 'COMPLEX':
            scope = 'COMPLEX'
        if floor.get('criticality') == 'CORE':
            criticality = 'CORE'
        if ('scope' in floor or 'criticality' in floor) and fact not in floors:
            floors.append(fact)
    start = 2 if scope == 'COMPLEX' else 1
    if args.stage < start:
        return _needed('requested stage is below the starting stage', startingStage=start)
    if args.role == 'feature-lead':
        lane = 'default'
    elif args.role == 'reviewer':
        lane = 'core' if criticality == 'CORE' else 'non-critical'
    else:
        lane = 'ui-frontend' if lane == 'UI_FRONTEND' else 'backend-logic'
    cell = data['matrix'][args.role][lane][str(args.stage)]
    try:
        state = load_quota_state(local=args.local_dir, config=cfg)
    except (ValueError, RecursionError):
        raise ConfigValidationError([]) from None
    now = datetime.now(timezone.utc)
    try:
        catalog, catalog_digest = load_catalog(args.catalog, args.catalog_read_at, now)
    except CatalogError as error:
        return _needed(_bounded(str(error)))
    try:
        live = live_refusals(state.data.get('refusals', ()), data['quota']['refusalRetryMinutes'], now)
    except (ValueError, RecursionError):
        raise ConfigValidationError([]) from None
    for manager in (data['manager']['primary'], *data['manager']['quotaFallback']):
        provider = catalog.get(manager.get('providerInstanceId'))
        if provider is not None and provider['driverKind'] not in ('claudeAgent', 'codex', 'antigravity'):
            raise ConfigValidationError([])
    memory = {}
    for entry in live:
        key = entry['providerInstanceId'], entry['model']
        if key not in memory or utc(entry['expiresAt']) > utc(memory[key]):
            memory[key] = entry['expiresAt']
    tried, retries = [], []
    for index, frozen in enumerate((cell['primary'], *cell['quotaFallback'])):
        selection = thaw_value(frozen)
        if 'providerPool' in selection:
            accounts = list(data['providerPools'][selection['providerPool']]['providerInstanceIds'])
            active = state.data['activeProviderInstanceId']
            offset = accounts.index(active) if active in accounts else 0
            accounts = accounts[offset:] + accounts[:offset]
        else:
            accounts = [None]
        for account in accounts:
            instance = account if account is not None else selection['providerInstanceId']
            provider = catalog.get(instance)
            reason = _availability(provider, selection)
            if reason is None and provider['driverKind'] not in ('claudeAgent', 'codex', 'antigravity', 'pi'):
                reason = 'driver kind not allowed: ' + provider['driverKind']
            if reason is None:
                block = next((b for b in data['roleBlocks']
                              if b['driverKind'] == provider['driverKind'] and b['role'] == args.role), None)
                if block is not None:
                    reason = 'role block: ' + block['reason']
            if reason is None and (instance, selection['model']) in memory:
                expiry = memory[instance, selection['model']]
                reason = 'live quota refusal until ' + expiry
                retries.append(expiry)
            tried.append({'index': index, 'selection': selection, 'account': account,
                          'reason': _bounded(reason or 'selected')})
            if reason is not None:
                continue
            payload = {**refs, 'configVersion': 3, 'classificationRef': decision['seq'],
                       'role': args.role, 'lane': lane, 'stage': args.stage,
                       'pass': 'record' if args.role == 'reviewer' else None, 'round': args.round,
                       'criticality': criticality, 'scope': scope, 'floorsApplied': floors,
                       'selection': selection, 'chosenPoolAccount': account, 'targetsTried': tried,
                       'chosenIndex': index, 'configDigest': cfg.digest, 'catalogReadAt': args.catalog_read_at,
                       'catalogDigest': catalog_digest, 'quotaStateDigest': state.digest}
            if provider['driverKind'] == 'pi':
                payload['billing'] = 'per-token'
            event = records.append_event(args.local_dir, args.repo, args.work, 'routing-selection', payload,
                                         writer=loaded['effectiveWork']['writer'])
            return {**payload, 'selectionRef': event['seq'], 'providerInstanceId': instance,
                    'model': selection['model'], 'options': selection.get('options', {})}
    return Outcome({'reason': 'no eligible target in the cell',
                    'retryAfter': min(retries, key=utc) if retries else None, 'targetsTried': tried},
                   ExitCode.REPORT)


def command(args):
    try:
        try:
            cfg = load_matrix_config(local=args.local_dir)
        except (ValueError, RecursionError):
            raise ConfigValidationError([]) from None
        if args.route_action == 'select' and (
                (args.round == 'corrections' and args.role != 'worker') or
                (args.round == 're-review' and args.role != 'reviewer')):
            raise CommandError('invalid round for routing role')
        loaded = records.load_work(args.local_dir, args.repo, args.work)
        if args.route_action == 'input':
            task, _ = read_json_with_digest(args.input, max_bytes=1024 * 1024)
            return records.record_routing_input(args.local_dir, args.repo, args.work, task=task, context=None,
                                               policy_digest=cfg.policy_digest,
                                               writer=loaded['effectiveWork']['writer'])
        source, failure = _current(args, loaded['events'], cfg)
        if failure is not None:
            return failure
        if args.route_action == 'select':
            return _select(args, cfg, loaded, source)
        return records.append_event(args.local_dir, args.repo, args.work, 'routing-judgment', {
            'configVersion': 3, 'inputRef': source['seq'], 'inputDigest': source['data']['inputDigest'],
            'policyDigest': cfg.policy_digest, 'lane': args.lane, 'criticality': args.criticality,
            'scope': args.scope, 'reason': args.reason, 'basis': 'manual', 'defaulted': []},
            writer=loaded['effectiveWork']['writer'])
    except ConfigValidationError:
        raise CommandError('invalid version-3 routing config or quota state', ExitCode.INVALID_CONFIG) from None
    except (records.RecordError, schema.SchemaError, OSError, ValueError, RecursionError):
        raise CommandError('invalid routing input or records', ExitCode.INVALID_INPUT) from None


def register(registry):
    for leaf in ('input', 'judge', 'select'):
        parser = registry.group('route').add_parser(leaf)
        parser.set_defaults(handler=command, route_action=leaf)
        parser.add_argument('--repo', required=True)
        parser.add_argument('--work', required=True)
        if leaf == 'input':
            parser.add_argument('--input', required=True)
            continue
        parser.add_argument('--input-ref', type=int, required=True)
        parser.add_argument('--policy-digest', required=True)
        if leaf == 'judge':
            parser.add_argument('--lane', choices=('BACKEND_LOGIC', 'UI_FRONTEND'), required=True)
            parser.add_argument('--criticality', choices=('CORE', 'NON_CRITICAL'), required=True)
            parser.add_argument('--scope', choices=('STANDARD', 'COMPLEX'), required=True)
            parser.add_argument('--reason', required=True)
        else:
            parser.add_argument('--role', choices=('worker', 'reviewer', 'feature-lead'), required=True)
            parser.add_argument('--stage', type=int, choices=(1, 2, 3), required=True)
            parser.add_argument('--round', choices=('first', 'corrections', 're-review'), default='first')
            parser.add_argument('--catalog', required=True)
            parser.add_argument('--catalog-read-at', required=True)
