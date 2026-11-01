"""Version-3 matrix selection with saved capabilities and refusal memory."""
from datetime import datetime, timezone
import re

from . import briefs, records, schema
from .cli import CommandError, ExitCode, Outcome
from .config import ConfigValidationError, load_matrix_config, load_quota_state, thaw_value
from .quota import utc, live_refusals
from .jsonio import read_json_with_digest


CATALOG_MAX_AGE_SECONDS = 86400


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
    """Read one bounded saved catalog; its supplied capture time is at most 24 hours old."""
    try:
        age = (now - utc(read_at)).total_seconds()
        if not 0 <= age <= CATALOG_MAX_AGE_SECONDS:
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


def _decision(args, cfg, loaded, source):
    data = cfg.data
    refs = {k: source['data'][k] for k in ('inputDigest', 'policyDigest')}
    refs['inputRef'] = source['seq']
    decision = next((e for e in reversed(loaded['events'])
                     if e['kind'] in ('routing-classification', 'routing-judgment')
                     and e['data'].get('configVersion') == 3
                     and all(e['data'][k] == v for k, v in refs.items())), None)
    if decision is None:
        return None, _needed('record a judgment or classification for the current input')
    facts = decision['data']
    if decision['kind'] == 'routing-classification':
        if facts['outcome'] != 'accepted' or any(
                a['confidence'] < data['jev']['minConfidence'] for a in facts['answers'].values()):
            return None, _needed('record a judgment')
        lane, criticality, scope = (facts['answers'][q]['choice'] for q in ('lane', 'criticality', 'scope'))
    else:
        lane, criticality, scope = (facts[q] for q in ('lane', 'criticality', 'scope'))
    floors = []
    for fact in source['data']['task']['riskFacts']:
        if fact not in data['riskFacts']:
            return None, _needed('unknown declared risk fact')
        floor = data['riskFacts'][fact]
        if floor.get('scope') == 'COMPLEX':
            scope = 'COMPLEX'
        if floor.get('criticality') == 'CORE':
            criticality = 'CORE'
        if ('scope' in floor or 'criticality' in floor) and fact not in floors:
            floors.append(fact)
    start = 2 if scope == 'COMPLEX' else 1
    if args.role == 'feature-lead':
        lane = 'default'
    elif args.role == 'reviewer':
        lane = 'core' if criticality == 'CORE' else 'non-critical'
    else:
        lane = 'ui-frontend' if lane == 'UI_FRONTEND' else 'backend-logic'
    return dict(refs=refs, decision=decision, criticality=criticality, scope=scope,
                floors=floors, lane=lane, start=start), None


def _reached(events, source, role, lane, start):
    reached = start
    for event in events:
        if event['seq'] <= source['seq'] or event['kind'] != 'routing-escalation':
            continue
        link = event['data']
        if (link['role'] == role and link['lane'] == lane and
                link['fromStage'] == reached and link['toStage'] == reached + 1):
            reached += 1
    return reached


def _evidence(path):
    try:
        with open(path, 'rb') as stream:
            raw = stream.read(schema.MAX_ESCALATION_EVIDENCE_BYTES + 1)
        if len(raw) > schema.MAX_ESCALATION_EVIDENCE_BYTES:
            raise ValueError
        text = raw.decode('utf-8')
        if not text.strip():
            raise ValueError
        return text
    except (OSError, ValueError):
        raise CommandError('invalid escalation evidence file') from None


def _escalate(args, cfg, loaded, source):
    evidence = _evidence(args.evidence_file)
    # Validate the existing schema before prerequisite checks, using a valid
    # temporary lane. The actual lane always comes from the current decision.
    payload = dict(role=args.role, lane={'worker': 'backend-logic', 'reviewer': 'core',
                   'feature-lead': 'default'}[args.role], fromStage=args.from_stage,
                   toStage=args.from_stage + 1, reason=args.reason, evidence=evidence,
                   wipCommit=args.wip_commit)
    schema.validate_event_data('routing-escalation', payload)
    if args.reason == 'worker-hard-stop':
        parsed = briefs.parse_escalation(evidence)
        if (parsed['wipCommit'] != args.wip_commit.lower() or
                parsed['reviewBase'] != loaded['work']['reviewBase'].lower()):
            raise CommandError('escalation commits do not match the work and WIP commit')
        payload['evidence'] = parsed['block']
    context, failure = _decision(args, cfg, loaded, source)
    if failure is not None:
        return failure
    payload['lane'] = context['lane']
    reached = _reached(loaded['events'], source, args.role, context['lane'], context['start'])
    if args.from_stage != reached:
        return _needed('escalation must start at the currently reached stage', reachedStage=reached)
    return records.append_event(args.local_dir, args.repo, args.work, 'routing-escalation', payload,
                                writer=loaded['effectiveWork']['writer'])


def _select(args, cfg, loaded, source):
    data = cfg.data
    refs = {k: source['data'][k] for k in ('inputDigest', 'policyDigest')}
    refs['inputRef'] = source['seq']
    helper = args.role in ('verifier', 'text')
    if helper:
        decision = None
        lane = criticality = scope = None
        floors = []
        cell = data['helpers'][args.role]
    else:
        context, failure = _decision(args, cfg, loaded, source)
        if failure is not None:
            return failure
        decision, lane = context['decision'], context['lane']
        criticality, scope, floors = context['criticality'], context['scope'], context['floors']
        if args.stage < context['start']:
            return _needed('requested stage is below the starting stage', startingStage=context['start'])
        reached = _reached(loaded['events'], source, args.role, lane, context['start'])
        if args.stage > reached:
            return _needed('record escalation chain to the requested stage', reachedStage=reached)
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
    pre = args.pass_ == 'pre'
    if pre:
        targets = (cell['prePass'],) if 'prePass' in cell else ()
    elif helper:
        targets = (cell['primary'],)
    else:
        targets = (cell['primary'], *cell['quotaFallback'])
    for index, frozen in enumerate(targets):
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
            payload = {**refs, 'configVersion': 3, 'classificationRef': decision['seq'] if decision else None,
                       'role': args.role, 'lane': lane, 'stage': args.stage,
                       'pass': args.pass_, 'round': args.round,
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
    if pre:
        return {'skipped': True, 'reason': 'no eligible pre-pass target' if targets else 'no configured pre-pass',
                'retryAfter': min(retries, key=utc) if retries else None, 'targetsTried': tried}
    return Outcome({'reason': 'no eligible target in the cell',
                    'retryAfter': min(retries, key=utc) if retries else None, 'targetsTried': tried},
                   ExitCode.REPORT)


def command(args):
    try:
        try:
            cfg = load_matrix_config(local=args.local_dir)
        except (ValueError, RecursionError):
            raise ConfigValidationError([]) from None
        if args.route_action == 'select':
            helper = args.role in ('verifier', 'text')
            if helper:
                if any(v is not None for v in (args.stage, args.round, args.pass_)):
                    raise CommandError('helpers do not accept stage, round or pass')
            else:
                if args.stage is None:
                    raise CommandError('matrix roles require stage')
                if args.pass_ is not None and args.role != 'reviewer':
                    raise CommandError('pass is reviewer-only')
                args.round = args.round or 'first'
                if ((args.round == 'corrections' and args.role != 'worker') or
                        (args.round == 're-review' and args.role != 'reviewer')):
                    raise CommandError('invalid round for routing role')
                if args.role == 'reviewer':
                    args.pass_ = args.pass_ or 'record'
        loaded = records.load_work(args.local_dir, args.repo, args.work)
        if args.route_action == 'input':
            task, _ = read_json_with_digest(args.input, max_bytes=1024 * 1024)
            return records.record_routing_input(args.local_dir, args.repo, args.work, task=task, context=None,
                                               policy_digest=cfg.policy_digest,
                                               writer=loaded['effectiveWork']['writer'])
        source, failure = _current(args, loaded['events'], cfg)
        if failure is not None:
            return failure
        if args.route_action == 'escalate':
            return _escalate(args, cfg, loaded, source)
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
    for leaf in ('input', 'judge', 'select', 'escalate'):
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
        elif leaf == 'escalate':
            parser.add_argument('--role', choices=('worker', 'reviewer', 'feature-lead'), required=True)
            parser.add_argument('--from-stage', type=int, choices=(1, 2), required=True)
            parser.add_argument('--reason', required=True)
            parser.add_argument('--evidence-file', required=True)
            parser.add_argument('--wip-commit')
        else:
            parser.add_argument('--role', choices=('worker', 'reviewer', 'feature-lead', 'verifier', 'text'), required=True)
            parser.add_argument('--stage', type=int, choices=(1, 2, 3))
            parser.add_argument('--round', choices=('first', 'corrections', 're-review'))
            parser.add_argument('--pass', dest='pass_', choices=('pre', 'record'))
            parser.add_argument('--catalog', required=True)
            parser.add_argument('--catalog-read-at', required=True)
