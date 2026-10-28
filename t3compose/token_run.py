"""Run one command under an App token lease; see docs/cli/token_run.md."""
import argparse
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import github_app as app, jsonio, process, token_preconditions
from .cli import CommandError, PassThrough

_REMOVED = frozenset('GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN '
                     'GH_HOST GH_DEBUG GIT_CONFIG GIT_ALLOW_PROTOCOL GIT_SSL_NO_VERIFY '
                     'GIT_CURL_VERBOSE GIT_REDACT_COOKIES'.split())
_HELPER = ('!f() { test "$1" = get || return 0; echo username=x-access-token; '
           'printf \'password=%s\\n\' "$GH_TOKEN"; }; f')
_OVERRIDES = (('credential.helper', ''),
              ('credential.https://github.com.helper', _HELPER),
              ('protocol.ssh.allow', 'never'))
_CODES = {'invalid-input': 2, 'invalid-config': 3, 'setup-failed': 2,
          'mint-failed': 1, 'status-failed': 2, 'start-failed': 2, 'interrupted': 1}
_CAUGHT = (Exception, KeyboardInterrupt, SystemExit)


def sanitized_environment(base):
    """Token-free base for GA-3 inspection, before controlled config is added."""
    return {key: value for key, value in base.items()
            if key not in _REMOVED and not key.startswith(('GIT_CONFIG_', 'GIT_TRACE'))}


def child_environment(base, token):
    """Add command-scope helper reset and SSH refusal; token only in GH_TOKEN."""
    env = dict(base)
    env['GIT_CONFIG_COUNT'] = str(len(_OVERRIDES))
    for index, (key, value) in enumerate(_OVERRIDES):
        env[f'GIT_CONFIG_KEY_{index}'] = key
        env[f'GIT_CONFIG_VALUE_{index}'] = value
    env['GH_TOKEN'] = token
    return env


@dataclass
class RunResult:
    """Actual in-process facts. An on-disk true/null record is only intent."""
    child_started: bool = False
    child_status: int | None = None
    refusal: str | None = None
    revocation: str | None = None
    code: int = 2

    def record(self):
        if self.child_started:
            return {'childStarted': True, 'childStatus': self.child_status,
                    'revocation': self.revocation}
        return {'childStarted': False, 'refusal': self.refusal}


def _warn(message, secrets):
    # A closed stderr, or an interruption while reporting, cannot replace a
    # known child outcome or skip cleanup. Never display backend exceptions.
    try:
        print(secrets.diagnostic('t3c publish run: ' + message), file=sys.stderr)
    except _CAUGHT:
        pass


def _unexpected(error):
    return ('command failed (' + type(error).__name__ +
            '); check local inputs and helper installation')


def _prestart_failure(error, stage):
    if stage == 'setup-failed' and isinstance(error, token_preconditions.Refusal):
        return error.kind, 1, None
    interrupted = isinstance(error, (KeyboardInterrupt, InterruptedError))
    refusal = 'interrupted' if interrupted else stage
    expected = (interrupted or
                (stage == 'invalid-config' and isinstance(error, app.ConfigError)) or
                (stage == 'mint-failed' and isinstance(error, app.CheckError)) or
                (stage == 'start-failed' and isinstance(error, process.ProcessError)) or
                (stage == 'invalid-input' and isinstance(error, (OSError, TypeError, ValueError))) or
                (stage in ('setup-failed', 'status-failed') and isinstance(error, OSError)))
    return refusal, _CODES[refusal] if expected else 2, None if expected else _unexpected(error)


class _Signals:
    """Non-raising handlers cover mint, launch, wait, revocation and recording."""
    def __init__(self):
        self.previous = {}
        self.pending = deque()

    def receive(self, signum, frame):
        self.pending.append(signum)

    def __enter__(self):
        try:
            for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                self.previous[signum] = signal.getsignal(signum)
                signal.signal(signum, self.receive)
        except _CAUGHT:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        failure = None
        for signum, handler in self.previous.items():
            try:
                signal.signal(signum, handler)
            except _CAUGHT as error:
                failure = error
                try:
                    signal.signal(signum, handler)
                except _CAUGHT:
                    pass
        if failure is not None:
            raise failure

    def wait(self, child):
        # A signal received inside process.start is queued until its real
        # handle is available. No Python signal handler can unwind Popen.
        while True:
            while self.pending:
                signum = self.pending.popleft()
                try:
                    child.send_signal(signum)
                except ProcessLookupError:
                    pass
                except _CAUGHT:
                    self.pending.appendleft(signum)
                    break  # Retry after waiting; never revoke a live child.
            try:
                return child.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
            except _CAUGHT:
                # A failed wait must neither revoke early nor block forwarding
                # a subsequent signal. poll also reaps an exited real child.
                try:
                    status = child.poll()
                    if status is not None:
                        return status
                except _CAUGHT:
                    pass
                try:
                    time.sleep(0.1)
                except _CAUGHT:
                    pass


def _persist(path, value, secrets, *, started):
    try:
        jsonio.atomic_replace(path, value)
    except _CAUGHT as error:
        # atomic_replace may have renamed before a directory fsync/cleanup
        # error. Inspect only; never replace a real started result with false.
        try:
            published = jsonio.read_json(path) == value
        except _CAUGHT:
            published = False
        detail = 'complete record visible; durability uncertain' if published else 'record not confirmed'
        _warn('status recording failed; ' + detail +
              ('; reconcile child outcome before retry' if started else '; no child started'), secrets)
        expected = isinstance(error, (OSError, KeyboardInterrupt, InterruptedError))
        return False, None if expected else _unexpected(error)
    return True, None


def run_command(command, status_file, *, local_dir):
    """Run from current cwd in the main thread; return actual facts for PB-1.

    command is a nonempty list with the CLI separator already removed. The
    caller supplies an unused status path in an existing directory and owns
    that path exclusively until return. No config or output is parsed by a
    caller to infer startup. process.start and jsonio.atomic_replace retain
    their shared contracts; this module owns the child handle and its wait.
    """
    result = RunResult()
    secrets = app.Secrets()
    lease = None
    stage = 'invalid-input'
    path = None
    diagnostics = []
    try:
        path = Path(status_file)
        if os.path.lexists(path):
            result.refusal = 'invalid-input'
            _warn('existing status path belongs to an earlier invocation; use an unused path', secrets)
            return result
        with _Signals() as signals:
            try:
                if not isinstance(command, list) or not command or not command[0]:
                    raise ValueError('invalid command')
                stage = 'setup-failed'
                cwd = os.getcwd()
                base = sanitized_environment(os.environ)
                token_preconditions.check(command, env=base, cwd=cwd)
                stage = 'invalid-config'
                config = app.load_config(Path(local_dir) / 'github-app.json')
                stage = 'setup-failed'
                rt = app.default_runtime()
                lease = app.TokenLease(app.GitHubAPI(rt.http, secrets))
                with lease:
                    if signals.pending:
                        stage = 'interrupted'
                        raise InterruptedError
                    stage = 'mint-failed'
                    app.mint_token(config, rt, secrets, lease)
                    if signals.pending:
                        stage = 'interrupted'
                        raise InterruptedError
                    env = child_environment(base, lease.token)
                    stage = 'status-failed'
                    # Only a successful return permits launch, even if a
                    # failed atomic write left a complete intent visible.
                    jsonio.atomic_replace(path, {'childStarted': True, 'childStatus': None})
                    if signals.pending:
                        stage = 'interrupted'
                        raise InterruptedError
                    stage = 'start-failed'
                    child = process.start(command, env=env, cwd=cwd)
                    result.child_started = True
                    result.child_status = PassThrough(signals.wait(child)).status
                    result.code = result.child_status
            except _CAUGHT as error:
                if not result.child_started:
                    result.refusal, result.code, diagnostic = _prestart_failure(error, stage)
                    if diagnostic:
                        diagnostics.append(diagnostic)
                elif result.child_status is not None:
                    # An unexpected lease cleanup error cannot override the
                    # known child status. TokenLease normally handles these.
                    lease.status = 'failed'
            finally:
                if lease is not None and lease.status == 'failed':
                    _warn('warning: token revocation failed; expires_at=' + lease.expires_at, secrets)
                if result.child_started:
                    result.revocation = 'ok' if lease.status == 'ok' else 'failed'
                elif lease is not None and lease.unexpected:
                    result.code = 2
                    diagnostics.append(lease.unexpected)
                _, diagnostic = _persist(path, result.record(), secrets, started=result.child_started)
                if diagnostic:
                    if not result.child_started:
                        result.code = 2
                    diagnostics.append(diagnostic)
                for diagnostic in diagnostics:
                    _warn(diagnostic, secrets)
    except _CAUGHT as error:
        # Setup/handler failures before launch still have a public refusal;
        # restoration failures after completion keep the known child result.
        if not result.child_started:
            result.refusal, result.code, diagnostic = _prestart_failure(error, stage)
            if diagnostics:
                result.code = 2  # A later interruption cannot erase an earlier helper defect.
            if path is not None:
                _, persistence_diagnostic = _persist(path, result.record(), secrets, started=False)
                if persistence_diagnostic:
                    result.code = 2
                    _warn(persistence_diagnostic, secrets)
            if diagnostic:
                _warn(diagnostic, secrets)
        else:
            _warn('signal restoration failed; child outcome retained', secrets)
    return result


def run(args):
    command = args.child_argv
    valid = len(command) >= 2 and command[0] == '--'
    result = run_command(command[1:] if valid else [], args.status_file, local_dir=args.local_dir)
    if result.child_started:
        return PassThrough(result.child_status)
    raise CommandError('t3c publish run: ' + result.refusal, result.code)


def register(registry):
    parser = registry.group('publish').add_parser('run', help='run a command with an App token')
    parser.add_argument('--status-file', required=True)
    parser.add_argument('child_argv', nargs=argparse.REMAINDER)
    parser.set_defaults(handler=run)
