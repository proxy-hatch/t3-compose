"""Reviewed-head publication; durable facts, never child-output inference."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import os
from pathlib import Path
import re
import tempfile
import uuid

from . import cli, evidence, github_app, identifier_list, jsonio, privacy
from . import process, records, schema, token_preconditions, token_run

_SHA = re.compile(r'[0-9a-f]{40}')
_LIMIT = privacy.MAX_BYTES


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
        Path(home, '.gitconfig').write_text(
            '[http]\n followRedirects = false\n[push]\n followTags = false\n'
            ' recurseSubmodules = no\n[core]\n hooksPath = /dev/null\n')
        try:
            os.environ.clear()
            os.environ.update(env)
            os.chdir(repo)
            yield
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(before)


def _git(repo, *args, input=None, codes=(0,)):
    env = dict(os.environ)
    # Read-only network inspection cannot use stored credentials or redirects.
    settings = [('credential.helper', ''), ('http.followRedirects', 'false'),
                ('protocol.ssh.allow', 'never'), ('core.hooksPath', os.devnull)]
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
    return result.stdout


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
        _git(repo, 'cat-file', '-e', sha)
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


def _finish(local, repo, work, intent, outcome, summary=None):
    data = {key: intent['data'][key] for key in ('opId', 'operation', 'target', 'commit')}
    data.update(intentRef=intent['seq'], outcome=outcome,
                confirmation='Exact remote head confirmed' if outcome in {'succeeded', 'no-op'}
                else 'Publication not confirmed; reconcile remote and retained status before retry')
    if summary is not None:
        data['statusSummary'] = summary
    _append(local, repo, work, 'op-result', data)
    return _out(outcome, data['confirmation'], 0 if outcome in {'succeeded', 'no-op'} else 1)


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
        if event['kind'] == 'op-intent' and event['data']['target'] == target:
            confirmed = joined.get(event['seq'])
            if confirmed and evidence._result_gap(confirmed, event['data']['commit']) is None:
                prior = event['data']['commit']
                break
    if current not in {'absent', head, prior}:
        return _out('refused', 'unexpected remote movement; report to Shawn')
    if current not in {'absent', head}:
        _git(repo, 'merge-base', '--is-ancestor', current, head)
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


def push(args):
    """Public handler. No I/O at import or registration time."""
    try:
        info = cli.helper_info()
        if info['dirty'] or not _SHA.fullmatch(info['commit']):
            return _out('refused', 'helper must be clean; coordinator must commit helper changes')
        if (not _SHA.fullmatch(args.head) or not records.SEGMENT.fullmatch(args.work)
                or not isinstance(args.branch, str) or not args.branch or
                args.branch.startswith('-') or '\0' in args.branch):
            _fail('invalid head, work or branch')
        repo = Path(args.repo_path).resolve(strict=True)
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
            token_preconditions.check(command, env=dict(os.environ), cwd=str(repo))
            # Git follows redirects by default; publication refuses that ambient setting.
            unsafe = _git(repo, 'config', '--includes', '--get-regexp',
                          r'^(http(\..*)?\.followredirects|push\..*|core\.hookspath|protocol\..*)$', codes=(0, 1))
            allowed = {b'http.followredirects false', b'push.followtags false',
                       b'push.recursesubmodules no', b'core.hookspath /dev/null'}
            normalized = [key.lower() + b' ' + value for key, _, value in
                          (line.partition(b' ') for line in unsafe.splitlines())]
            if any(line not in allowed for line in normalized):
                _fail('remove unsafe publication overrides before publication')
            return _push(args, repo, identity, url)
    except github_app.ConfigError:
        _fail('invalid App configuration; coordinator must repair private configuration', 3)
    except token_preconditions.Refusal:
        return _out('refused', 'unsafe Git configuration; coordinator must repair configuration')
    except (records.RecordError, schema.SchemaError, OSError, ValueError, TypeError):
        _fail('invalid inputs or record persistence failed; preserve status and reconcile before retry')


def register(registry):
    parser = registry.group('publish').add_parser('push', help='publish an evidenced exact head')
    for name in ('repo-path', 'work', 'head', 'branch'):
        parser.add_argument('--' + name, required=True)
    parser.set_defaults(handler=push)
