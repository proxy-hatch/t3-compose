"""Offline pre-mint refusals through the real entry point and synthetic Git."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import stat
import subprocess
import sys
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT, FakeRunner
from t3compose import github_app as app, process

SECRET = 'synthetic-preflight-secret-7291'
TOKEN = 'synthetic-preflight-token-8352'


class OfflineTokenPreconditions(OfflineTestCase):
    def setUp(self):
        self.global_file = Path(os.environ['GIT_CONFIG_GLOBAL'])
        self.home = Path(os.environ['HOME'])
        self.local = self.temp / 'state'
        self.local.mkdir()
        self.status = self.temp / 'status.json'
        config = {'version': 1, 'app': {'slug': 'fixture-worker', 'appId': 123,
                  'installationAccount': 'fixture-org',
                  'keychain': {'service': 'fixture-service', 'account': 'fixture-account'}},
                  'repository': {'owner': 'fixture-org', 'name': 'fixture-repo',
                                 'permissions': {'contents': 'write'}}}
        (self.local / 'github-app.json').write_text(json.dumps(config))
        self.git = shutil.which('git')
        self.git_run('init', '-q')
        self.config = Path.cwd() / '.git/config'
        self.initial = self.config.read_text()
        self.old_handlers = {s: signal.getsignal(s) for s in
                             (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def git_run(self, *args):
        return process.run([self.git, *args], stdin=subprocess.DEVNULL,
                           capture_output=True, check=True, timeout=5)

    def setting(self, key, value, path=None):
        self.git_run('config', '--file', str(path or self.config), '--add', key, value)

    def fresh(self):
        self.status.unlink(missing_ok=True)
        self.config.write_text(self.initial)
        self.global_file.write_text('')

    def invoke(self, command=None):
        command = ['/usr/bin/true'] if command is None else command
        argv = ['t3c', '--local-dir', str(self.local), 'publish', 'run',
                '--status-file', str(self.status), '--', *command]
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stopped:
            runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
        return stopped.exception.code, out.getvalue(), err.getvalue()

    def refuse(self, kind, command=None, *, code=1, error_class=None):
        self.status.unlink(missing_ok=True)
        before = dict(os.environ)
        with patch.object(app, 'load_config', wraps=app.load_config) as config, \
                patch.object(app, 'default_runtime', return_value=app.Runtime(lambda *a: None)) as rt, \
                patch.object(app, 'TokenLease', wraps=app.TokenLease) as lease, \
                patch.object(app, 'mint_token', side_effect=app.CheckError(SECRET)) as mint, \
                patch.object(process, 'start', side_effect=AssertionError('unexpected child')) as start, \
                patch.object(app, 'read_keychain_secret', side_effect=AssertionError('unexpected key')) as key:
            result, out, err = self.invoke(command)
        self.assertEqual(result, code)
        self.assertEqual(json.loads(self.status.read_text()),
                         {'childStarted': False, 'refusal': kind})
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o600)
        self.assertEqual(out, '')
        self.assertIn(kind, err)
        if error_class:
            self.assertIn('command failed (' + error_class + ')', err)
        else:
            self.assertEqual(err, 't3c publish run: ' + kind + '\n')
        for sentinel in (SECRET, TOKEN, str(self.home), str(self.config)):
            self.assertNotIn(sentinel, out + err + self.status.read_text())
        for seam in (config, rt, lease, mint, start, key):
            seam.assert_not_called()
        self.assertEqual(dict(os.environ), before)
        self.assertEqual({s: signal.getsignal(s) for s in self.old_handlers}, self.old_handlers)

    def test_each_unsafe_setting_refuses_before_mint(self):
        cases = [('http.extraheader', 'git-extraheader'),
                 ('http..extraheader', 'git-extraheader'),
                 ('http.https://fixture.example/a.b.extraheader', 'git-extraheader'),
                 ('url..insteadOf', 'git-url-rewrite'),
                 ('url.https://fixture.example/a.b.insteadOf', 'git-url-rewrite'),
                 ('url.https://fixture.example/a.b.pushInsteadOf', 'git-url-rewrite'),
                 ('trace2.envVars', 'git-trace-env')]
        for key, kind in cases:
            for value in ('', SECRET + '\nsecond-line'):
                with self.subTest(key=key, value_empty=not value):
                    self.fresh()
                    self.setting(key, value)
                    self.refuse(kind)

    def test_effective_sources_and_includes_are_read(self):
        # The guard inserts its synthetic global file. HOME/XDG are included
        # explicitly here; this does not claim normal global discovery coverage.
        sources = [self.config, self.global_file, self.home / '.gitconfig',
                   Path(os.environ['XDG_CONFIG_HOME']) / 'git/config',
                   self.temp / 'included-config']
        for source in sources:
            with self.subTest(source=source.name):
                self.fresh()
                source.parent.mkdir(parents=True, exist_ok=True)
                if source not in (self.config, self.global_file):
                    source.write_text('')
                    self.setting('include.path', str(source))
                self.setting('http.extraheader', SECRET, source)
                self.refuse('git-extraheader')
        self.fresh()
        include = self.temp / 'conditional-config'
        self.setting('trace2.envVars', SECRET, include)
        self.setting('includeIf.gitdir:' + str(Path.cwd() / '.git') + '.path', str(include))
        self.refuse('git-trace-env')
        self.fresh()
        self.setting('extensions.worktreeConfig', 'true')
        self.setting('http.extraheader', SECRET, Path.cwd() / '.git/config.worktree')
        self.refuse('git-extraheader')

    def test_inherited_clean_decoys_cannot_hide_forbidden_repository_setting(self):
        decoy = self.temp / 'clean-config'
        decoy.write_text('')
        self.setting('http.extraheader', SECRET)
        with patch.dict(os.environ, {'GIT_CONFIG': str(decoy), 'GIT_CONFIG_GLOBAL': str(decoy),
                                    'GIT_CONFIG_SYSTEM': str(decoy), 'GIT_CONFIG_NOSYSTEM': '1',
                                    'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.sslVerify',
                                    'GIT_CONFIG_VALUE_0': 'true', 'GIT_CONFIG_PARAMETERS': "'http.sslVerify=true'"}):
            self.refuse('git-extraheader')

    def test_false_git_booleans_global_and_url_specific(self):
        for key in ('http.sslVerify', 'http.https://fixture.example/a.b.sslVerify'):
            for value in ('', 'false', 'NO', 'Off', '0', '00', '+0', '-0', '0x0', '0k'):
                with self.subTest(key=key, value=value):
                    self.fresh()
                    self.setting(key, value)
                    self.refuse('git-ssl-verify')
        self.fresh()
        with self.config.open('a') as stream:
            stream.write('\n[HTTP]\n SSLVerify = \t FaLsE \t\n')
        self.refuse('git-ssl-verify')
        self.setting('http.sslVerify', 'true')  # Earlier unsafe values also refuse.
        self.refuse('git-ssl-verify')

    def test_invalid_boolean_and_invalid_config_fail_closed(self):
        for value in (SECRET, ' false ', '0invalid'):
            with self.subTest(value=value):
                self.fresh()
                self.setting('http.sslVerify', value)
                self.refuse('git-config')
        self.config.write_text('[broken ' + SECRET)
        self.refuse('git-config')
        self.fresh()
        unreadable = self.temp / 'config-directory'
        unreadable.mkdir()
        self.setting('include.path', str(unreadable))
        self.refuse('git-config')

    def test_mixed_case_and_valueless_settings(self):
        for text, kind in [('[HTTP "https://fixture.example/a.b"]\n ExtraHeader', 'git-extraheader'),
                           ('[URL "https://fixture.example/a.b"]\n PushInsteadOf', 'git-url-rewrite'),
                           ('[TRACE2]\n envVars', 'git-trace-env')]:
            with self.subTest(kind=kind):
                self.fresh()
                with self.config.open('a') as stream:
                    stream.write('\n' + text + '\n')
                self.refuse(kind)

    def test_secret_subsections_and_newline_values_are_not_diagnostics(self):
        self.setting('http.https://' + SECRET + '.example/a.b.extraheader', SECRET + '\nmore')
        self.refuse('git-extraheader')
        self.fresh()
        self.setting('fixture.multiline', 'text\nhttp.extraheader\n' + SECRET)
        self.setting('http.https://fixture.example/a.b.proxy', 'http://other.invalid')
        self.succeeds()

    def test_system_config_refuses_through_synthetic_process_fixture(self):
        # The production base still has no overrides. Only this test's process
        # fixture selects a synthetic system file; the offline guard stays on.
        system = self.temp / 'system-config'
        system.write_text('')
        self.setting('http.extraheader', SECRET, system)
        real = process.run
        def synthetic_system(argv, **kw):
            self.assertFalse(any(k.startswith('GIT_CONFIG') for k in kw['env']))
            kw['env'] = dict(kw['env'], GIT_CONFIG_SYSTEM=str(system), GIT_CONFIG_NOSYSTEM='0')
            return real(argv, **kw)
        with patch.object(process, 'run', synthetic_system):
            self.refuse('git-extraheader')

    def test_netrc_refusals_and_unset_home(self):
        netrc = self.home / '.netrc'
        for content, kind in [('machine github.com login ' + SECRET, 'netrc-github'),
                              ('machine "GitHub.COM" login "' + SECRET + '" \t\n', 'netrc-github'),
                              ('DEFAULT login ' + SECRET, 'netrc-default'),
                              ('"default" login ' + SECRET + '\n', 'netrc-default'),
                              ('# github.com ' + SECRET, 'netrc-github')]:
            with self.subTest(kind=kind, content=content[:12]):
                netrc.write_text(content)
                self.refuse(kind)
        netrc.unlink()
        netrc.mkdir()
        self.refuse('netrc-unreadable')
        netrc.rmdir()
        for home in (None, ''):
            with patch.dict(os.environ):
                if home is None:
                    del os.environ['HOME']
                else:
                    os.environ['HOME'] = home
                self.refuse('home-unset')

    def test_shells_and_symlinked_shells_are_never_executed(self):
        names = 'sh bash rbash zsh dash ash ksh ksh93 mksh pdksh csh tcsh fish yash'.split()
        for name in names:
            for program in (name, '/synthetic/bin/' + name):
                with self.subTest(program=program):
                    self.refuse('shell-command', [program, '-c', SECRET])
        alias = Path.cwd() / 'inert-shell-alias'
        alias.symlink_to('/bin/sh')
        self.refuse('shell-command', [str(alias), '-c', SECRET])

    def test_git_global_overrides_and_equivalent_forms_refuse(self):
        alias = Path.cwd() / 'inert-git-alias'
        alias.symlink_to(self.git)
        programs = ['git', self.git, str(alias)]
        forms = [(['-C', '.'], 'git-directory'), (['-C.'], 'git-directory'),
                 (['-c', 'http.extraheader=' + SECRET], 'git-config-option'),
                 (['-chttp.extraheader=' + SECRET], 'git-config-option'),
                 (['--config-env=http.extraheader=FIXTURE'], 'git-config-option'),
                 (['--git-dir=.git'], 'git-option'), (['--work-tree=.'], 'git-option'),
                 (['--namespace=fixture'], 'git-option'), (['--bare'], 'git-option'),
                 (['--exec-path=/synthetic'], 'git-option'), (['--'], 'git-option')]
        for program in programs:
            for args, kind in forms:
                with self.subTest(program=Path(program).name, args=args[:1]):
                    self.refuse(kind, [program, '--no-pager', *args, 'status'])

    def test_relative_path_resolution_uses_captured_cwd(self):
        bindir = Path.cwd() / 'bin'
        bindir.mkdir()
        (bindir / 'git').symlink_to(self.git)
        (bindir / 'alias').symlink_to(self.git)
        with patch.dict(os.environ, {'PATH': 'bin:/usr/bin:/bin'}):
            self.refuse('git-directory', ['alias', '-C.', 'status'])
            self.refuse('git-config-option', ['./bin/git', '-cx.y=z', 'status'])

    def test_git_read_failures_have_static_kinds(self):
        for kind in ('not-found', 'timeout', 'os-error', 'exit'):
            with self.subTest(kind=kind):
                failure = process.ProcessError(kind)
                failure.args = (SECRET,)
                fake = FakeRunner({'git': lambda *a, **kw: (_ for _ in ()).throw(failure)})
                with patch.object(process, 'run', fake):
                    self.refuse('git-config')
        for returncode, data in [(7, SECRET.encode()), (0, b'not-terminated'),
                                 (0, b'\0'), (0, b'invalid-key\n' + SECRET.encode() + b'\0')]:
            with self.subTest(returncode=returncode, data=data[:10]):
                fake = FakeRunner({'git': lambda argv, **kw:
                                   subprocess.CompletedProcess(argv, returncode, data, SECRET.encode())})
                with patch.object(process, 'run', fake):
                    self.refuse('git-config')

    def test_missing_override_capability_refuses(self):
        real = process.run
        def no_overrides(argv, **kw):
            if kw['env'].get('GIT_CONFIG_COUNT'):
                return subprocess.CompletedProcess(argv, 0, b'core.bare\nfalse\0')
            return real(argv, **kw)
        with patch.object(process, 'run', no_overrides):
            self.refuse('git-config-overrides')

    def test_boolean_read_and_probe_failures_cannot_reach_mint(self):
        real = process.run
        self.setting('http.sslVerify', 'true')
        for surface in ('bool', 'probe'):
            for result in ('timeout', 'nonzero', 'malformed'):
                with self.subTest(surface=surface, result=result):
                    def broken(argv, **kw):
                        selected = ('--type=bool' in argv if surface == 'bool' else
                                    'GIT_CONFIG_COUNT' in kw['env'])
                        if not selected:
                            return real(argv, **kw)
                        if result == 'timeout':
                            raise process.ProcessError('timeout')
                        return subprocess.CompletedProcess(argv, 7 if result == 'nonzero' else 0,
                                                           SECRET.encode(), SECRET.encode())
                    with patch.object(process, 'run', broken):
                        self.refuse('git-config')

    def test_programming_errors_in_git_seam_are_not_expected_refusals(self):
        for error in (RuntimeError(SECRET), process.ProcessError('invalid-options'),
                      process.ProcessError('invalid-command')):
            with self.subTest(error=type(error).__name__):
                with patch.object(process, 'run', side_effect=error):
                    self.refuse('setup-failed', code=2, error_class=type(error).__name__)

    def test_netrc_read_error_is_public_kind_only(self):
        with patch.object(Path, 'read_bytes', side_effect=PermissionError(SECRET)):
            self.refuse('netrc-unreadable')

    def test_unexpected_preflight_errors_stay_exit_two_and_class_only(self):
        # Import inside the method so base red tests exercise missing behavior.
        from t3compose import token_preconditions as preflight
        for error in (RuntimeError(SECRET), ValueError(SECRET), TypeError(SECRET), SystemExit(SECRET)):
            with self.subTest(error=type(error).__name__):
                with patch.object(preflight, 'check', side_effect=error):
                    self.refuse('setup-failed', code=2, error_class=type(error).__name__)
        for error in (KeyboardInterrupt(SECRET), InterruptedError(SECRET)):
            with patch.object(preflight, 'check', side_effect=error):
                self.refuse('interrupted')

    def succeeds(self, command=None):
        self.status.unlink(missing_ok=True)
        self.allow_program('/usr/bin/true')
        before = dict(os.environ)
        reads, starts, events = [], [], []
        real_run, real_start = process.run, process.start
        def inspect(argv, **kw):
            self.assertNotIn('GH_TOKEN', kw['env'])
            self.assertEqual(kw['cwd'], os.getcwd())
            self.assertEqual(kw['stdin'], subprocess.DEVNULL)
            self.assertEqual(kw['stderr'], subprocess.DEVNULL)
            self.assertEqual(kw['stdout'], subprocess.PIPE)
            self.assertGreater(kw['timeout'], 0)
            self.assertLessEqual(kw['timeout'], 10)
            reads.append((list(argv), dict(kw['env'])))
            return real_run(argv, **kw)
        def mint(config, rt, secrets, lease):
            self.assertTrue(reads)
            events.append('mint')
            lease.token, lease.expires_at = TOKEN, '2030-01-02T03:04:05Z'
            secrets.add(TOKEN)
        def start(argv, **kw):
            starts.append(dict(kw['env']))
            events.append('start')
            self.assertEqual(kw['cwd'], os.getcwd())
            return real_start(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
        def revoke(request, timeout):
            self.assertEqual(request.get_method(), 'DELETE')
            events.append('revoke')
            return io.BytesIO(b'')
        with patch.object(process, 'run', inspect), patch.object(process, 'start', start), \
                patch.object(app, 'mint_token', mint), \
                patch.object(app, 'default_runtime', lambda: app.Runtime(revoke)):
            self.assertEqual(self.invoke(command), (0, '', ''))
        self.assertEqual(events, ['mint', 'start', 'revoke'])
        self.assertEqual(json.loads(self.status.read_text()),
                         {'childStarted': True, 'childStatus': 0, 'revocation': 'ok'})
        self.assertEqual(dict(os.environ), before)
        execution_base = {k: v for k, v in starts[0].items()
                          if k != 'GH_TOKEN' and not k.startswith('GIT_CONFIG_')}
        ordinary_reads = [env for argv, env in reads if 'GIT_CONFIG_COUNT' not in env]
        self.assertTrue(ordinary_reads)
        for env in ordinary_reads:
            self.assertEqual(env, execution_base)
        return reads

    def test_true_git_booleans_and_safe_netrc_allow_inert_child(self):
        for value in ('true', 'YES', 'On', '1', '2', '-1', '0x1', '1k'):
            with self.subTest(value=value):
                self.fresh()
                self.setting('http.sslVerify', value)
                self.setting('http.https://fixture.example/a.b.sslVerify', value)
                self.succeeds()
        self.fresh()
        with self.config.open('a') as stream:
            stream.write('\n[http]\n sslVerify\n')
        self.succeeds()
        (self.home / '.netrc').write_text('machine other.invalid login fixture password fixture \n')
        self.succeeds()

    def test_sanitized_base_and_path_match_execution(self):
        decoy = self.temp / 'clean-config'
        decoy.write_text('')
        unsafe = ('GH_TOKEN GITHUB_TOKEN GIT_CONFIG GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM '
                  'GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS GIT_CONFIG_KEY_99 GIT_CONFIG_VALUE_99 '
                  'GIT_TRACE GIT_TRACE2_ENV_VARS GIT_SSL_NO_VERIFY GH_DEBUG').split()
        with patch.dict(os.environ, dict.fromkeys(unsafe, str(decoy))):
            reads = self.succeeds()
        for argv, env in reads:
            self.assertNotIn('GIT_CONFIG', env)
            self.assertNotIn('GIT_TRACE', env)
            self.assertNotIn('GIT_CONFIG_KEY_99', env)
            self.assertEqual(argv[0], 'git')

    def test_explicit_git_is_also_the_inspected_git(self):
        # The system Git launcher dispatches by basename; preserve "git" for
        # positive execution. Renamed aliases above are inert refusal tests.
        alias = Path.cwd() / 'synthetic-bin/git'
        alias.parent.mkdir()
        alias.symlink_to(self.git)
        for program in (self.git, str(alias)):
            with self.subTest(program=Path(program).name):
                reads = self.succeeds([program, 'version'])
                self.assertTrue(all(argv[0] == program for argv, env in reads))

    def test_git_subcommand_options_are_not_global_overrides(self):
        from t3compose import token_preconditions as preflight
        from t3compose.token_run import sanitized_environment
        # Only preflight runs: an actual commit would be a non-inert child.
        for args in (['commit', '-c', SECRET], ['show', '-C'], ['config', '--get', 'x.y']):
            preflight.check(['git', '--no-pager', *args],
                            env=sanitized_environment(os.environ), cwd=os.getcwd())
        self.succeeds(['git', '--no-pager', 'version'])
