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

from . import cli, evidence, github_app, identifier_list, privacy
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


def _screen(repo, local, branch, selected):
    """Bounded exact-set adapter using PV-1 readers and grep semantics."""
    patterns = identifier_list.load_identifiers(local)
    matched = bool(identifier_list.match_identifiers(patterns, branch))
    workflow = False
    for sha in selected:
        raw = privacy._object(repo, 'commit', sha)
        _, sep, message = raw.partition(b'\n\n')
        if not sep:
            _fail('invalid commit object')
        matched |= bool(identifier_list.match_identifiers(patterns, privacy._text(message)))
        tree = privacy._git(repo, 'ls-tree', '-r', '-t', '-z', '--full-tree', sha, '--')
        if tree and not tree.endswith(b'\0'):
            _fail('invalid publication tree')
        for entry in tree[:-1].split(b'\0') if tree else []:
            metadata, tab, path = entry.partition(b'\t')
            if not tab or not path:
                _fail('invalid publication entry')
            filename = privacy._text(path)
            if filename.endswith('.lock'):
                continue
            if not re.fullmatch(rb'(?:(?:100644|100755|120000) blob|040000 tree) [0-9a-f]{40}', metadata):
                _fail('unsupported publication entry')
            matched |= bool(identifier_list.match_identifiers(patterns, filename))
            if not metadata.startswith(b'040000 tree '):
                contents = privacy._object(repo, 'blob', metadata[-40:].decode())
                matched |= bool(identifier_list.match_identifiers(patterns, privacy._text(contents)))
        changed = _git(repo, 'diff-tree', '--root', '-m', '--no-commit-id',
                       '--name-only', '-r', '-z', sha, '--', '.github/workflows/')
        workflow |= bool(changed)
    if matched:
        return _out('refused', 'privacy blocked; Corrections for unpublished commits; coordinator fixes branch; report published matches to Shawn')
    if workflow:
        return _out('refused', 'Shawn must push workflow changes', 5)
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
    refs = _remote(repo, url)
    selected = _publication_set(repo, head, refs)
    stopped = _screen(repo, local, branch, selected)
    if stopped:
        return stopped
    ref = 'refs/heads/' + branch
    target = url + '#' + ref
    current = refs.get(ref, 'absent')
    loaded = records.load_work(local, identity, work)
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
    try:
        _overrides(repo)
    except cli.CommandError:
        return _finish(local, identity, work, intent, 'refused',
                       confirmation='Publication configuration changed; no child launched; coordinator must repair overrides')
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
    screened = privacy.check(SimpleNamespace(local_dir=local, repo_path=str(repo),
        range=base + '..' + head, branch=args.branch, text_file=[]))
    patterns = identifier_list.load_identifiers(local)
    text_match = any(identifier_list.match_identifiers(patterns, text) for text in (title, body))
    if isinstance(screened, cli.Outcome) or screened.get('ok') is not True or text_match:
        return _out('refused', 'privacy blocked; coordinator fixes text; Corrections only for unpublished commits; report published matches to Shawn')
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
        try:
            _overrides(repo)
        except cli.CommandError:
            return finish('refused', gap='Publication configuration changed; no child launched; coordinator must repair overrides')
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
