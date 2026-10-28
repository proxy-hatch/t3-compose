"""Recorded routing decisions, subscription availability and read-only quota walks."""
from datetime import datetime, timezone
import re

from . import records, schema
from .cli import CommandError, ExitCode, Outcome
from .config import ConfigValidationError, load_config, load_quota_state, thaw_value
from .jsonio import read_json_with_digest

TIERS = ('easy', 'medium', 'challenging')
SUBSCRIPTIONS = {'claudeAgent', 'codex', 'antigravity'}


class CatalogError(ValueError):
    """Bounded diagnostic about the needed saved-catalog shape or freshness."""


def bounded(text):
    return ''.join(c if c.isprintable() else ' ' for c in text)[:512]


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


class RoutingStop(Exception):
    def __init__(self, reason, code=ExitCode.JUDGMENT_NEEDED, **data):
        self.outcome = Outcome({'reason': bounded(reason), **data}, code)


def current_input(args, loaded, cfg):
    if args.input_ref <= 0 or not re.fullmatch(r'sha256:[0-9a-f]{64}', args.policy_digest):
        raise CommandError('invalid input reference or policy digest', ExitCode.INVALID_INPUT)
    inputs = [e for e in loaded['events'] if e['kind'] == 'routing-input']
    if not inputs or inputs[-1]['seq'] != args.input_ref:
        raise RoutingStop('latest routing input required')
    data = inputs[-1]['data']
    if args.policy_digest != cfg.policy_digest or data['policyDigest'] != cfg.policy_digest:
        raise RoutingStop('routing policy changed; record a current input and decision')
    return {'inputRef': args.input_ref, 'inputDigest': data['inputDigest'],
            'policyDigest': cfg.policy_digest}, data['task']


def decision_for(events, source, cfg):
    decisions = [e for e in events if e['kind'] in ('routing-classification', 'routing-judgment')
                 and all(e['data'][k] == v for k, v in source.items())]
    if not decisions:
        raise RoutingStop('current classification or judgment required')
    event = decisions[-1]
    data = event['data']
    if event['kind'] == 'routing-judgment':
        return event['seq'], data['domain'], data['difficulty'].lower()
    if data['outcome'] != 'accepted':
        raise RoutingStop('classification requires manager judgment')
    answers = data['answers']
    if any(a['confidence'] < cfg['jev']['minConfidence'] for a in answers.values()):
        raise RoutingStop('classification confidence requires manager judgment')
    return event['seq'], answers['domain']['choice'], answers['difficulty']['choice'].lower()


def tier_for(args, task, difficulty, events, cfg):
    facts = task['riskFacts']
    if any(f not in cfg['riskFacts'] for f in facts):
        raise RoutingStop('unknown declared risk fact')
    floors = list(facts)
    tier = max([difficulty, *[cfg['riskFacts'][f]['floor'] for f in facts]], key=TIERS.index)
    workers = [e['data'] for e in events if e['kind'] == 'routing-selection'
               and e['data']['role'] == 'worker']
    if args.role == 'worker':
        if args.round == 'corrections':
            tier, floors = cfg['roundTiers']['corrections'], []
        if args.escalate:
            prior = [d['tier'] for d in workers if d['round'] == args.round]
            if not prior or max(prior, key=TIERS.index) == 'challenging':
                raise RoutingStop('escalation needs a prior same-round Worker below challenging')
            tier = max(tier, TIERS[TIERS.index(max(prior, key=TIERS.index)) + 1], key=TIERS.index)
    elif args.role in ('reviewer', 'feature-lead'):
        if args.role == 'reviewer' and args.review_scope in ('document', 'corrections-delta'):
            key = 'documentReview' if args.review_scope == 'document' else 'correctionsReview'
            tier, floors = cfg['roundTiers'][key], []
        else:
            tier = max([tier, *[d['tier'] for d in workers]], key=TIERS.index)
    if args.role in ('reviewer', 'feature-lead'):
        tier = max(tier, 'medium', key=TIERS.index)
    return tier, floors


def provider_status(provider):
    if provider is None:
        return 'missing provider in catalog'
    for flag in ('canRunChildTask', 'canRunCrossProviderChildTask'):
        if not provider[flag]:
            return flag + ' is false'
    if provider['constraints']:
        return bounded('constraints: ' + '; '.join(provider['constraints']))
    return None


def unavailable(provider, selection, role):
    reason = provider_status(provider)
    if reason:
        return reason
    if provider['driverKind'] not in SUBSCRIPTIONS:
        return 'non-subscription driver unavailable'
    if role == 'reviewer' and provider['driverKind'] == 'antigravity':
        return 'role limit: antigravity never Reviewer'
    model = next((m for m in provider['models'] if m['id'] == selection['model']), None)
    if model is None:
        return bounded('model not offered: missing model ' + selection['model'])
    options = {o['id']: o['values'] for o in model.get('options', [])}
    for key, value in selection['options'].items():
        if key not in options or not any(type(v) is type(value) and v == value for v in options[key]):
            return bounded('model not offered: missing option ' + key)
    return None


def resolve(selection, role, cfg, state, catalog, quotas, skips):
    if 'roles' in selection and role not in selection['roles']:
        return None, 'role limit on selection'
    if 'providerPool' not in selection:
        instance = selection['providerInstanceId']
        return instance, unavailable(catalog.get(instance), selection, role)
    if role not in ('worker', 'text-helper'):
        return None, 'role limit on pool selection'
    members = cfg['providerPools'][selection['providerPool']]['providerInstanceIds']
    active = state['activeProviderInstanceId']
    reason = provider_status(catalog.get(active))
    if active not in members or reason:
        raise RoutingStop(reason or 'active account outside selected pool', skippedPoolAccounts=list(skips))
    start = members.index(active)
    for instance in [*members[start:], *members[:start]]:
        reason = unavailable(catalog.get(instance), selection, role)
        if reason is None and quotas.get(instance, 'unknown') == 'exhausted':
            reason = 'confirmed exhausted account'
        if reason is None:
            return instance, None
        skips.append({'providerInstanceId': instance, 'reason': reason})
    return None, 'no eligible offering pool account'


def selection_identity(selection):
    return {k: v for k, v in selection.items() if k != 'roles'}


def select(args, loaded, cfg, source, task):
    if args.escalate and args.role != 'worker':
        raise CommandError('escalation requires Worker role', ExitCode.INVALID_INPUT)
    data = cfg.data
    classification, domain, tier, floors = None, None, 'easy', []
    if args.role == 'text-helper':
        preferred = data['textHelper']
    else:
        classification, domain, difficulty = decision_for(loaded['events'], source, data)
        tier, floors = tier_for(args, task, difficulty, loaded['events'], data)
        domain_key = 'software-engineering' if domain == 'SOFTWARE_ENGINEERING' else 'non-engineering'
        preferred = data['easyWorker'] if tier == 'easy' else data['modelPreferences'][domain_key][tier]
    now = datetime.now(timezone.utc)
    try:
        catalog, catalog_digest = load_catalog(args.catalog, args.catalog_read_at, now)
    except CatalogError as error:
        raise RoutingStop(str(error), skippedPoolAccounts=[]) from None
    master = catalog.get(data['master']['providerInstanceId'])
    if master is not None and master['driverKind'] not in SUBSCRIPTIONS:
        raise CommandError('master requires a subscription driver', ExitCode.INVALID_CONFIG)
    state = load_quota_state(local=args.local_dir, config=cfg)
    try:
        quotas = {i: evaluate_quota(obs, now) for i, obs in state.data['quotaObservations'].items()}
    except ValueError:
        raise CommandError('invalid quota observation time', ExitCode.INVALID_CONFIG) from None
    skips = []
    chosen, reason = resolve(preferred, args.role, data, state.data, catalog, quotas, skips)
    selection, fallback = preferred, None
    if reason:
        fallback = reason
        chains = [c for c in data['availabilityFallback']['chains'] if c['tier'] == tier
                  and selection_identity(c['from']) == selection_identity(preferred)]
        if args.role not in data['availabilityFallback']['roles'] or not chains:
            raise RoutingStop(reason, ExitCode.REPORT, skippedPoolAccounts=skips)
        selection = chains[0]['to'][0]
        try:
            chosen, reason = resolve(selection, args.role, data, state.data, catalog, quotas, skips)
        except RoutingStop as stop:
            stop.outcome.data['fallbackReason'] = fallback
            raise
        if reason:
            raise RoutingStop(reason, ExitCode.REPORT, fallbackReason=fallback, skippedPoolAccounts=skips)
    payload = {**source, 'classificationRef': classification, 'role': args.role, 'round': args.round,
        'reviewScope': args.review_scope if args.role == 'reviewer' else None,
        'tier': tier, 'floorsApplied': floors, 'escalation': args.escalate,
        'selection': thaw_value(selection),
        'chosenPoolAccount': chosen if 'providerPool' in selection else None,
        'skippedPoolAccounts': skips, 'availabilityReason': 'catalog available; quota not exhausted',
        'fallbackReason': fallback, 'configDigest': cfg.digest, 'catalogReadAt': args.catalog_read_at,
        'catalogDigest': catalog_digest, 'quotaStateDigest': state.digest}
    event = records.append_event(args.local_dir, args.repo, args.work, 'routing-selection', payload,
                                 writer=loaded['effectiveWork']['writer'])
    return {**payload, 'selectionRef': event['seq'], 'providerInstanceId': chosen,
            'model': selection['model'], 'options': thaw_value(selection['options'])}


def command(args):
    try:
        cfg = load_config(local=args.local_dir)
        loaded = records.load_work(args.local_dir, args.repo, args.work)
        writer = loaded['effectiveWork']['writer']
        if args.route_action == 'input':
            task, _ = read_json_with_digest(args.input, max_bytes=1024 * 1024)
            return records.record_routing_input(args.local_dir, args.repo, args.work, task=task,
                                                 context=None, policy_digest=cfg.policy_digest, writer=writer)
        source, task = current_input(args, loaded, cfg)
        if args.route_action == 'judge':
            return records.append_event(args.local_dir, args.repo, args.work, 'routing-judgment',
                {**source, 'domain': args.domain, 'difficulty': args.difficulty,
                 'reason': args.reason, 'basis': 'manual'}, writer=writer)
        return select(args, loaded, cfg, source, task)
    except RoutingStop as stop:
        return stop.outcome
    except ConfigValidationError:
        raise CommandError('invalid routing config or quota state', ExitCode.INVALID_CONFIG) from None
    except (records.RecordError, schema.SchemaError, OSError, ValueError):
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
            parser.add_argument('--domain', choices=('SOFTWARE_ENGINEERING', 'OTHER'), required=True)
            parser.add_argument('--difficulty', choices=('EASY', 'MEDIUM', 'CHALLENGING'), required=True)
            parser.add_argument('--reason', required=True)
        else:
            parser.add_argument('--role', choices=('worker', 'reviewer', 'feature-lead', 'text-helper'), required=True)
            parser.add_argument('--round', choices=('first', 'corrections'), default='first')
            parser.add_argument('--review-scope', choices=('code', 'document', 'corrections-delta'), default='code')
            parser.add_argument('--catalog', required=True)
            parser.add_argument('--catalog-read-at', required=True)
            parser.add_argument('--escalate', action='store_true')
