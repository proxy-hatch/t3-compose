"""Offline runner integration: synthetic credentials and inert real children."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import threading
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT, BOOTSTRAP_PATH
from t3compose import github_app as ga, jsonio, process

TOKEN = 'synthetic-run-token-0123456789'
EXPIRY = '2030-01-02T03:04:05Z'
REAL_MINT = ga.mint_token


class OfflineTokenRun(OfflineTestCase):
    def setUp(self):
        self.local = self.temp / 'state'
        self.local.mkdir()
        self.status = self.temp / 'status.json'
        config = {'version': 1, 'app': {'slug': 'fixture-worker', 'appId': 123,
                  'installationAccount': 'fixture-org',
                  'keychain': {'service': 'fixture-service', 'account': 'fixture-account'}},
                  'repository': {'owner': 'fixture-org', 'name': 'fixture-repo',
                                 'permissions': {'contents': 'write'}}}
        (self.local / 'github-app.json').write_text(json.dumps(config))
        self.env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.temp / 'home'),
                    'XDG_CONFIG_HOME': str(self.temp / 'xdg-config'),
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_TERMINAL_PROMPT': '0',
                    'LC_ALL': 'C', 'KEEP_ME': 'kept'}
        self.enterContext(patch.dict(os.environ, self.env, clear=True))
        self.allow_program('/usr/bin/printenv')
        self.allow_program('/bin/sleep')
        self.children = []
        self.events = []
        self.start = process.start
        self.mint_error = self.revoke_error = None
        self.after_start = None
        self.sink_out = self.enterContext(open(self.temp / 'stdout', 'w+b'))
        self.sink_err = self.enterContext(open(self.temp / 'stderr', 'w+b'))
        self.addCleanup(self.reap)
        self.enterContext(patch.object(ga, 'default_runtime', lambda: ga.Runtime(self.http)))
        self.enterContext(patch.object(ga, 'mint_token', self.mint))
        self.enterContext(patch.object(process, 'start', self.capture_start))

    def reap(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)

    def mint(self, config, rt, secrets, lease):
        self.events.append('mint')
        secrets.add('synthetic-private-key')
        secrets.add('synthetic-jwt')
        secrets.add(TOKEN)
        lease.token, lease.expires_at = TOKEN, EXPIRY
        if self.mint_error:
            raise self.mint_error
        return ga.Minted(456, 'selected', {'contents': 'write', 'metadata': 'read'})

    def http(self, request, timeout):
        self.assertEqual(request.get_method(), 'DELETE')
        self.assertEqual(request.full_url, 'https://api.github.com/installation/token')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertTrue(all(child.poll() is not None for child in self.children),
                        'revocation must follow child termination')
        self.events.append('revoke')
        if self.revoke_error:
            raise self.revoke_error
        return io.BytesIO(b'')

    def capture_start(self, argv, **kwargs):
        self.assertEqual(self.record(), {'childStarted': True, 'childStatus': None})
        self.assertEqual(Path(kwargs['cwd']), Path.cwd())
        self.assertNotIn('stdout', kwargs)
        self.assertNotIn('stderr', kwargs)
        self.launch = (list(argv), dict(kwargs))
        child = self.start(argv, stdout=self.sink_out, stderr=self.sink_err, **kwargs)
        self.children.append(child)
        self.events.append('start')
        if self.after_start:
            self.after_start(child)
        return child

    def invoke(self, command=('/usr/bin/printenv', 'KEEP_ME'), *, args=None, text=False):
        argv = ['t3c', *(['--text'] if text else []), '--local-dir', str(self.local), 'publish', 'run']
        argv += args if args is not None else ['--status-file', str(self.status), '--', *command]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
        self.assertEqual(out.getvalue(), '', 'no helper stdout wrapper')
        return stopped.exception.code, err.getvalue()

    def record(self):
        return json.loads(self.status.read_text())

    def output(self, sink):
        sink.seek(0)
        return sink.read()

    def script(self, code, *args):
        target = self.temp / 'child.py'
        target.write_text(code)
        return [sys.executable, '-I', '-S', '-B', str(BOOTSTRAP_PATH), str(target), *args]

    def fresh(self):
        self.status.unlink(missing_ok=True)
        self.events.clear()
        for sink in (self.sink_out, self.sink_err):
            sink.seek(0)
            sink.truncate()

    def test_real_entry_passes_output_and_records_success(self):
        self.assertEqual(self.invoke(), (0, ''))
        self.assertEqual(self.output(self.sink_out), b'kept\n')
        self.assertEqual(self.output(self.sink_err), b'')
        self.assertEqual(self.record(), {'childStarted': True, 'childStatus': 0, 'revocation': 'ok'})
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o600)
        self.assertEqual(self.events, ['mint', 'start', 'revoke'])

    def test_global_text_does_not_wrap_child_output(self):
        self.assertEqual(self.invoke(text=True), (0, ''))
        self.assertEqual(self.output(self.sink_out), b'kept\n')

    def test_environment_removes_all_unsafe_variables_without_mutating_parent(self):
        unsafe = ('GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN '
                  'GH_HOST GH_DEBUG GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM '
                  'GIT_CONFIG_NOSYSTEM GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT '
                  'GIT_CONFIG_KEY_99 GIT_CONFIG_VALUE_99 GIT_CONFIG_UNKNOWN '
                  'GIT_ALLOW_PROTOCOL GIT_SSL_NO_VERIFY GIT_TRACE GIT_TRACE2_ENV_VARS '
                  'GIT_CURL_VERBOSE GIT_REDACT_COOKIES').split()
        os.environ.update({name: 'synthetic-old-value' for name in unsafe})
        before = dict(os.environ)
        code, err = self.invoke(('/usr/bin/printenv',))
        self.assertEqual((code, err), (0, ''))
        child_env = self.launch[1]['env']
        self.assertEqual(os.environ, before)
        for name in unsafe:
            if name not in ('GH_TOKEN', 'GIT_CONFIG_COUNT'):
                self.assertNotIn(name, child_env)
        self.assertEqual(child_env['GH_TOKEN'], TOKEN)
        self.assertEqual(child_env['KEEP_ME'], 'kept')
        self.assertEqual(child_env['GIT_CONFIG_COUNT'], '3')
        self.assertEqual(child_env['GIT_CONFIG_KEY_0'], 'credential.helper')
        self.assertEqual(child_env['GIT_CONFIG_VALUE_0'], '')
        for name, value in child_env.items():
            if name != 'GH_TOKEN':
                self.assertNotIn(TOKEN, value)
        dump = self.output(self.sink_out).decode()
        self.assertIn('GH_TOKEN=' + TOKEN + '\n', dump)
        for secret in ('synthetic-old-value', 'synthetic-private-key', 'synthetic-jwt'):
            self.assertNotIn(secret, dump + err + repr(self.launch[0]) + self.status.read_text())

    def test_child_exit_collisions_and_option_arguments_pass_through(self):
        for code in (0, 1, 2, 3, 7, 75, 255):
            with self.subTest(code=code):
                self.fresh()
                command = self.script('import os,sys\nos.write(1,b"raw-out\\x00")\n'
                                      'os.write(2,b"raw-err\\n")\n'
                                      'assert sys.argv[2:] == ["--text", "--help", "--local-dir"]\n'
                                      'sys.exit(int(sys.argv[1]))\n', str(code),
                                      '--text', '--help', '--local-dir')
                self.assertEqual(self.invoke(command), (code, ''))
                self.assertEqual(self.output(self.sink_out), b'raw-out\x00')
                self.assertEqual(self.output(self.sink_err), b'raw-err\n')
                self.assertEqual(self.record(), {'childStarted': True, 'childStatus': code,
                                                 'revocation': 'ok'})

    def test_syntax_and_parser_no_file_boundaries(self):
        for suffix, recorded in [([], False), (['--status-file'], False),
                                 (['--status-file', str(self.status)], True),
                                 (['--status-file', str(self.status), '--'], True),
                                 (['--status-file', str(self.status), '/bin/sleep', '0'], True),
                                 (['--status-file', str(self.status), '--bogus'], False)]:
            with self.subTest(suffix=suffix):
                self.fresh()
                code, err = self.invoke(args=suffix)
                self.assertEqual(code, 2)
                self.assertTrue(err)
                self.assertEqual(self.status.exists(), recorded)
                if recorded:
                    self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'invalid-input'})
                self.assertEqual(self.events, [])

    def test_config_failure_records_no_start_from_explicit_state(self):
        (self.local / 'github-app.json').write_text('{"secret": "synthetic-private-key"}')
        code, err = self.invoke()
        self.assertEqual(code, 3)
        self.assertNotIn('synthetic-private-key', err)
        self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'invalid-config'})
        self.assertEqual(self.events, [])

    def test_mint_failure_and_malformed_token_still_revoke(self):
        self.mint_error = ga.CheckError('synthetic-private-key ' + TOKEN)
        code, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'mint-failed'})
        self.assertEqual(self.events, ['mint', 'revoke'])
        self.assertNotIn(TOKEN, err)
        self.assertNotIn('synthetic-private-key', err)

    def test_start_failure_replaces_intent_and_revokes(self):
        for kind in ('not-found', 'os-error', 'invalid-options'):
            with self.subTest(kind=kind):
                self.fresh()
                def fail(*args, **kwargs):
                    self.assertEqual(self.record(), {'childStarted': True, 'childStatus': None})
                    error = process.ProcessError(kind)
                    error.args = (TOKEN,)  # Even injected raw error text is never logged.
                    raise error
                with patch.object(process, 'start', fail):
                    code, err = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'start-failed'})
                self.assertEqual(self.events, ['mint', 'revoke'])
                self.assertNotIn(TOKEN, err)

    def test_revocation_failures_preserve_child_status_and_redact_warning(self):
        for error in (ga.CheckError(TOKEN + '\nsecret'), RuntimeError(TOKEN),
                      KeyboardInterrupt(), SystemExit(TOKEN)):
            with self.subTest(error=type(error).__name__):
                self.fresh()
                self.revoke_error = error
                code, err = self.invoke(self.script('raise SystemExit(75)\n'))
                self.assertEqual(code, 75)
                self.assertIn('expires_at=' + EXPIRY, err)
                self.assertNotIn(TOKEN, err)
                self.assertEqual(self.record(), {'childStarted': True, 'childStatus': 75,
                                                 'revocation': 'failed'})

    def test_existing_status_is_never_accepted_as_fresh_or_overwritten(self):
        for value in ({'childStarted': True, 'childStatus': 0, 'revocation': 'ok'},
                      {'childStarted': True, 'childStatus': None}):
            self.status.write_text(json.dumps(value))
            code, err = self.invoke()
            self.assertEqual(code, 2)
            self.assertIn('existing', err)
            self.assertEqual(self.record(), value)
            self.assertEqual(self.events, [])

    def test_atomic_failures_before_intent_never_launch(self):
        original = jsonio.atomic_replace
        for published in (False, True):
            with self.subTest(published=published):
                self.fresh()
                def broken(path, value, **kwargs):
                    if published:
                        original(path, value, **kwargs)
                    raise OSError(TOKEN)
                with patch.object(jsonio, 'atomic_replace', broken):
                    code, err = self.invoke()
                self.assertEqual(code, 2)
                self.assertNotIn('start', self.events)
                self.assertEqual(self.events, ['mint', 'revoke'])
                self.assertNotIn(TOKEN, err)
                self.assertIn('status', err)
                if published:
                    self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'status-failed'})
                else:
                    self.assertFalse(self.status.exists())

    def test_final_atomic_failures_keep_actual_outcome_or_uncertain_intent(self):
        original = jsonio.atomic_replace
        for published in (False, True):
            with self.subTest(published=published):
                self.fresh()
                def broken(path, value, **kwargs):
                    if value.get('childStatus') is not None:
                        if published:
                            original(path, value, **kwargs)
                        raise OSError(TOKEN)
                    original(path, value, **kwargs)
                with patch.object(jsonio, 'atomic_replace', broken):
                    code, err = self.invoke(self.script('raise SystemExit(3)\n'))
                self.assertEqual(code, 3)
                self.assertIn('reconcil', err)
                self.assertNotIn(TOKEN, err)
                self.assertEqual(self.record(), {'childStarted': True, 'childStatus': 3,
                                                 'revocation': 'ok'} if published else
                                 {'childStarted': True, 'childStatus': None})
                self.assertEqual(self.events, ['mint', 'start', 'revoke'])

    def test_forwarded_signals_reap_real_sleep_before_revoke_and_restore_handlers(self):
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
            signal.signal(sig, lambda *args: None)
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            with self.subTest(sig=sig):
                self.fresh()
                original = {s: signal.getsignal(s) for s in
                            (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)}
                ready, sent = threading.Event(), threading.Event()
                errors = []
                def send():
                    try:
                        if not ready.wait(5):
                            raise AssertionError('child startup did not complete')
                        os.kill(os.getpid(), sig)
                    except BaseException as error:
                        errors.append(error)
                    finally:
                        sent.set()
                def started(child):
                    self.assertIsNone(child.poll())
                    self.assertEqual(self.record(), {'childStarted': True, 'childStatus': None})
                    ready.set()
                    self.assertTrue(sent.wait(5))
                self.after_start = started
                thread = threading.Thread(target=send)
                thread.start()
                try:
                    self.assertEqual(self.invoke(('/bin/sleep', '5')), (128 + sig, ''))
                    self.assertEqual(self.record(), {'childStarted': True, 'childStatus': 128 + sig,
                                                     'revocation': 'ok'})
                    self.assertEqual(self.events, ['mint', 'start', 'revoke'])
                finally:
                    ready.set()
                    thread.join(6)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual({s: signal.getsignal(s) for s in original}, original)

    def test_signal_during_mint_refuses_start_but_revokes_and_restores(self):
        original = signal.getsignal(signal.SIGTERM)
        mint = self.mint
        def interrupted(*args):
            value = mint(*args)
            os.kill(os.getpid(), signal.SIGTERM)
            return value
        # A broken implementation must fail an assertion, not terminate SUITE.
        signal.signal(signal.SIGTERM, lambda *args: None)
        self.addCleanup(signal.signal, signal.SIGTERM, original)
        with patch.object(ga, 'mint_token', interrupted):
            code, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'interrupted'})
        self.assertEqual(self.events, ['mint', 'revoke'])
        self.assertTrue(err)

    def test_wait_failures_cannot_revoke_while_child_is_alive(self):
        for error in (KeyboardInterrupt(), SystemExit(TOKEN), OSError(TOKEN)):
            with self.subTest(error=type(error).__name__):
                self.fresh()
                def started(child):
                    wait = child.wait
                    first = True
                    def interrupted(*args, **kwargs):
                        nonlocal first
                        if first:
                            first = False
                            raise error
                        return wait(*args, **kwargs)
                    child.wait = interrupted
                self.after_start = started
                code, err = self.invoke(('/bin/sleep', '0.02'))
                self.assertEqual(code, 0)
                self.assertNotIn(TOKEN, err)
                self.assertEqual(self.record(), {'childStarted': True, 'childStatus': 0,
                                                 'revocation': 'ok'})
                self.assertEqual(self.events, ['mint', 'start', 'revoke'])

    def test_wait_error_does_not_delay_pending_signal_until_natural_exit(self):
        def started(child):
            wait = child.wait
            def broken(*args, **kwargs):
                child.wait = wait
                os.kill(os.getpid(), signal.SIGTERM)
                raise OSError(TOKEN)
            child.wait = broken
        self.after_start = started
        self.assertEqual(self.invoke(('/bin/sleep', '0.3')), (128 + signal.SIGTERM, ''))
        self.assertEqual(self.record()['childStatus'], 128 + signal.SIGTERM)

    def test_real_mint_malformed_response_is_owned_by_lease(self):
        def http(request, timeout):
            if request.get_method() == 'DELETE':
                return self.http(request, timeout)
            if request.get_method() == 'GET':
                return io.BytesIO(b'[{"id":456,"account":{"login":"fixture-org"}}]')
            return io.BytesIO(json.dumps({'token': TOKEN, 'expires_at': 17}).encode())
        with patch.object(ga, 'mint_token', REAL_MINT), \
                patch.object(ga, 'read_keychain_secret', return_value=b'synthetic-key'), \
                patch.object(ga, 'decode_keychain_pem', return_value=b'synthetic-private-key'), \
                patch.object(ga, 'build_jwt', return_value='synthetic-jwt'), \
                patch.object(ga, 'default_runtime', lambda: ga.Runtime(http)):
            code, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'mint-failed'})
        self.assertEqual(self.events, ['revoke'])
        self.assertNotIn(TOKEN, err)

    def test_unexpected_lease_exit_error_preserves_known_status(self):
        original = ga.TokenLease.__exit__
        def broken(lease, *exc):
            original(lease, *exc)
            raise RuntimeError(TOKEN)
        with patch.object(ga.TokenLease, '__exit__', broken):
            code, err = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(self.record(), {'childStarted': True, 'childStatus': 0,
                                         'revocation': 'failed'})
        self.assertIn('expires_at=' + EXPIRY, err)
        self.assertNotIn(TOKEN, err)

    def test_real_atomic_rename_and_directory_sync_failures(self):
        original = jsonio.os.fsync
        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(TOKEN)
            original(fd)
        for surface, replacement in (('replace', lambda *a, **k: (_ for _ in ()).throw(OSError(TOKEN))),
                                     ('fsync', sync)):
            with self.subTest(surface=surface):
                self.fresh()
                with patch.object(jsonio.os, surface, replacement):
                    code, err = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(self.events, ['mint', 'revoke'])
                self.assertNotIn(TOKEN, err)
                if surface == 'fsync':
                    self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'status-failed'})
                else:
                    self.assertFalse(self.status.exists())
                self.assertEqual(list(self.temp.glob('.t3c-tmp-*')), [])

    def test_failed_no_start_record_leaves_uncertain_intent_for_reconciliation(self):
        original = jsonio.atomic_replace
        def write(path, value, **kwargs):
            if not value['childStarted']:
                raise OSError(TOKEN)
            original(path, value, **kwargs)
        with patch.object(jsonio, 'atomic_replace', write), \
                patch.object(process, 'start', side_effect=process.ProcessError('not-found')):
            code, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(self.record(), {'childStarted': True, 'childStatus': None})
        self.assertEqual(self.events, ['mint', 'revoke'])
        self.assertIn('no child started', err)

    def test_missing_status_parent_never_starts_and_does_not_create_directories(self):
        self.status = self.temp / 'missing' / 'status.json'
        code, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertFalse(self.status.parent.exists())
        self.assertEqual(self.events, ['mint', 'revoke'])
        self.assertIn('status', err)

    def test_pending_signal_after_intent_prevents_launch(self):
        original = jsonio.atomic_replace
        def write(path, value, **kwargs):
            original(path, value, **kwargs)
            if value['childStarted']:
                os.kill(os.getpid(), signal.SIGHUP)
        with patch.object(jsonio, 'atomic_replace', write):
            code, err = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'interrupted'})
        self.assertEqual(self.events, ['mint', 'revoke'])

    def test_signal_during_failed_launch_still_cleans_up_and_restores(self):
        previous = signal.getsignal(signal.SIGHUP)
        def fail(*args, **kwargs):
            os.kill(os.getpid(), signal.SIGHUP)
            raise process.ProcessError('os-error')
        with patch.object(process, 'start', fail):
            code, err = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(self.record(), {'childStarted': False, 'refusal': 'start-failed'})
        self.assertEqual(self.events, ['mint', 'revoke'])
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous)

    def test_handler_restoration_continues_after_one_interruption(self):
        previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        real_signal = signal.signal
        for sig, handler in previous.items():
            self.addCleanup(real_signal, sig, handler)
        interrupted = False
        def install(sig, handler):
            nonlocal interrupted
            if handler == previous[sig] and not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            return real_signal(sig, handler)
        with patch.object(signal, 'signal', install):
            code, err = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual({s: signal.getsignal(s) for s in previous}, previous)
        self.assertEqual(self.record()['childStatus'], 0)
        self.assertEqual(self.events, ['mint', 'start', 'revoke'])

    def test_signal_forwarding_continues_after_transient_send_error(self):
        def started(child):
            send = child.send_signal
            def fail_once(sig):
                child.send_signal = send
                raise RuntimeError(TOKEN)
            child.send_signal = fail_once
            os.kill(os.getpid(), signal.SIGHUP)
        self.after_start = started
        self.assertEqual(self.invoke(('/bin/sleep', '5')), (128 + signal.SIGHUP, ''))
        self.assertEqual(self.events, ['mint', 'start', 'revoke'])

    def test_reusable_result_distinguishes_child_from_failure(self):
        from t3compose import token_run
        result = token_run.run_command(['/bin/sleep', '0'], self.status, local_dir=self.local)
        self.assertTrue(result.child_started)
        self.assertEqual(result.child_status, 0)
        self.assertEqual(result.record(), self.record())
        self.fresh()
        self.mint_error = ga.CheckError('synthetic failure')
        result = token_run.run_command(['/bin/sleep', '0'], self.status, local_dir=self.local)
        self.assertFalse(result.child_started)
        self.assertIsNone(result.child_status)
        self.assertEqual(result.refusal, 'mint-failed')
        self.assertEqual(result.code, 1)

    def test_real_git_helpers_reset_all_scopes_and_only_answer_github(self):
        from t3compose import token_run
        home = Path(self.env['HOME'])
        xdg = Path(self.env['XDG_CONFIG_HOME']) / 'git'
        xdg.mkdir()
        marker = self.temp / 'decoy-ran'
        # Git executes its helper snippets; only shell builtins are used.
        helper = f'!f() {{ echo ran >> {marker}; echo username=decoy; echo password=DECOY; }}; f'
        env = dict(self.env, GIT_CONFIG_GLOBAL=str(home / '.gitconfig'),
                   GIT_CONFIG_SYSTEM=str(self.temp / 'system-config'))
        (self.temp / 'system-config').write_text('')
        process.run(['git', 'init', '-q'], env=env, check=True, capture_output=True)
        for path, section in ((home / '.gitconfig', 'credential'),
                              (home / '.gitconfig', 'credential "https://github.com"'),
                              (xdg / 'config', 'credential'),
                              (Path.cwd() / '.git/config', 'credential')):
            with path.open('a') as stream:
                stream.write(f'\n[{section}]\n\thelper = "{helper}"\n')
        def fill(child_env, host):
            with patch.dict(os.environ, child_env, clear=True):
                return process.run(['git', 'credential', 'fill'],
                                   input=f'protocol=https\nhost={host}\n\n',
                                   capture_output=True, text=True, timeout=5)
        self.assertIn('password=DECOY', fill(env, 'github.com').stdout)
        self.assertTrue(marker.exists())
        marker.unlink()
        clean = token_run.sanitized_environment(env)
        self.assertFalse(any(name.startswith('GIT_CONFIG') for name in clean))
        controlled = token_run.child_environment(clean, TOKEN)
        # A test-only system-config exclusion keeps this fixture wholly offline.
        # env=None avoids the guard adding a global-config override that hides XDG.
        controlled['GIT_CONFIG_NOSYSTEM'] = '1'
        with patch.dict(os.environ, dict(clean, GIT_CONFIG_NOSYSTEM='1'), clear=True):
            listing = process.run(['git', 'config', '--list', '--show-origin'],
                                  capture_output=True, text=True, check=True)
        self.assertEqual(listing.stdout.count('helper='), 4)
        for path in (home / '.gitconfig', xdg / 'config', Path('.git/config')):
            self.assertIn(str(path), listing.stdout)
        result = fill(controlled, 'github.com')
        self.assertEqual(result.returncode, 0)
        self.assertIn('username=x-access-token\n', result.stdout)
        self.assertIn('password=' + TOKEN + '\n', result.stdout)
        self.assertFalse(marker.exists())
        other = fill(controlled, 'example.invalid')
        self.assertNotEqual(other.returncode, 0)
        self.assertNotIn(TOKEN, other.stdout + other.stderr)
        self.assertFalse(marker.exists())

    def test_ssh_transport_refused_despite_inherited_allowlist(self):
        from t3compose import token_run
        marker = self.temp / 'fake-transport-ran'
        # This inert target can only create a marker and fail, even on regression.
        fake = self.temp / 'fake-transport'
        fake.write_text(f'#!/bin/sh\necho ran >> {marker}\nexit 1\n')
        fake.chmod(0o700)
        inherited = dict(self.env, GIT_ALLOW_PROTOCOL='ssh:https',
                         GIT_SSH_COMMAND=str(fake), GIT_SSH_VARIANT='ssh')
        command = ['git', 'ls-remote', 'ssh://git@example.invalid/fixture/repo.git']
        control = process.run(command, env=inherited, capture_output=True, timeout=5)
        self.assertNotEqual(control.returncode, 0)
        self.assertTrue(marker.exists())
        marker.unlink()
        env = token_run.child_environment(token_run.sanitized_environment(inherited), TOKEN)
        self.assertNotIn('GIT_ALLOW_PROTOCOL', env)
        env['GIT_CONFIG_NOSYSTEM'] = '1'
        # env=None avoids the guard's file-only baseline masking a broken override.
        with patch.dict(os.environ, env, clear=True):
            result = process.run(command, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"transport 'ssh' not allowed", result.stderr)
        self.assertFalse(marker.exists())
