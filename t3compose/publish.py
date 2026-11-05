"""Reviewed-head publication; durable facts, never child-output inference."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import os
import json
from pathlib import Path
import re
import tempfile
import uuid
from types import SimpleNamespace
from urllib.parse import urlencode

from . import cli, evidence, github_app, privacy
from . import process, pullrequest, records, schema, token_preconditions, token_run

_SHA = re.compile(r'[0-9a-f]{40}')
_LIMIT = privacy.MAX_BYTES
# Controlled values: the private HOME config sets them and no other source may differ.
_SAFE = (('http.followRedirects', 'false'), ('push.followTags', 'false'),
         ('push.recurseSubmodules', 'no'), ('core.hooksPath', os.devnull))
_WATCHED = r'^(http(\..*)?\.followredirects|push\..*|core\.hookspath|protocol\..*)$'


def _fail(reason, code=2):
    raise cli.CommandError('publication: ' + reason, code) from None


def _out(outcome, reason, code=1):
    return cli.Outcome({'outcome': outcome, 'reason': reason}, code)


def _policy_verify_command(local, repo, base, head, branch, policy, *, text_files=()):
    """Build dormant verification argv; normalize spelling without reading files."""
    def text(value):
        if (type(value) is not str or not value or
                any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise ValueError
        if len(value.encode('utf-8')) > 4096:
            raise ValueError
        return value

    def path(value):
        if not isinstance(value, (str, Path)):
            raise ValueError
        return text(os.path.abspath(text(str(value))))

    try:
        if any(type(v) is not str or not _SHA.fullmatch(v) for v in (base, head)):
            raise ValueError
        text(branch)
        if (branch.startswith(('-', '/')) or branch.endswith(('/', '.')) or
                '@{' in branch or '..' in branch or branch in {'@', 'HEAD'} or
                re.search(r'[ ~^:?*\\[ ]', branch) or
                any(not part or part.startswith('.') or part.endswith('.lock')
                    for part in branch.split('/'))):
            raise ValueError
        if type(text_files) not in (tuple, list) or len(text_files) not in (0, 2):
            raise ValueError
        command = ['t3c', '--local-dir', path(local), 'privacy', 'verify',
                   '--repo-path', path(repo), '--range', base + '..' + head,
                   '--branch', branch, '--privacy-policy', path(policy)]
        for item, label in zip(text_files, ('title', 'body')):
            if type(item) not in (tuple, list) or len(item) != 2 or item[0] != label:
                raise ValueError
            command.extend(['--text-file', label + '=' + path(item[1])])
        if len(json.dumps(command, ensure_ascii=False).encode('utf-8')) > schema.MAX_PAYLOAD_BYTES:
            raise ValueError
        return command
    except (ValueError, TypeError, OSError):
        _fail('invalid policy verification command')


def _policy_verify_check(local, identity, work, head, command):
    """Join current records only; a matching check never clears another gap.

    Future callers must build expected argv from their captured invocation.
    Cooperative check provenance is not authentication or policy permission.
    """
    try:
        if (type(command) is not list or len(command) not in (13, 17) or
                any(type(token) is not str for token in command)):
            raise ValueError
        endpoints = command[8].split('..')
        if len(endpoints) != 2 or type(head) is not str or not _SHA.fullmatch(head):
            raise ValueError
        texts = ()
        if len(command) == 17:
            if not command[14].startswith('title=') or not command[16].startswith('body='):
                raise ValueError
            texts = (('title', command[14][6:]), ('body', command[16][5:]))
        canonical = _policy_verify_command(command[2], command[6], *endpoints,
                                            command[10], command[12], text_files=texts)
        if canonical != command:
            raise ValueError
        expected_local = _policy_verify_command(local, command[6], *endpoints,
                            command[10], command[12], text_files=texts)[2]
        loaded = records.load_work(local, identity, work)
        effective = loaded['effectiveWork']
        if (command[2] != expected_local or endpoints != [effective['reviewBase'], head]):
            return False
        (_, checks, _, _, _, _, _, assignments, lead, since, _) = evidence._index(loaded)
        for requirement in effective['requiredChecks']:
            if requirement['command'] != command:
                continue
            key = requirement['id']
            boundary = since['requiredChecks'][key]
            event = evidence._select(checks.get(key, []), boundary, head,
                                     assignments, lead, command)
            if event is None:
                continue
            data = event['data']
            if (event['seq'] > boundary and data['commit'] == head and
                    data['command'] == command and evidence._trusted(event, assignments, lead) and
                    data['outcome'] == 'pass' and records.concrete(data['evidencePath'])):
                return evidence._compare(loaded, head, False)['verified'] is True
        return False
    except (ValueError, TypeError, OSError, records.RecordError):
        _fail('invalid policy verification inputs or records')


def _policy_observation(result, *, operation, repository, base, head, commits,
                        branch, texts, check_command, identifiers, list_observed_at):
    """Project current private facts only; no I/O, authentication or permission.

    The caller captures context and freshly observes identifiers. Stable policy
    bindings and volatile authority observations deliberately have separate hashes.
    Never hash unselected facts, findings, paths, verdicts or proof objects.
    """
    from datetime import datetime, timezone
    from types import MappingProxyType
    from . import jsonio, privacy_dispositions as pd

    def require(condition):
        if not condition:
            raise ValueError

    def closed(value, keys):
        require(type(value) in (dict, MappingProxyType) and set(value) == set(keys.split()))
        return value

    def text(value, limit=4096):
        require(type(value) is str and bool(value) and len(value.encode('utf-8')) <= limit
                and not any(ord(c) < 32 or ord(c) == 127 for c in value))
        return value

    def digest(value):
        require(type(value) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None)
        return value

    def sha(value):
        require(type(value) is str and _SHA.fullmatch(value) is not None)
        return value

    def count(value):
        require(type(value) is int and 0 <= value <= 2**31-1)
        return value

    def sequence(value):
        require(type(value) in (tuple, list) and len(value) <= privacy.MAX_BYTES)
        return value

    def time(value):
        require(type(value) is str and len(value) <= 32 and re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)', value))
        parsed = datetime.fromisoformat(value)
        require(parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed))
        return parsed

    def identity(value):
        text(value)
        require(records.canonical_identity(value) == value)

    def segment(value):
        require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', value))

    def authority(value):
        closed(value, 'authorityEventCount authorityCheckpoint authorityWorkDigest authorityEventsDigest '
                      'observedStart observedEnd')
        n = count(value['authorityEventCount'])
        cp = closed(value['authorityCheckpoint'], 'lastSeq digest')
        require(count(cp['lastSeq']) == n)
        require(time(value['observedStart']) <= time(value['observedEnd']))
        return dict(authorityEventCount=n, authorityCheckpoint=dict(lastSeq=n,digest=digest(cp['digest'])),
            authorityWorkDigest=digest(value['authorityWorkDigest']),
            authorityEventsDigest=digest(value['authorityEventsDigest']),
            observedStart=value['observedStart'], observedEnd=value['observedEnd'])

    def authorities(value, work_digest):
        closed(value, 'start end')
        start, end = authority(value['start']), authority(value['end'])
        require(start['authorityWorkDigest'] == end['authorityWorkDigest'] == work_digest)
        return privacy._canonical_digest(start), privacy._canonical_digest(end)

    try:
        require(type(result) is privacy.PolicyEvaluation and result.policy_satisfied is True)
        require(type(operation) is str and operation in {'push', 'pr'})
        identity(repository)
        sha(base), sha(head), text(branch)
        chosen = [sha(item) for item in sequence(commits)]
        require(len(chosen)*41 <= privacy.MAX_BYTES and len(set(chosen)) == len(chosen))
        pairs = sequence(texts)
        require((operation == 'push' and not pairs) or (operation == 'pr' and len(pairs) == 2))
        projected_texts = []
        for i, pair in enumerate(pairs):
            require(len(sequence(pair)) == 2 and pair[0] == ('title','body')[i])
            label, body = pair
            text(label)
            require(type(body) is str and '\0' not in body and len(body.encode('utf-8')) <= privacy.MAX_BYTES)
            projected_texts.append(dict(labelDigest=privacy._digest(label.encode('utf-8')),
                                        textDigest=privacy._digest(body.encode('utf-8'))))
        require(type(check_command) is list and bool(check_command))
        command = [text(item) for item in check_command]
        require(len(jsonio.canonical_json(command)) <= 16384)
        ids = closed(identifiers, 'policyVersion policyDigest listDigest patternCount')
        require(ids['policyVersion'] == privacy.POLICY_VERSION and
                digest(ids['policyDigest']) == privacy._canonical_digest(privacy._POLICY_DESCRIPTOR))
        digest(ids['listDigest']), count(ids['patternCount'])
        context = dict(version='selection-context/v1', repository=repository, base=base, head=head,
            commits=chosen, branchDigest=privacy._digest(branch.encode('utf-8')), texts=projected_texts)
        context_digest, selection_digest = privacy._canonical_digest(context), privacy._canonical_digest(chosen)
        f = result.facts
        version = f['version']
        require(version in {'policy-evaluation/v1','policy-evaluation/v2'})
        v2 = version == 'policy-evaluation/v2'
        closed(f, 'version policySatisfied rawStatus raw coverage authority observation' +
                  (' selection publicText' if v2 else ''))
        observation = closed(f['observation'], 'start end')
        started, ended = time(observation['start']), time(observation['end'])
        require(started <= ended <= time(list_observed_at))
        raw = closed(f['raw'], 'ok commits findings action')
        require(type(raw['ok']) is bool and count(raw['commits']) == len(chosen))
        finding_count = len(sequence(raw['findings']))
        require(raw['action'] is None or type(raw['action']) is str)
        stable_artifact, stable_public = None, None
        artifact_start = artifact_end = public_start = public_end = None
        coverage, approved_count = f['coverage'], 0
        if coverage is not None:
            closed(coverage, 'version repository base head commits selectionDigest branchDigest texts provenance '
                             'screened approvedUnscanned excluded')
            require(coverage['version'] == 'artifact-coverage/v1')
            for key in ('repository','base','head','branchDigest'):
                require(coverage[key] == context[key])
            require(list(sequence(coverage['commits'])) == chosen and coverage['selectionDigest'] == selection_digest)
            got_texts = sequence(coverage['texts'])
            require(len(got_texts) == len(projected_texts))
            for got, expected in zip(got_texts, projected_texts):
                closed(got, 'labelDigest textDigest')
                require(dict(got) == expected)
            sequence(coverage['screened']), sequence(coverage['excluded'])
            approved_count = len(sequence(coverage['approvedUnscanned']))
            p = closed(coverage['provenance'], 'receiptId authorityWorkId approvalSeq inventoryContextDigest '
                'contextDigest policyFileDigest manifestDigest receiptDigest sourceDigest verificationDigest '
                'policyVersion policyDigest listDigest patternCount baseline authorityEventCount authorityCheckpoint '
                'authorityWorkDigest authorityEventsDigest observedStart observedEnd')
            segment(p['receiptId']), segment(p['authorityWorkId']), sha(p['baseline'])
            require(count(p['approvalSeq']) > 0)
            for key in ids:
                require(type(p[key]) is type(ids[key]) and p[key] == ids[key])
            stable_artifact = dict(repository=repository, base=base, receiptId=p['receiptId'],
                authorityWorkId=p['authorityWorkId'], approvalSeq=p['approvalSeq'], baseline=p['baseline'],
                policyVersion=p['policyVersion'], patternCount=p['patternCount'])
            for key in ('inventoryContextDigest','contextDigest','policyFileDigest','manifestDigest','receiptDigest',
                        'sourceDigest','verificationDigest','policyDigest','listDigest','authorityWorkDigest'):
                stable_artifact[key] = digest(p[key])
            a = authority({key:p[key] for key in privacy._OBSERVED})
            require(p['approvalSeq'] <= a['authorityEventCount'])
            artifact_start, artifact_end = authorities(f['authority'], p['authorityWorkDigest'])
        else:
            require(v2 and f['authority'] is None)
        if v2:
            s = closed(f['selection'], 'version repository base head commits branchDigest texts selectionContext '
                                      'listDigest patternCount localeDigest')
            require(s['version'] == context['version'])
            for key in ('repository','base','head','branchDigest'):
                require(s[key] == context[key])
            require(list(sequence(s['commits'])) == chosen)
            rows = sequence(s['texts'])
            require(len(rows) == len(projected_texts))
            for got, expected in zip(rows, projected_texts):
                closed(got, 'labelDigest textDigest')
                require(dict(got) == expected)
            require(digest(s['selectionContext']) == context_digest and
                    digest(s['listDigest']) == ids['listDigest'] and count(s['patternCount']) == ids['patternCount'])
            locale_digest = digest(s['localeDigest'])
            public = closed(f['publicText'], 'bindings authority verdicts resolved unresolved')
            require(count(public['resolved']) == len(sequence(public['verdicts'])) and
                    count(public['unresolved']) == 0 and public['resolved'] == finding_count)
            b = closed(public['bindings'], 'ruleId ruleContext repository scope fields policyVersion policyDigest '
                'authorityWorkId approvalSeq publicCommit listDigest selectionContext artifact ruleDigest '
                'publicationDigest dispositionsDigest authorityWorkDigest policyFileDigest')
            segment(b['ruleId']), segment(b['authorityWorkId']), identity(b['repository']), sha(b['publicCommit'])
            require(b['repository'] == repository and b['scope'] == pd.SCOPE and
                    b['policyVersion'] == pd.POLICY_VERSION and digest(b['policyDigest']) == pd.digest(pd.DESCRIPTOR)
                    and count(b['approvalSeq']) > 0)
            fields = list(sequence(b['fields']))
            require(bool(fields) and all(type(field) is str for field in fields) and
                    fields == sorted(set(fields)) and set(fields) <= set(pd.FIELDS))
            require(digest(b['selectionContext']) == context_digest and digest(b['listDigest']) == ids['listDigest'])
            pair = sequence(b['artifact'])
            require(len(pair) == 2)
            if coverage is None:
                require(tuple(pair) == (None,None))
                artifact_pin = [None,None]
            else:
                name = text(pair[0])
                require(not any(c in name for c in '\\*?[]') and
                        all(p not in {'','.','..'} for p in name.split('/')))
                require(digest(pair[1]) == stable_artifact['policyFileDigest'])
                artifact_pin = [privacy._digest(name.encode('utf-8')), pair[1]]
            stable_public = dict(ruleId=b['ruleId'], repository=repository, scope=b['scope'], fields=fields,
                policyVersion=b['policyVersion'], authorityWorkId=b['authorityWorkId'], approvalSeq=b['approvalSeq'],
                publicCommit=b['publicCommit'], artifact=artifact_pin, localeDigest=locale_digest)
            for key in ('ruleContext','policyDigest','listDigest','selectionContext','ruleDigest','publicationDigest',
                        'dispositionsDigest','authorityWorkDigest','policyFileDigest'):
                stable_public[key] = digest(b[key])
            public_start, public_end = authorities(public['authority'], b['authorityWorkDigest'])
            require(b['approvalSeq'] <= public['authority']['start']['authorityEventCount'])
        else:
            require(finding_count == 0)
        status = 'findings' if finding_count else ('policy-evaluation-required' if approved_count else 'no-text-findings')
        require(f['rawStatus'] == status and raw['ok'] is (not finding_count and not approved_count))
        output = dict(version='publication-policy-observation/v1', operation=operation, base=base, head=head,
            selectionDigest=selection_digest, contextDigest=context_digest,
            checkCommandDigest=privacy._canonical_digest(command), listDigest=ids['listDigest'],
            patternCount=ids['patternCount'], policyBindingDigest=privacy._canonical_digest(dict(
                version=version, contextDigest=context_digest, artifact=stable_artifact, publicText=stable_public)),
            artifactAuthorityStartDigest=artifact_start, artifactAuthorityEndDigest=artifact_end,
            publicAuthorityStartDigest=public_start, publicAuthorityEndDigest=public_end,
            rawStatus=status, rawExit=0 if raw['ok'] else 1, commitCount=len(chosen), findingCount=finding_count,
            approvedUnscannedCount=approved_count, policySatisfied=True, observedStart=observation['start'],
            observedEnd=observation['end'], listObservedAt=list_observed_at)
        require(len(jsonio.canonical_json(output)) <= 16384)
        return output
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError, cli.CommandError):
        _fail('invalid private policy observation')


def _seal_policy_context(result, *, repo, local, policy, operation, repository,
                         base, head, commits, branch, texts, check_command):
    """Dormant current-context observation, without repeating scans or proofs.

    Inputs are the trusted caller's exact evaluation context, never a saved pass.
    Existing loaders validate current cooperative provenance and nonrevocation.
    Their sequential read windows are not an atomic publication guarantee.
    """
    from datetime import datetime, timezone

    try:
        if type(result) is not privacy.PolicyEvaluation or result.policy_satisfied is not True:
            raise ValueError
        patterns = privacy.identifier_list.load_identifiers(local)
        identifiers = privacy.prepare_policy_identifiers(local)
        if privacy._policy_identifiers(patterns) != identifiers:
            raise ValueError
        facts = result.facts
        public = privacy._text_policy(repo, local, policy, patterns)
        if facts['version'] == privacy.EVALUATION_VERSION_2:
            if (public is None or public['bindings'] != facts['publicText']['bindings'] or
                    privacy._locale_digest() != facts['selection']['localeDigest']):
                raise ValueError
            artifact = public['artifactPath']
        elif facts['version'] == privacy.EVALUATION_VERSION and public is None:
            artifact = policy
        else:
            raise ValueError
        if (artifact is None) != (facts['coverage'] is None):
            raise ValueError
        if artifact is not None:
            current = privacy.load_artifact_policy(repo, local, artifact, base=base, patterns=patterns)
            provenance = facts['coverage']['provenance']
            expected = dict(provenance, repository=repository, base=base)
            if any(current[key] != expected[key] for key in privacy._BINDINGS):
                raise ValueError
            if any(type(provenance[key]) is not type(identifiers[key]) or
                   provenance[key] != identifiers[key] for key in identifiers):
                raise ValueError
        # Last list read is separate from the evaluator and the captured patterns.
        # The projection retains actual evaluation/authority times, not seal times.
        if privacy.prepare_policy_identifiers(local) != identifiers:
            raise ValueError
        return _policy_observation(result, operation=operation, repository=repository,
            base=base, head=head, commits=commits, branch=branch, texts=texts,
            check_command=check_command, identifiers=identifiers,
            list_observed_at=datetime.now(timezone.utc).isoformat())
    except (cli.CommandError, OSError, KeyError, TypeError, ValueError, UnicodeError,
            OverflowError, RecursionError):
        raise cli.CommandError('publication: invalid current policy context', 2) from None


def _encode_policy_observation(observation):
    """Independently validate the flat private output, never evaluation facts."""
    from datetime import datetime
    from . import jsonio

    def require(ok):
        if not ok:
            raise ValueError

    try:
        keys = ('version operation base head selectionDigest contextDigest checkCommandDigest listDigest '
                'patternCount policyBindingDigest artifactAuthorityStartDigest artifactAuthorityEndDigest '
                'publicAuthorityStartDigest publicAuthorityEndDigest rawStatus rawExit commitCount '
                'findingCount approvedUnscannedCount policySatisfied observedStart observedEnd listObservedAt').split()
        require(type(observation) is dict and set(observation) == set(keys))
        value = observation.copy()
        require(type(value['version']) is str and value['version'] == 'publication-policy-observation/v1')
        require(type(value['operation']) is str and value['operation'] in {'push', 'pr'})
        require(value['policySatisfied'] is True)
        for key in ('base', 'head'):
            require(type(value[key]) is str and _SHA.fullmatch(value[key]))
        for key in ('selectionDigest', 'contextDigest', 'checkCommandDigest', 'listDigest',
                    'policyBindingDigest', 'artifactAuthorityStartDigest', 'artifactAuthorityEndDigest',
                    'publicAuthorityStartDigest', 'publicAuthorityEndDigest'):
            item = value[key]
            if key.startswith(('artifactAuthority', 'publicAuthority')) and item is None:
                continue
            require(type(item) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', item))
        for prefix in ('artifact', 'public'):
            require((value[prefix+'AuthorityStartDigest'] is None) ==
                    (value[prefix+'AuthorityEndDigest'] is None))
        artifact, public = value['artifactAuthorityStartDigest'], value['publicAuthorityStartDigest']
        require(artifact is not None or public is not None)
        for key in ('patternCount', 'commitCount', 'findingCount', 'approvedUnscannedCount'):
            require(type(value[key]) is int and 0 <= value[key] <= 2**31-1)
        require(value['commitCount']*41 <= privacy.MAX_BYTES)
        findings, approved = value['findingCount'], value['approvedUnscannedCount']
        require(findings <= privacy.MAX_BYTES and approved <= privacy.MAX_BYTES)
        require((not findings or public is not None) and (not approved or artifact is not None))
        status = 'findings' if findings else ('policy-evaluation-required' if approved else 'no-text-findings')
        require(type(value['rawStatus']) is str and value['rawStatus'] == status)
        require(type(value['rawExit']) is int and value['rawExit'] == int(bool(findings or approved)))
        times = []
        for key in ('observedStart', 'observedEnd', 'listObservedAt'):
            item = value[key]
            require(type(item) is str and len(item) <= 32 and re.fullmatch(
                r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|\+00:00)', item))
            times.append(datetime.fromisoformat(item))
        require(times[0] <= times[1] <= times[2])
        encoded = jsonio.canonical_json(value)
        require(len(encoded) <= 16384)
        return encoded
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        _fail('invalid private policy observation storage')


def _write_policy_observation(local, identity, work, op_id, observation):
    """Dormant exclusive private storage; saved bytes never confer permission.

    Partial files are visible during writing. Failure cleanup only unlinks our
    inode; cleanup failure retains uncertainty. No hostile-same-user guarantee.
    """
    from contextlib import ExitStack
    import stat

    def require(ok):
        if not ok:
            raise ValueError

    def private_directory(fd):
        info = os.fstat(fd)
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
                stat.S_IMODE(info.st_mode) == 0o700)
        return info

    def same(left, right):
        return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)

    try:
        encoded = _encode_policy_observation(observation)  # Before any filesystem creation.
        require(type(identity) is str and records.canonical_identity(identity) == identity)
        for component in (work, op_id):
            require(type(component) is str and
                    re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', component))
        _, work_path = records.location(local, identity, work)
        root = work_path.parents[len(identity.split('/'))+3]
        name = op_id + '.json'
        with ExitStack() as stack:
            current = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
            stack.callback(os.close, current)
            for component in root.parts[1:]:
                require(component not in {'.', '..'})
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=current)
                stack.callback(os.close, child)
                current = child
            private_directory(current)
            pins = []
            for component in work_path.relative_to(root).parts:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                stack.callback(os.close, child)
                info = private_directory(child)
                pins.append((current, component, child, info))
                current = child
            try:
                os.mkdir('publication-policy', 0o700, dir_fd=current)
            except FileExistsError:
                pass
            os.fsync(current)  # An earlier creator may have failed its directory sync.
            parent = os.open('publication-policy', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            stack.callback(os.close, parent)
            info = private_directory(parent)
            pins.append((current, 'publication-policy', parent, info))
            fd, created, complete = None, None, False
            try:
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                # Capture identity before any later validation can fail.
                created = os.fstat(fd)
                os.fchmod(fd, 0o600)
                initial = os.fstat(fd)
                require(same(initial, created) and stat.S_ISREG(initial.st_mode) and
                        initial.st_uid == os.getuid() and initial.st_nlink == 1)
                remaining = memoryview(encoded)
                while remaining:
                    written = os.write(fd, remaining)
                    require(type(written) is int and 0 < written <= len(remaining))
                    remaining = remaining[written:]
                os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                readback = bytearray()
                while len(readback) <= len(encoded):
                    chunk = os.read(fd, len(encoded)+1-len(readback))
                    if not chunk:
                        break
                    readback.extend(chunk)
                require(readback == encoded)
                final = os.fstat(fd)
                require(same(final, created) and stat.S_ISREG(final.st_mode) and
                        final.st_uid == os.getuid() and stat.S_IMODE(final.st_mode) == 0o600 and
                        final.st_nlink == 1 and final.st_size == len(encoded) and
                        same(final, os.stat(name, dir_fd=parent, follow_symlinks=False)))
                for ancestor, component, pinned, initial_dir in pins:
                    require(same(private_directory(pinned), initial_dir) and
                            same(initial_dir, os.stat(component, dir_fd=ancestor, follow_symlinks=False)))
                closing, fd = fd, None  # A close failure must never close a recycled descriptor.
                os.close(closing)
                os.fsync(parent)
                complete = True
            finally:
                try:
                    if created is not None and not complete:
                        try:
                            if same(created, os.stat(name, dir_fd=parent, follow_symlinks=False)):
                                os.unlink(name, dir_fd=parent)
                                os.fsync(parent)
                        except OSError:
                            pass  # Retained partial/complete bytes remain exclusive, never reused.
                finally:
                    if fd is not None:
                        os.close(fd)
        return work_path / 'publication-policy' / name
    except (OSError, ValueError, TypeError, RecursionError, cli.CommandError):
        _fail('invalid private policy observation storage')


@contextmanager
def _environment(repo):
    """No ambient credentials, Git selectors, executable paths or user config."""
    before, cwd = dict(os.environ), Path.cwd()
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='t3c-publish-') as home:
        env = {'PATH': '/usr/bin:/bin:/opt/homebrew/bin', 'HOME': home,
               'XDG_CONFIG_HOME': home, 'GIT_CONFIG_NOSYSTEM': '1',
               'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_REPLACE_OBJECTS': '1',
               'GIT_NO_LAZY_FETCH': '1', 'GIT_GRAFT_FILE': os.devnull,
               'GIT_OPTIONAL_LOCKS': '0', 'GIT_ALLOW_PROTOCOL': 'https',
               'LC_ALL': 'C'}
        Path(home, '.gitconfig').write_text(''.join(
            '[{}]\n {} = {}\n'.format(*key.split('.'), value) for key, value in _SAFE))
        try:
            os.environ.clear()
            os.environ.update(env)
            os.chdir(repo)
            yield
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(before)


def _git(repo, *args, input=None, codes=(0,), as_child=False, with_status=False):
    """Read-only Git. as_child shows the configuration a push child will see."""
    env = dict(os.environ)
    if as_child:
        env = token_run.sanitized_environment(env)
    else:
        # Read-only network inspection cannot use stored credentials or redirects.
        settings = [('credential.helper', ''), ('protocol.ssh.allow', 'never'), *_SAFE]
        env['GIT_CONFIG_COUNT'] = str(len(settings))
        for i, (key, value) in enumerate(settings):
            env[f'GIT_CONFIG_KEY_{i}'], env[f'GIT_CONFIG_VALUE_{i}'] = key, value
    try:
        result = process.run(['git', *args], cwd=repo, env=env, input=input,
                             capture_output=True, timeout=30, check=False)
    except process.ProcessError:
        _fail('Git inspection failed; report to coordinator')
    if (type(result.returncode) is not int or result.returncode not in codes or
            not isinstance(result.stdout, bytes) or len(result.stdout) > _LIMIT):
        _fail('Git inspection incomplete; report to coordinator')
    return (result.returncode, result.stdout) if with_status else result.stdout


def _overrides(repo):
    """Refuse a watched setting, from any source or include, that is not ours."""
    raw = _git(repo, 'config', '--includes', '--get-regexp', _WATCHED, codes=(0, 1), as_child=True)
    allowed = {(key.lower() + ' ' + value).encode() for key, value in _SAFE}
    if any(line not in allowed for line in raw.splitlines()):
        _fail('remove unsafe publication overrides before publication')


def _remote(repo, url):
    raw = _git(repo, 'ls-remote', '--', url)
    if raw and not raw.endswith(b'\n'):
        _fail('incomplete advertised refs')
    refs = {}
    for line in raw.splitlines():
        parts = line.split(b'\t')
        if len(parts) != 2 or not re.fullmatch(rb'[0-9a-f]{40}', parts[0]):
            _fail('malformed advertised refs; report to coordinator')
        try:
            name = parts[1].decode('ascii')
        except UnicodeError:
            _fail('malformed advertised ref')
        if (name != 'HEAD' and not name.startswith('refs/') or name in refs or
                any(c.isspace() for c in name)):
            _fail('unsafe advertised ref')
        # Peeled tag rows are facts too. Validate the underlying ref spelling.
        if name.endswith('^{}') and not name.startswith('refs/tags/'):
            _fail('unsafe peeled advertised ref')
        check = name.removesuffix('^{}')
        if check != 'HEAD':
            _git(repo, 'check-ref-format', check)
        refs[name] = parts[0].decode('ascii')
    return refs


def _publication_set(repo, head, refs):
    if _git(repo, 'rev-parse', '--is-shallow-repository') != b'false\n':
        _fail('complete history required; report to coordinator')
    # Require every advertised object locally; never fetch or guess exclusions.
    for sha in set(refs.values()) | {head}:
        try:
            _git(repo, 'cat-file', '-e', sha)
        except cli.CommandError:
            _fail('advertised object unavailable; coordinator must prepare all refs and tag objects locally, then retry')
    raw = _git(repo, 'rev-list', '--stdin', '--',
               input=('\n'.join([head, *('^' + s for s in sorted(set(refs.values())))]) + '\n').encode())
    selected = raw.decode('ascii').splitlines()
    if any(not _SHA.fullmatch(s) for s in selected) or len(set(selected)) != len(selected):
        _fail('invalid publication set')
    return selected


def _screen(repo, local, branch, selected, *, base, head, texts=()):
    """One strict scanner; workflow transitions are an independent restriction."""
    screened = privacy.scan_commits(repo, local, selected, base=base, head=head,
                                   branch=branch, texts=texts)
    if screened.get('ok') is not True:
        return _out('refused', 'privacy blocked; Corrections for unpublished commits; coordinator fixes branch and text; report published matches to Shawn')
    for sha in selected:
        # Do not apply the scanner's .lock exclusions here. -m walks every
        # merge parent; --root treats the empty tree as a root's predecessor.
        if _git(repo, 'diff-tree', '--root', '-m', '--no-commit-id',
                '--name-only', '-r', '-z', sha, '--', '.github/workflows/'):
            return _out('refused', 'Shawn must push workflow changes', 5)
    return None


def _review_set(repo, base, head):
    raw = _git(repo, 'rev-list', base + '..' + head, '--')
    if raw and not raw.endswith(b'\n'):
        _fail('incomplete review selection')
    selected = raw.decode('ascii').splitlines()
    if any(not _SHA.fullmatch(s) for s in selected) or len(set(selected)) != len(selected):
        _fail('invalid review selection')
    return selected


def _final_gate(args, repo, identity, loaded, intent, selected, branch, *,
                url=None, refs=None, texts=(), metadata=None):
    """After preparation, validate captured inputs before entering token_run.

    Own intent is the only permitted record addition. No child/status facts
    are invented on refusal. These sequential reads are not a transaction.
    """
    local, work, head = args.local_dir, args.work, intent['data']['commit']
    base = loaded['work']['reviewBase']
    try:
        _overrides(repo)
    except cli.CommandError:
        return _out('refused', 'Publication configuration changed; no child launched; coordinator must repair overrides')
    stopped = _screen(repo, local, branch, selected, base=base, head=head, texts=texts)
    if stopped:
        return stopped
    if not evidence.check(local, identity, work, head, pr_ready=False)['verified']:
        return _out('refused', 'Publication evidence changed; no child launched; record fresh exact-head checks and review')
    current = records.load_work(local, identity, work)
    if (current['work'] != loaded['work'] or
            current['effectiveWork'] != loaded['effectiveWork'] or
            current['events'] != [*loaded['events'], intent]):
        return _out('refused', 'Publication work changed; no child launched; reconcile current work and review')
    if url is not None:
        actual_refs = _remote(repo, url)
        if actual_refs != refs or _publication_set(repo, head, actual_refs) != selected:
            return _out('refused', 'Publication refs or selection changed; no child launched; prepare fresh publication')
    elif _review_set(repo, base, head) != selected:
        return _out('refused', 'Publication selection changed; no child launched')
    if (args.head != head or args.branch != branch or
            metadata is not None and (args.base, privacy._file(args.title_file),
                                      privacy._file(args.body_file)) != metadata):
        return _out('refused', 'Publication metadata changed; no child launched; prepare fresh publication')
    if _git(repo, 'rev-parse', '--verify', 'HEAD') != (head + '\n').encode():
        return _out('refused', 'Publication HEAD changed; no child launched; restore reviewed checkout')
    return None


def _append(local, repo, work, kind, data):
    loaded = records.load_work(local, repo, work)
    return records.append_event(local, repo, work, kind, data,
                                writer=loaded['effectiveWork']['writer'])


def _latest(loaded, target):
    intents = [e for e in loaded['events'] if e['kind'] == 'op-intent' and
               e['data']['operation'] == 'push' and e['data']['target'] == target]
    intent = intents[-1] if intents else None
    results = [e for e in loaded['events'] if e['kind'] == 'op-result' and
               intent and e['data']['intentRef'] == intent['seq']]
    return intent, results[-1] if results else None


def _status_path(local, repo, work, intent):
    _, root = records.location(local, repo, work)
    path = root / 'publication-status'
    with records.directory(path, create=True):
        pass
    return path / (intent['data']['opId'] + '.json')


def _read_status(path):
    if not os.path.lexists(path):
        return {'childStarted': False, 'refusal': 'missing-status'}
    try:
        with records.directory(path.parent) as fd:
            value = records.read_file(fd, path.name)
        schema._check_status_summary(value, 'status')
        if value.get('refusal') not in {None, 'missing-status', *token_run._CODES, *token_preconditions.KINDS}:
            return None
        return value
    except (OSError, ValueError):
        return None


def _finish(local, repo, work, intent, outcome, summary=None, confirmation=None):
    data = {key: intent['data'][key] for key in ('opId', 'operation', 'target', 'commit')}
    data.update(intentRef=intent['seq'], outcome=outcome,
                confirmation='Exact remote head confirmed' if outcome in {'succeeded', 'no-op'}
                else 'Publication not confirmed; reconcile remote and retained status before retry')
    if confirmation is not None:
        data['confirmation'] = confirmation
    if summary is not None:
        data['statusSummary'] = summary
    _append(local, repo, work, 'op-result', data)
    reason = data['confirmation']
    kind = (summary or {}).get('refusal')
    if outcome == 'refused' and kind in {'missing-status', *token_run._CODES, *token_preconditions.KINDS}:
        reason += '; refusal ' + kind + '; coordinator must repair the named condition and use fresh exact-head evidence before retry'
    if (summary or {}).get('revocation') == 'failed':  # CD-2's warning, which _quiet discards
        reason += '; token revocation failed, so the token expires on its own'
    return _out(outcome, reason, 0 if outcome in {'succeeded', 'no-op'} else 1)


@contextmanager
def _quiet():
    """Contain inherited child descriptors as well as Python diagnostics."""
    saved = [os.dup(1), os.dup(2)]
    try:
        with open(os.devnull, 'w') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
    finally:
        for original, copy in zip((1, 2), saved):
            os.dup2(copy, original)
            os.close(copy)


def _valid_summary(result, status):
    if not isinstance(result, token_run.RunResult):
        return None
    value = result.record()
    try:
        schema._check_status_summary(value, 'status')
    except ValueError:
        return None
    # Persisted status and actual return must agree; code alone proves nothing.
    return value if value == status else None


def _push(args, repo, identity, url):
    local, work, head, branch = args.local_dir, args.work, args.head, args.branch
    checked = evidence.check(local, identity, work, head, pr_ready=False)
    if not checked['verified']:
        return _out('refused', 'stale or missing evidence; coordinator must record exact-head checks and review')
    loaded = records.load_work(local, identity, work)
    refs = _remote(repo, url)
    selected = _publication_set(repo, head, refs)
    stopped = _screen(repo, local, branch, selected,
                      base=loaded['work']['reviewBase'], head=head)
    if stopped:
        return stopped
    ref = 'refs/heads/' + branch
    target = url + '#' + ref
    current = refs.get(ref, 'absent')
    old, result = _latest(loaded, target)
    uncertain = records.uncertain_items(loaded['events'])
    pending = [i for i in uncertain if i['kind'] == 'operation' and
               i['intent']['data']['operation'] == 'push']
    if pending:
        # Never let another branch, an older intent or late result resolve it.
        if len(pending) != 1 or not old or pending[0]['ref'] != old['seq'] or old['data']['commit'] != head:
            return _out('unknown', 'unresolved push attempt; coordinator must reconcile records')
        status = _read_status(_status_path(local, identity, work, old))
        if status and status.get('childStarted') is True and type(status.get('childStatus')) is int:
            outcome = 'succeeded' if status['childStatus'] == 0 and current == head else 'failed'
            return _finish(local, identity, work, old, outcome, status)
        if status and status['childStarted'] is False:
            return _finish(local, identity, work, old, 'refused', status)
        return _out('unknown', 'child status unresolved; reconcile retained status and remote; no second push')
    prior = None
    # A later refused attempt cannot erase an earlier confirmed prior head.
    # Order by intent, not arrival of a late result for an older attempt.
    joined = {e['data']['intentRef']: e for e in loaded['events'] if e['kind'] == 'op-result'}
    for event in reversed(loaded['events']):
        if (event['kind'] == 'op-intent' and event['data']['operation'] == 'push'
                and event['data']['target'] == target):
            confirmed = joined.get(event['seq'])
            if confirmed and evidence._result_gap(confirmed, event['data']['commit']) is None:
                prior = event['data']['commit']
                break
    if current not in {'absent', head, prior}:
        return _out('refused', 'unexpected remote movement; report to Shawn')
    if current not in {'absent', head}:
        code, _ = _git(repo, 'merge-base', '--is-ancestor', current, head,
                       codes=(0, 1), with_status=True)
        if code == 1:
            return _out('refused', 'non-fast-forward update; coordinator must preserve the confirmed prior head in reviewed history')
    intent = _append(local, identity, work, 'op-intent', {
        'opId': uuid.uuid4().hex, 'operation': 'push', 'target': target,
        'commit': head, 'expectedPriorHead': current})
    if _remote(repo, url).get(ref, 'absent') != current:
        return _finish(local, identity, work, intent, 'refused')
    if current == head:
        return _finish(local, identity, work, intent, 'no-op')
    path = _status_path(local, identity, work, intent)
    command = ['git', 'push', '--force-with-lease=' + ref + ':' +
               ('' if current == 'absent' else current), url, head + ':' + ref]
    stopped = _final_gate(args, repo, identity, loaded, intent, selected, branch,
                          url=url, refs=refs)
    if stopped:
        refused = _finish(local, identity, work, intent, 'refused',
                          confirmation=stopped.data['reason'])
        return cli.Outcome(refused.data, stopped.code)
    try:
        with _quiet():
            result = token_run.run_command(command, path, local_dir=local)
    except (Exception, KeyboardInterrupt):
        return _finish(local, identity, work, intent, 'unknown')
    status = _read_status(path)
    summary = _valid_summary(result, status)
    if summary is None:
        return _finish(local, identity, work, intent, 'unknown')
    if summary['childStarted'] is False:
        return _finish(local, identity, work, intent, 'refused', summary)
    if summary.get('childStatus') is None:
        return _finish(local, identity, work, intent, 'unknown', summary)
    try:
        confirmed = _remote(repo, url).get(ref) == head
    except cli.CommandError:
        return _finish(local, identity, work, intent, 'unknown', summary)
    outcome = 'succeeded' if summary['childStatus'] == 0 and confirmed else 'failed'
    return _finish(local, identity, work, intent, outcome, summary)


def _publish(args, *, pr_mode=False):
    """Public handler. No I/O at import or registration time."""
    try:
        info = cli.helper_info()
        dirty = info['dirty']
        if dirty:
            helper = Path(info['root'])
            with _environment(helper):
                dirty = bool(_git(helper, 'status', '--porcelain', '--untracked-files=no'))
        if dirty or not _SHA.fullmatch(info['commit']):
            return _out('refused', 'helper must be clean; coordinator must commit helper changes')
        if (not _SHA.fullmatch(args.head) or not records.SEGMENT.fullmatch(args.work)
                or not isinstance(args.branch, str) or not args.branch or
                args.branch.startswith('-') or '\0' in args.branch):
            _fail('invalid head, work or branch')
        repo = Path.cwd() if pr_mode else Path(args.repo_path).resolve(strict=True)
        reader_env = dict(os.environ)
        with _environment(repo):
            config = github_app.load_config(Path(args.local_dir) / 'github-app.json')
            identity = 'github.com/' + config.full_name
            url = 'https://' + identity + '.git'
            _git(repo, 'check-ref-format', 'refs/heads/' + args.branch)
            if _git(repo, 'rev-parse', '--verify', 'HEAD') != (args.head + '\n').encode():
                _fail('checkout HEAD differs from reviewed head')
            if _git(repo, 'rev-parse', '--show-toplevel').strip() != os.fsencode(repo):
                _fail('repository root required')
            origins = _git(repo, 'config', '--get-all', 'remote.origin.url').splitlines()
            if origins not in ([url.encode()], [url.removesuffix('.git').encode()]):
                _fail('configured HTTPS repository identity required')
            command = ['git', 'push', url, args.head + ':refs/heads/' + args.branch]
            token_preconditions.check(command, env=token_run.sanitized_environment(os.environ),
                                      cwd=str(repo))
            _overrides(repo)
            if pr_mode:
                return _pr(args, repo, identity, config, reader_env)
            return _push(args, repo, identity, url)
    except github_app.ConfigError:
        _fail('invalid App configuration; coordinator must repair private configuration', 3)
    except token_preconditions.Refusal:
        return _out('refused', 'unsafe Git configuration; coordinator must repair configuration')
    except (records.RecordError, schema.SchemaError, OSError, ValueError, TypeError):
        _fail('invalid inputs or record persistence failed; preserve status and reconcile before retry')



def push(args):
    return _publish(args)


def pr(args):
    return _publish(args, pr_mode=True)


@contextmanager
def _reader_environment(env):
    """Only documented read-only GETs may use the caller's host authentication."""
    private = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        # Pin executables while retaining gh's existing authentication locations.
        os.environ['PATH'] = private['PATH']
        yield
    finally:
        os.environ.clear()
        os.environ.update(private)


def _discover(config, branch, env):
    query = urlencode({'state': 'all', 'head': config.owner + ':' + branch, 'per_page': 2})
    try:
        with _reader_environment(env):
            result = process.run(['gh', 'api', '--hostname', 'github.com', '--method', 'GET',
                                  f'repos/{config.full_name}/pulls?' + query],
                                 capture_output=True, check=False, timeout=30)
        raw = result.stdout
        if type(result.returncode) is not int or result.returncode != 0:
            _fail('PR discovery failed; reconcile before retry')
        if not isinstance(raw, bytes) or len(raw) > pullrequest.MAX_RESPONSE_BYTES:
            _fail('PR discovery incomplete; reconcile before retry')
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=pullrequest._pairs,
                          parse_constant=pullrequest._constant)
        # Two rows also covers a full page: never guess through pagination.
        if not isinstance(data, list) or len(data) > 1:
            _fail('PR discovery ambiguous; reconcile before retry')
        if not data:
            return None
        row = data[0]
        pullrequest._shape(row)
        number = row['number']
        pullrequest._request(config.full_name, number)
        if (row['head']['ref'] != branch or
                row['head']['repo']['full_name'] != config.full_name or
                row['base']['repo']['full_name'] != config.full_name):
            _fail('PR discovery target mismatch; report to Shawn')
        return number
    except Exception:
        _fail('PR discovery unavailable or invalid; reconcile before retry')


def _verified_pr(args, config, number, env):
    try:
        with _reader_environment(env):
            result = pullrequest.verify(SimpleNamespace(local_dir=args.local_dir,
                repo=config.full_name, number=number, head=args.head,
                base=args.base, author=config.bot_login))
            data = pullrequest.read_pull_request(config.full_name, number)
            facts = pullrequest._shape(data)
        return (type(result) is dict and result.get('ok') is True and
                result == {'ok': True, 'mismatches': [], 'action': None} and
                data.get('draft') is False and
                facts == (args.head.lower(), args.base, 'open', config.bot_login))
    except Exception:
        return False


def _pr(args, repo, identity, config, reader_env):
    local, work, head = args.local_dir, args.work, args.head
    if args.base != 'main':
        _fail('PR base must be main')
    title, body = privacy._file(args.title_file), privacy._file(args.body_file)
    if (not title.strip() or len(title.encode('utf-8')) > 256 or
            any(ord(c) < 32 or ord(c) == 127 for c in title)):
        _fail('PR title must be nonempty bounded single-line text')
    if not evidence.check(local, identity, work, head, pr_ready=False)['verified']:
        return _out('refused', 'stale or missing evidence; record exact-head checks and review')
    loaded = records.load_work(local, identity, work)
    base = loaded['work']['reviewBase']
    branch = args.branch
    metadata = (args.base, title, body)
    texts = (('title', title), ('body', body))
    selected = _review_set(repo, base, head)
    stopped = _screen(repo, local, branch, selected, base=base, head=head, texts=texts)
    if stopped:
        return stopped
    target = 'https://github.com/' + config.full_name + '/pulls#' + args.branch
    intents = [e for e in loaded['events'] if e['kind'] == 'op-intent' and
               e['data']['operation'] == 'pull-request' and e['data']['target'] == target]
    old = intents[-1] if intents else None
    pending = [i for i in records.uncertain_items(loaded['events']) if
               i['kind'] == 'operation' and i['intent']['data']['operation'] == 'pull-request']
    if pending and (len(pending) != 1 or old is None or pending[0]['ref'] != old['seq'] or
                    old['data']['commit'] != head):
        return _out('unknown', 'unresolved PR attempt; reconcile records before retry')
    intent = old if pending else _append(local, identity, work, 'op-intent', {
        'opId': uuid.uuid4().hex, 'operation': 'pull-request', 'target': target,
        'commit': head, 'expectedPriorHead': None})

    def finish(outcome, summary=None, number=None, gap=None):
        result = _finish(local, identity, work, intent, outcome, summary,
                         gap or ('Exact PR head, base, open state and App author verified'
                                 if outcome in {'succeeded', 'no-op'} else
                                 'PR not confirmed; reconcile repository PR state and retained status before retry'))
        if number is not None:
            result.data.update(number=number, url=f'https://github.com/{config.full_name}/pull/{number}')
        return result

    try:
        number = _discover(config, args.branch, reader_env)
    except cli.CommandError:
        return finish('unknown')
    if pending:
        summary = _read_status(_status_path(local, identity, work, intent))
        if summary and summary['childStarted'] is False:
            return finish('refused', summary)
        if not summary or summary.get('childStatus') is None:
            return finish('unknown', summary)
        if number is None or summary['childStatus'] != 0:
            return finish('failed', summary)
        passed = _verified_pr(args, config, number, reader_env)
        return finish('succeeded' if passed else 'failed', summary,
                      number if passed else None, None if passed else
                      'PR verification failed; report to Shawn; not PR-ready')
    if number is not None:
        if not _verified_pr(args, config, number, reader_env):
            return finish('failed', gap='PR verification failed; report to Shawn; not PR-ready')
        return finish('no-op', number=number)
    path = _status_path(local, identity, work, intent)
    # Snapshot the screened body. The title is already immutable in this argv.
    with tempfile.TemporaryDirectory(dir='/tmp', prefix='t3c-pr-text-') as scratch:
        body_path = Path(scratch) / 'body.txt'
        with body_path.open('x', encoding='utf-8', newline='') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
        command = ['gh', 'pr', 'create', '--repo', config.full_name, '--head', args.branch,
                   '--base', args.base, '--title=' + title, '--body-file', str(body_path)]
        stopped = _final_gate(args, repo, identity, loaded, intent, selected, branch,
                              texts=texts, metadata=metadata)
        if stopped:
            refused = finish('refused', gap=stopped.data['reason'])
            return cli.Outcome(refused.data, stopped.code)
        try:
            with _quiet():
                actual = token_run.run_command(command, path, local_dir=local)
        except (Exception, KeyboardInterrupt):
            return finish('unknown')
    summary = _valid_summary(actual, _read_status(path))
    if summary is None:
        return finish('unknown')
    if summary['childStarted'] is False:
        return finish('refused', summary)
    if summary.get('childStatus') is None:
        return finish('unknown', summary)
    try:
        number = _discover(config, args.branch, reader_env)
    except cli.CommandError:
        return finish('unknown', summary)
    if number is None:
        return finish('failed', summary)
    passed = _verified_pr(args, config, number, reader_env)
    if not passed:
        return finish('failed', summary, gap='PR verification failed; report to Shawn; not PR-ready')
    return finish('succeeded' if summary['childStatus'] == 0 else 'failed', summary,
                  number if summary['childStatus'] == 0 else None)


def register(registry):
    parser = registry.group('publish').add_parser('push', help='publish an evidenced exact head')
    for name in ('repo-path', 'work', 'head', 'branch'):
        parser.add_argument('--' + name, required=True)
    parser.set_defaults(handler=push)

    parser = registry.group('publish').add_parser('pr', help='create or reuse an evidenced PR')
    for name in ('work', 'head', 'branch', 'base', 'title-file', 'body-file'):
        parser.add_argument('--' + name, required=True)
    parser.set_defaults(handler=pr)
