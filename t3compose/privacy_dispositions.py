"""Public-text standing authority, prior-publication evidence and occurrence joins.

Policy logic over explicit facts that the trusted privacy facade supplies: this
module reads no policy file, starts no process, scans nothing and never imports
the facade; its only I/O is observing the records stream. A complete literal
proof is necessary and never sufficient. Scope comes only from a recorded owner
decision plus independently attested, exact occurrence evidence that is bound to
the facade's fingerprints of the exact selection and of each finding's place in
it. Provenance is cooperative, not authentication.
"""
from datetime import date, datetime, timezone
from functools import wraps
import hashlib
import re

from . import jsonio, records

POLICY_VERSION = 'public-text/v2'
SCHEMA = 2
SCOPE = 'public-product-text'
FIELDS = ('branch', 'file', 'label', 'message', 'path', 'text')
CLASSES = (SCOPE, 'ambiguous', 'credential', 'private-path', 'unrelated')
DESCRIPTOR = dict(authority='owner', contextVersion=1, policyVersion=POLICY_VERSION, schemaVersion=SCHEMA,
                  scope=SCOPE, verification='occurrence-attestation')
MAX_ENTRIES, MAX_OCCURRENCES = 256, 4096
OCCURRENCE = ('patternLine', 'byteOffset', 'byteLength', 'literalDigest', 'publicBlobOid', 'publicContentDigest')
OBSERVED = ('authorityEventCount', 'authorityCheckpoint', 'authorityWorkDigest', 'authorityEventsDigest',
            'observedStart', 'observedEnd')
_ID, _DIGEST, _SHA = r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', r'sha256:[0-9a-f]{64}', r'[0-9a-f]{40}'
_QUESTION = re.compile(r'privacy-public-text/v1 (approve|revoke) (%s) (%s)' % (_ID, _DIGEST))


class Refusal(ValueError):
    """Invalid, stale or unproven evidence. The message never carries an input value."""


def _require(condition):
    if not condition:
        raise Refusal('invalid public-text evidence')


def _boundary(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, TypeError, RecursionError, OverflowError):  # Refusal is a ValueError
            raise Refusal('invalid public-text evidence') from None
    return call


def digest(value):
    return 'sha256:' + hashlib.sha256(jsonio.canonical_json(value)).hexdigest()


def _closed(value, keys):
    _require(type(value) is dict and set(value) == set(keys.split()))
    if 'schemaVersion' in value:
        _require(type(value['schemaVersion']) is int and value['schemaVersion'] == SCHEMA)


def _shape(value, pattern):
    _require(isinstance(value, str) and re.fullmatch(pattern, value) is not None)


def _text(value):
    _require(isinstance(value, str) and bool(value.strip()) and '\0' not in value)
    value.encode('utf-8')


def _name(value):
    """An exact relative file name: no empty, dot or dotdot segment, wildcard or backslash."""
    _text(value)
    _require(not any(c in value for c in '\\*?[]') and all(p not in {'', '.', '..'} for p in value.split('/')))


def _rule(rule, repository):
    _closed(rule, 'schemaVersion ruleId repository scope fields policyVersion policyDigest authorityWorkId '
                  'approvalSeq source')
    _shape(rule['ruleId'], _ID)
    _shape(rule['authorityWorkId'], _ID)
    fields = rule['fields']
    _require(rule['repository'] == repository and rule['scope'] == SCOPE
             and rule['policyVersion'] == POLICY_VERSION and rule['policyDigest'] == digest(DESCRIPTOR)
             and type(fields) is list and bool(fields) and all(isinstance(f, str) for f in fields)
             and fields == sorted(set(fields)) and set(fields) <= set(FIELDS))
    source = rule['source']
    _closed(source, 'sourceRef date quote')
    for key in source:
        _text(source[key])
    _require(re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', source['date'])
             and date.fromisoformat(source['date']).isoformat() == source['date'])


def _attested(document, method, keys, rule, context):
    _closed(document, 'schemaVersion ruleId ruleContext verifier sourceRef method result ' + keys)
    _require(document['ruleId'] == rule['ruleId'] and document['ruleContext'] == context
             and document['method'] == method and document['result'] == 'verified')
    _text(document['verifier'])
    _text(document['sourceRef'])


def _entries(items):
    _require(type(items) is list and len(items) <= MAX_ENTRIES)
    entries, total = {}, 0
    for item in items:
        _closed(item, 'field context inputDigest occurrences')
        _shape(item['context'], _DIGEST)
        _shape(item['inputDigest'], _DIGEST)
        _require(item['field'] in FIELDS)
        key, occurrences = (item['field'], item['context'], item['inputDigest']), item['occurrences']
        _require(key not in entries and type(occurrences) is list and bool(occurrences))
        total += len(occurrences)
        _require(total <= MAX_OCCURRENCES)
        rows = []
        for occurrence in occurrences:
            _closed(occurrence, ' '.join(OCCURRENCE) + ' scope')
            _require(all(type(occurrence[k]) is int for k in OCCURRENCE[:3]) and occurrence['patternLine'] >= 1
                     and occurrence['byteOffset'] >= 0 and occurrence['byteLength'] >= 1
                     and occurrence['scope'] in CLASSES)
            _shape(occurrence['literalDigest'], _DIGEST)
            _shape(occurrence['publicBlobOid'], _SHA)
            _shape(occurrence['publicContentDigest'], _DIGEST)
            rows.append(tuple(occurrence[k] for k in OCCURRENCE) + (occurrence['scope'],))
        entries[key] = tuple(sorted(rows))
    return entries


def _authority(local_dir, rule, context):
    try:
        loaded = records.load_work(local_dir, rule['repository'], rule['authorityWorkId'])
    except ValueError:
        raise Refusal('invalid public-text evidence') from None
    events, source, seq = loaded['events'], rule['source'], rule['approvalSeq']
    _require(type(seq) is int and 1 <= seq <= len(events))
    approval = events[seq - 1]
    _require(approval['kind'] == 'decision' and approval['data'] == dict(
        question='privacy-public-text/v1 approve %s %s' % (rule['ruleId'], context),
        answer=source['quote'], date=source['date'], decidedBy='owner'))
    for event in events:
        data = event['data']
        # Near-miss spellings of the namespace are in it: they refuse, they are never ignored.
        if event['kind'] != 'decision' or not data['question'].strip().lower().startswith('privacy-public-text'):
            continue
        match = _QUESTION.fullmatch(data['question'])
        _require(match is not None and data['decidedBy'] == 'owner' and isinstance(data['answer'], str)
                 and bool(data['answer'].strip()) and isinstance(data['date'], str))
        action, identity, bound = match.groups()
        if identity == rule['ruleId']:
            # A revoke is terminal even when it names an earlier context; another context is ambiguous.
            _require(action == 'approve' and bound == context)
    return dict(authorityEventCount=len(events), authorityCheckpoint=records.checkpoint(loaded['work'], events),
                authorityWorkDigest=digest(loaded['work']), authorityEventsDigest=digest(events))


@_boundary
def observe(policy, read, *, local_dir, repository, list_digest):
    """Validate the evidence and join current authority; return bindings, entries and authority facts.

    repository is the canonical origin identity and list_digest the one captured list's digest.
    read(name, digest) returns the strict-JSON document pinned by that raw digest, from a relative
    name it reads once. Any invalid, mismatched, unattested or revoked evidence raises Refusal with
    a constant message. This is an observation of cooperative records at one moment: no
    authentication and no atomic revocation.
    """
    started = datetime.now(timezone.utc).isoformat()
    _require(isinstance(repository, str) and records.canonical_identity(repository) == repository)  # pure
    _shape(list_digest, _DIGEST)
    kinds = ('rule', 'publication', 'dispositions')
    _closed(policy, 'schemaVersion policyVersion authorityWorkId artifactPolicyFile artifactPolicyDigest '
                    + ' '.join(kind + 'File ' + kind + 'Digest' for kind in kinds))
    _require(policy['policyVersion'] == POLICY_VERSION)
    for kind in kinds:
        _name(policy[kind + 'File'])
        _shape(policy[kind + 'Digest'], _DIGEST)
    artifact = (policy['artifactPolicyFile'], policy['artifactPolicyDigest'])
    if artifact != (None, None):
        _name(artifact[0])
        _shape(artifact[1], _DIGEST)
    rule, publication, dispositions = (read(policy[kind + 'File'], policy[kind + 'Digest']) for kind in kinds)
    _rule(rule, repository)
    _require(rule['authorityWorkId'] == policy['authorityWorkId'])
    context = digest({k: v for k, v in rule.items() if k != 'approvalSeq'})
    _attested(publication, 'prior-publication', 'repository publicCommit', rule, context)
    _attested(dispositions, 'occurrence-review', 'publicCommit listDigest selectionContext entries', rule, context)
    _shape(publication['publicCommit'], _SHA)
    _shape(dispositions['selectionContext'], _DIGEST)
    _require(publication['repository'] == repository and dispositions['publicCommit'] == publication['publicCommit']
             and dispositions['listDigest'] == list_digest)
    entries = _entries(dispositions['entries'])
    authority = _authority(local_dir, rule, context)
    return dict(
        bindings=dict(ruleId=rule['ruleId'], ruleContext=context, repository=repository, scope=rule['scope'],
                      fields=tuple(rule['fields']), policyVersion=POLICY_VERSION, policyDigest=rule['policyDigest'],
                      authorityWorkId=rule['authorityWorkId'], approvalSeq=rule['approvalSeq'],
                      publicCommit=publication['publicCommit'], listDigest=list_digest,
                      selectionContext=dispositions['selectionContext'], artifact=artifact,
                      ruleDigest=policy['ruleDigest'], publicationDigest=policy['publicationDigest'],
                      dispositionsDigest=policy['dispositionsDigest'],
                      authorityWorkDigest=authority['authorityWorkDigest']),
        entries=entries, artifact=artifact,
        authority=dict(authority, observedStart=started, observedEnd=datetime.now(timezone.utc).isoformat()))


def _join(proof, entry, finding, bindings, locale_digest):
    try:
        found = proof['occurrences']
        sound = (proof['proofVersion'] == 'literal-proof/v1' and proof['complete'] is True
                 and proof['reason'] is None and proof['publicCommit'] == bindings['publicCommit']
                 and proof['listDigest'] == bindings['listDigest'] and proof['inputDigest'] == finding['inputDigest']
                 and proof['localeDigest'] == locale_digest and bool(found)
                 and sorted({o['patternLine'] for o in found}) == finding['lines']
                 and all(o['publicBlobOid'] and o['publicContentDigest'] for o in found))
        got = sorted(tuple(o[k] for k in OCCURRENCE) for o in found)
    except (KeyError, TypeError):
        return 'proof'
    if not sound:
        return 'proof'
    if got != [row[:-1] for row in entry]:
        return 'mismatch'
    return None if all(row[-1] == SCOPE for row in entry) else 'excluded'


@_boundary
def resolve(observed, findings, prove, *, locale_digest, selection_context):
    """Join trusted scan findings with attested occurrences; return one private verdict per finding.

    findings are the scan's {field, lines, inputDigest, context} in scan order: lines is a list and
    context the facade's fingerprint of the finding's place in the selection. selection_context is
    its fingerprint of the exact selection. prove(inputDigest) returns the shared-list literal proof
    for that exact input and is requested only for a finding whose field is in scope, whose review
    is for this selection and that has an entry. A finding resolves only then, when its proof is
    complete, equals the attested occurrences exactly (every repeat, nothing extra) and each carries
    the rule's scope. A witness alone never resolves anything; every other finding stays unresolved.
    """
    bindings, entries, verdicts = observed['bindings'], observed['entries'], []
    _shape(locale_digest, _DIGEST)
    _shape(selection_context, _DIGEST)
    for finding in findings:
        _closed(finding, 'field lines inputDigest context')
        _shape(finding['inputDigest'], _DIGEST)
        _shape(finding['context'], _DIGEST)
        _require(finding['field'] in FIELDS)
        entry = entries.get((finding['field'], finding['context'], finding['inputDigest']))
        if finding['field'] not in bindings['fields']:
            reason = 'out-of-scope'
        elif selection_context != bindings['selectionContext']:
            reason = 'selection'
        elif entry is None:
            reason = 'unattested'
        else:
            reason = _join(prove(finding['inputDigest']), entry, finding, bindings, locale_digest)
        verdicts.append(dict(field=finding['field'], context=finding['context'], inputDigest=finding['inputDigest'],
                             resolved=reason is None, reason=reason, occurrences=len(entry) if reason is None else 0))
    return verdicts
