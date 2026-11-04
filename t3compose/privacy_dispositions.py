"""Public-text standing authority, prior-publication evidence and occurrence joins.

Policy logic over explicit facts that the trusted privacy facade supplies: this
module reads no policy file, starts no process, scans nothing and never imports
the facade; its only I/O is observing the records stream. A complete literal
proof is necessary and never sufficient. Scope comes only from a recorded owner
decision plus independently attested, exact occurrence evidence. Provenance is
cooperative, not authentication.
"""
from datetime import datetime, timezone
import hashlib
import re

from . import jsonio, records

POLICY_VERSION = 'public-text/v1'
SCOPE = 'public-product-text'
FIELDS = ('branch', 'file', 'label', 'message', 'path', 'text')
CLASSES = (SCOPE, 'ambiguous', 'credential', 'private-path', 'unrelated')
DESCRIPTOR = dict(authority='owner', contextVersion=1, policyVersion=POLICY_VERSION, schemaVersion=1,
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


def digest(value):
    return 'sha256:' + hashlib.sha256(jsonio.canonical_json(value)).hexdigest()


def _closed(value, keys):
    _require(type(value) is dict and set(value) == set(keys.split()))
    if 'schemaVersion' in value:
        _require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1)


def _shape(value, pattern):
    _require(isinstance(value, str) and re.fullmatch(pattern, value) is not None)


def _text(value):
    _require(isinstance(value, str) and bool(value.strip()) and '\0' not in value)
    value.encode('utf-8')


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
    _text(source['sourceRef'])
    _text(source['quote'])  # The date must equal the approval event's, which records validated.


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
        _closed(item, 'field inputDigest occurrences')
        _shape(item['inputDigest'], _DIGEST)
        _require(item['field'] in FIELDS)
        key, occurrences = (item['field'], item['inputDigest']), item['occurrences']
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
        if event['kind'] != 'decision' or not data['question'].startswith('privacy-public-text'):
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


def observe(policy, read, *, local_dir, repository, list_digest):
    """Validate the evidence and join current authority; return bindings, entries and authority facts.

    read(name, digest) returns the parsed document pinned by that raw digest. Any invalid,
    mismatched, unattested or revoked evidence raises Refusal. This is an observation of
    cooperative records at one moment: no authentication and no atomic revocation.
    """
    started = datetime.now(timezone.utc).isoformat()
    kinds = ('rule', 'publication', 'dispositions')
    _closed(policy, 'schemaVersion policyVersion authorityWorkId artifactPolicyFile artifactPolicyDigest '
                    + ' '.join(kind + 'File ' + kind + 'Digest' for kind in kinds))
    _require(policy['policyVersion'] == POLICY_VERSION)
    for kind in kinds:
        _shape(policy[kind + 'Digest'], _DIGEST)
    artifact = (policy['artifactPolicyFile'], policy['artifactPolicyDigest'])
    if artifact != (None, None):
        _text(artifact[0])
        _shape(artifact[1], _DIGEST)
    rule, publication, dispositions = (read(policy[kind + 'File'], policy[kind + 'Digest']) for kind in kinds)
    _rule(rule, repository)
    _require(rule['authorityWorkId'] == policy['authorityWorkId'])
    context = digest({k: v for k, v in rule.items() if k != 'approvalSeq'})
    _attested(publication, 'prior-publication', 'repository publicCommit', rule, context)
    _attested(dispositions, 'occurrence-review', 'publicCommit listDigest entries', rule, context)
    _shape(publication['publicCommit'], _SHA)
    _require(publication['repository'] == repository and dispositions['publicCommit'] == publication['publicCommit']
             and dispositions['listDigest'] == list_digest)
    entries = _entries(dispositions['entries'])
    authority = _authority(local_dir, rule, context)
    return dict(
        bindings=dict(ruleId=rule['ruleId'], ruleContext=context, repository=repository, scope=rule['scope'],
                      fields=tuple(rule['fields']), policyVersion=POLICY_VERSION, policyDigest=rule['policyDigest'],
                      authorityWorkId=rule['authorityWorkId'], approvalSeq=rule['approvalSeq'],
                      publicCommit=publication['publicCommit'], listDigest=list_digest,
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


def resolve(observed, findings, prove, *, locale_digest):
    """Join trusted scan findings with attested occurrences; return one private verdict per finding.

    findings are the scan's {field, lines, inputDigest} in scan order. prove(inputDigest) returns
    the shared-list literal proof for that exact input and is requested only for in-scope,
    attested candidates. A finding resolves only when its proof is complete, equals the attested
    occurrences exactly (every repeat, nothing extra) and each carries the rule's scope. A witness
    alone never resolves anything; every other finding stays unresolved.
    """
    bindings, entries, verdicts = observed['bindings'], observed['entries'], []
    for finding in findings:
        entry = entries.get((finding['field'], finding['inputDigest']))
        if finding['field'] not in bindings['fields']:
            reason = 'out-of-scope'
        elif entry is None:
            reason = 'unattested'
        else:
            reason = _join(prove(finding['inputDigest']), entry, finding, bindings, locale_digest)
        verdicts.append(dict(inputDigest=finding['inputDigest'], resolved=reason is None, reason=reason,
                             occurrences=len(entry) if reason is None else 0))
    return verdicts
