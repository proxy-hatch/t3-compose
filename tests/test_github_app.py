"""Offline App checks: synthetic config, fake HTTP/Keychain, throwaway RSA."""
import contextlib
import io
import runpy
import sys
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT


class OfflineEntry(OfflineTestCase):
    def test_publish_check_uses_selected_local_directory(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['t3c', '--local-dir', str(self.temp), 'publish', 'check']), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
        self.assertEqual(stopped.exception.code, 3)
        self.assertEqual(out.getvalue(), '')
        self.assertIn('config', err.getvalue())

import base64
import copy
import importlib
import json
import os
import stat
import subprocess
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace

from tests.support import FakeRunner
from t3compose import process


def config_data():
    return {'version': 1, 'app': {'slug': 'fixture-worker', 'appId': 12345,
            'installationAccount': 'fixture-org',
            'keychain': {'service': 'fixture-service', 'account': 'fixture-account'}},
            'repository': {'owner': 'fixture-org', 'name': 'fixture-repo',
                           'permissions': {'contents': 'write', 'pull_requests': 'write'}}}


TOKEN = 'synthetic-installation-token-0123456789'
EXPIRY = '2030-01-02T03:04:05Z'
INSTALL = {'id': 67890, 'account': {'login': 'fixture-org'}, 'app_id': 12345,
           'app_slug': 'fixture-worker', 'repository_selection': 'selected'}
GRANTED = {'contents': 'write', 'pull_requests': 'write', 'metadata': 'read'}
POST = '/app/installations/67890/access_tokens'
REVOKE = '/installation/token'
REPOS = '/installation/repositories'


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value if isinstance(value, bytes) else json.dumps(value).encode())
        self.limits = []

    def read(self, n=-1):
        self.limits.append(n)
        return super().read(n)


class FakeHTTP:
    def __init__(self):
        self.pages = [[copy.deepcopy(INSTALL)]]
        self.token = {'token': TOKEN, 'expires_at': EXPIRY, 'permissions': dict(GRANTED)}
        self.repos = {'total_count': 1, 'repositories': [{'full_name': 'fixture-org/fixture-repo'}]}
        self.errors = {}
        self.calls = []
        self.responses = []

    def __call__(self, request, timeout):
        path = urllib.parse.urlsplit(request.full_url).path
        method = request.get_method()
        self.calls.append((method, path, request, timeout))
        error = self.errors.get((method, path))
        if callable(error):
            error = error(request)
        if isinstance(error, BaseException):
            raise error
        if error is not None:
            code, body = error
            raise urllib.error.HTTPError(request.full_url, code, 'untrusted reason',
                                         {'X-Secret': TOKEN}, Response(body))
        if path == '/app/installations':
            page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)['page'][0])
            value = self.pages[page - 1]
        elif method == 'POST' and path == POST:
            value = self.token
        elif path == REPOS:
            value = self.repos
        elif method == 'DELETE' and path == REVOKE:
            value = b''
        else:
            raise AssertionError('unexpected HTTP request')
        response = Response(value)
        self.responses.append(response)
        return response


class OfflineAppBase(OfflineTestCase):
    def setUp(self):
        self.ga = importlib.import_module('t3compose.github_app')
        self.local = self.temp / 'synthetic-state'
        self.local.mkdir()
        self.path = self.local / 'github-app.json'
        self.write_config(config_data())
        self.http = FakeHTTP()

    def write_config(self, value):
        self.path.write_text(json.dumps(value))
        self.path.chmod(0o600)

    def key(self):
        self.allow_program('/usr/bin/openssl')
        path = self.temp / 'throwaway.pem'
        process.run(['/usr/bin/openssl', 'genrsa', '-out', str(path), '2048'],
                       check=True, capture_output=True, timeout=30)
        return path.read_bytes()

    def invoke(self, http=None, raw=None, real_sign=False, text=False, extra=()):
        http = http or self.http
        # A syntactically valid placeholder suffices where signing is faked;
        # integration/signature tests generate a fresh RSA key instead.
        raw = raw if raw is not None else b'-----BEGIN PRIVATE KEY-----\nYWJjZA==\n-----END PRIVATE KEY-----\n'
        real_run = process.run
        def sign(argv, **kw):
            if real_sign:
                return real_run(argv, **kw)
            with os.fdopen(os.dup(kw['pass_fds'][0]), 'rb') as pipe:
                self.transported_key = pipe.read()
            return subprocess.CompletedProcess(argv, 0, b'synthetic-signature', b'')
        fake = FakeRunner({'security': lambda argv, **kw: subprocess.CompletedProcess(argv, 0, raw, b''),
                           'openssl': sign})
        rt = self.ga.Runtime(http=http, clock=lambda: 1900000000)
        out, err = io.StringIO(), io.StringIO()
        argv = ['t3c', '--local-dir', str(self.local), *(['--text'] if text else []),
                'publish', 'check', *extra]
        with patch.object(sys, 'argv', argv), patch.object(process, 'run', fake), \
                patch.object(self.ga, 'default_runtime', return_value=rt), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
        self.runner = fake
        self.output = out.getvalue() + err.getvalue()
        return int(stopped.exception.code), out.getvalue(), err.getvalue()

    def checked(self, expected=1, **kw):
        code, out, err = self.invoke(**kw)
        self.assertEqual(code, expected, out + err)
        if expected == 2:
            self.assertIn('t3c publish check: command failed (', err)
            self.assertNotIn('Traceback', err)
        else:
            self.assertEqual(err, '')
        result = json.loads(out)
        self.assertEqual(result['ok'], expected == 0)
        self.assertNotIn(TOKEN, self.output)
        return result

    def assert_revoked(self, result, status='ok'):
        self.assertEqual(result['revocation']['status'], status)
        self.assertEqual([(m, p) for m, p, _, _ in self.http.calls].count(('DELETE', REVOKE)), 1)


class OfflineConfig(OfflineAppBase):
    def test_valid_config_and_identity(self):
        cfg = self.ga.load_config(self.path)
        self.assertEqual((cfg.app_id, cfg.full_name, cfg.bot_login),
                         (12345, 'fixture-org/fixture-repo', 'fixture-worker[bot]'))
        data = config_data()
        data['repository']['owner'] = 'FIXTURE-ORG'
        data['repository']['permissions'] = {'contents': 'read'}
        self.write_config(data)
        self.assertEqual(self.ga.load_config(self.path).permissions, {'contents': 'read'})

    def test_missing_unknown_and_wrong_type_fields_refuse_before_keychain(self):
        paths = [('version',), ('app',), ('repository',), ('app', 'slug'), ('app', 'appId'),
                 ('app', 'installationAccount'), ('app', 'keychain'),
                 ('app', 'keychain', 'service'), ('app', 'keychain', 'account'),
                 ('repository', 'owner'), ('repository', 'name'), ('repository', 'permissions')]
        for path in paths:
            for mode in ('missing', 'type', 'unknown'):
                with self.subTest(path=path, mode=mode):
                    data = config_data()
                    parent = data
                    for part in path[:-1]:
                        parent = parent[part]
                    if mode == 'missing':
                        del parent[path[-1]]
                    elif mode == 'unknown':
                        parent['secret-unknown-field'] = 'secret-value'
                    else:
                        parent[path[-1]] = []
                    self.write_config(data)
                    code, out, err = self.invoke()
                    self.assertEqual((code, out), (3, ''))
                    self.assertEqual(self.runner.calls, [])
                    self.assertEqual(self.http.calls, [])
                    self.assertNotIn('secret-', err)

    def test_invalid_values_and_permission_scope(self):
        cases = [(('version',), True), (('version',), 2), (('app', 'appId'), True),
                 (('app', 'appId'), 0), (('app', 'appId'), 1.0),
                 (('app', 'slug'), 'Upper'), (('app', 'installationAccount'), '-bad'),
                 (('app', 'keychain', 'service'), '-w'), (('app', 'keychain', 'account'), ''),
                 (('repository', 'owner'), 'other'), (('repository', 'name'), '..'),
                 (('repository', 'name'), '../outside'), (('repository', 'permissions'), {})]
        cases += [(('repository', 'permissions'), {p: 'write'})
                  for p in ('metadata', 'issues', 'workflows', 'actions', 'secret-field')]
        cases += [(('repository', 'permissions'), {'contents': p}) for p in (None, 1, 'admin')]
        for path, value in cases:
            data = config_data()
            parent = data
            for part in path[:-1]:
                parent = parent[part]
            parent[path[-1]] = value
            self.write_config(data)
            self.assertEqual(self.invoke()[0], 3)
            self.assertEqual(self.runner.calls, [])

    def test_file_and_json_refusals_do_not_echo_contents_or_paths(self):
        for raw in (b'{"secret-field":1,"secret-field":2}', b'{"version":NaN}', b'\xff',
                    b'{', b'[]', b' ' * (65536 + 1)):
            self.path.write_bytes(raw)
            code, out, err = self.invoke()
            self.assertEqual((code, out), (3, ''))
            self.assertNotIn('secret-field', err)
            self.assertNotIn(str(self.local), err)
        for mode in (0o620, 0o602, 0o666):
            self.write_config(config_data())
            self.path.chmod(mode)
            self.assertEqual(self.invoke()[0], 3)
        self.path.unlink()
        self.assertEqual(self.invoke()[0], 3)
        self.path.mkdir()
        self.assertEqual(self.invoke()[0], 3)
        self.path.rmdir()
        os.mkfifo(self.path)
        self.assertEqual(self.invoke()[0], 3)

    def test_foreign_owner_refuses(self):
        real_stat = os.fstat
        def foreign(fd):
            info = real_stat(fd)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=os.getuid() + 1)
        with patch.object(self.ga.os, 'fstat', side_effect=foreign):
            self.assertEqual(self.invoke()[0], 3)
        self.assertEqual(self.runner.calls, [])

    def test_import_and_register_are_side_effect_free(self):
        from t3compose import cli
        with patch.object(self.ga, 'load_config', side_effect=AssertionError), \
                patch.object(process, 'run', side_effect=AssertionError), \
                patch.object(os, 'open', side_effect=AssertionError):
            importlib.reload(self.ga)
            self.assertIn(('publish', 'check'), cli.command_names())


class OfflineCheck(OfflineAppBase):
    def test_real_entry_scoped_request_synthetic_signature_and_json_facts(self):
        key = self.key()
        result = self.checked(0, raw=key.hex().encode() + b'\n', real_sign=True)
        self.assertEqual(result, {'ok': True, 'app_slug': 'fixture-worker',
            'installation_id': 67890, 'installation_account': 'fixture-org',
            'installation_repository_selection': 'selected', 'permissions': GRANTED,
            'expires_at': EXPIRY, 'repositories': ['fixture-org/fixture-repo'],
            'total_count': 1, 'reasons': [], 'revocation': {'status': 'ok', 'expires_at': EXPIRY}})
        self.assertEqual([(m, p) for m, p, _, _ in self.http.calls],
                         [('GET', '/app/installations'), ('POST', POST), ('GET', REPOS), ('DELETE', REVOKE)])
        post = self.http.calls[1][2]
        self.assertEqual(json.loads(post.data), {'repositories': ['fixture-repo'],
                         'permissions': {'contents': 'write', 'pull_requests': 'write'}})
        jwt = post.get_header('Authorization').removeprefix('Bearer ')
        header, claims, signature = jwt.split('.')
        decode = lambda part: base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))
        self.assertEqual(json.loads(decode(header)), {'alg': 'RS256', 'typ': 'JWT'})
        self.assertEqual(json.loads(decode(claims)), {'iat': 1899999940, 'exp': 1900000540, 'iss': '12345'})
        public = self.temp / 'public.pem'
        process.run(['/usr/bin/openssl', 'rsa', '-in', str(self.temp / 'throwaway.pem'),
                        '-pubout', '-out', str(public)], check=True, capture_output=True)
        sig = self.temp / 'signature'
        sig.write_bytes(decode(signature))
        verified = process.run(['/usr/bin/openssl', 'dgst', '-sha256', '-verify', str(public),
                                  '-signature', str(sig)], input=(header + '.' + claims).encode(),
                                  capture_output=True, check=True)
        self.assertIn(b'Verified OK', verified.stdout)
        altered = process.run(['/usr/bin/openssl', 'dgst', '-sha256', '-verify', str(public),
                               '-signature', str(sig)], input=(header + '.' + claims + 'x').encode(),
                              capture_output=True, check=False)
        self.assertNotEqual(altered.returncode, 0)
        for _, _, request, timeout in self.http.calls:
            self.assertTrue(request.full_url.startswith('https://api.github.com/'))
            self.assertEqual(timeout, 30)
            self.assertEqual(request.get_header('X-github-api-version'), '2022-11-28')
        for secret in (key.decode(), key.hex(), jwt, signature, TOKEN):
            self.assertNotIn(secret, self.output)
        self.assertEqual(self.http.calls[-1][2].get_header('Authorization'), 'Bearer ' + TOKEN)
        self.assertEqual(self.runner.calls[0][0], ['/usr/bin/security', 'find-generic-password',
                         '-s', 'fixture-service', '-a', 'fixture-account', '-w'])
        self.http = FakeHTTP()
        self.assertEqual(self.checked(0, raw=key, real_sign=True), result)

    def test_text_success_and_bad_arguments(self):
        code, out, err = self.invoke(text=True)
        self.assertEqual((code, err), (0, ''))
        self.assertIn('revocation:', out)
        code, out, err = self.invoke(extra=('--unknown-secret',))
        self.assertEqual((code, out), (2, ''))
        self.assertNotIn('unknown-secret', err)
        self.assertEqual(self.runner.calls, [])

    def test_installation_uniqueness_and_malformed_responses(self):
        for batch in ([], [INSTALL, INSTALL], {}, [None, {}, {'account': {}}],
                      [dict(INSTALL, account={'login': 'FIXTURE-ORG'})]):
            self.http = FakeHTTP()
            self.http.pages = [batch]
            result = self.checked()
            self.assertEqual(result['revocation']['status'], 'not_needed')
            self.assertFalse(any(m == 'POST' for m, _, _, _ in self.http.calls))

    def test_pagination_and_page_limit(self):
        self.http.pages = [[{'account': {'login': 'other'}}] * 100, [INSTALL]]
        self.checked(0)
        self.assertIn('page=2', self.http.calls[1][2].full_url)
        self.http = FakeHTTP()
        self.http.pages = [[{}] * 100] * 20
        self.checked()
        self.assertEqual(len(self.http.calls), 20)

    def test_invalid_installation_id_and_app_mismatch(self):
        for field, value in (('id', None), ('id', True), ('id', 0), ('app_id', 99),
                             ('app_id', '12345'), ('app_slug', 'other')):
            self.http = FakeHTTP()
            self.http.pages = [[dict(INSTALL, **{field: value})]]
            self.checked()
            self.assertEqual(len(self.http.calls), 1)

    def test_repository_scope_and_malformed_lists_always_revoke(self):
        cases = [None, [], {}, {'total_count': True, 'repositories': []},
                 {'total_count': '1', 'repositories': []},
                 {'total_count': 1, 'repositories': [{}]},
                 {'total_count': 1, 'repositories': [None]},
                 {'total_count': 1, 'repositories': [{'full_name': 3}]},
                 {'total_count': 0, 'repositories': []},
                 {'total_count': 1, 'repositories': [{'full_name': 'fixture-org/other'}]},
                 {'total_count': 1, 'repositories': [{'full_name': 'FIXTURE-ORG/fixture-repo'}]},
                 {'total_count': 2, 'repositories': [{'full_name': 'fixture-org/fixture-repo'}]},
                 {'total_count': 1, 'repositories': [{'full_name': 'fixture-org/fixture-repo'}] * 2}]
        for repos in cases:
            self.http = FakeHTTP()
            self.http.repos = repos
            self.assert_revoked(self.checked())

    def test_exact_permissions_neither_broader_nor_narrower(self):
        for granted in (None, [], {}, dict(GRANTED, issues='read'), dict(GRANTED, metadata='write'),
                        dict(GRANTED, contents='read'), {'contents': 'write', 'pull_requests': 'write'},
                        dict(GRANTED, contents=True)):
            self.http = FakeHTTP()
            self.http.token['permissions'] = granted
            self.assert_revoked(self.checked())
        data = config_data()
        data['repository']['permissions'] = {'contents': 'read'}
        self.write_config(data)
        self.http = FakeHTTP()
        self.assert_revoked(self.checked())
        self.http.token['permissions'] = {'contents': 'read', 'metadata': 'read'}
        self.checked(0)

    def test_malformed_token_and_missing_expiry(self):
        for token in (None, '', 'short', 'synthetic token with spaces', 'synthetic\nnewline', 5):
            self.http = FakeHTTP()
            self.http.token['token'] = token
            result = self.checked()
            self.assertIn('no usable token', ' '.join(result['reasons']))
            if isinstance(token, str) and token:
                self.assertNotIn(token, self.output)
                self.assert_revoked(result)
        for expiry in (None, '', 3):
            self.http = FakeHTTP()
            self.http.token['expires_at'] = expiry
            result = self.checked()
            self.assert_revoked(result)
            self.assertIn('expires_at', ' '.join(result['reasons']))
            self.assertEqual(result['revocation']['expires_at'], 'unknown')

    def test_revocation_failure_is_failure_and_expiry_survives(self):
        for expiry in (EXPIRY, None):
            self.http = FakeHTTP()
            self.http.token['expires_at'] = expiry
            self.http.errors[('DELETE', REVOKE)] = (500, {'message': 'temporary failure'})
            result = self.checked()
            self.assert_revoked(result, 'failed')
            self.assertEqual(result['revocation']['expires_at'], expiry or 'unknown')
            self.assertIn('expires_at', result['reasons'][0])

    def test_listing_failure_interrupt_and_unexpected_error_cleanup(self):
        for error, code in ((urllib.error.URLError(TOKEN), 1), (KeyboardInterrupt(TOKEN), 1),
                            (RuntimeError(TOKEN), 2), (SystemExit(TOKEN), 2)):
            for cleanup in (None, KeyboardInterrupt(TOKEN), RuntimeError(TOKEN)):
                self.http = FakeHTTP()
                self.http.errors[('GET', REPOS)] = error
                self.http.errors[('DELETE', REVOKE)] = cleanup
                result = self.checked(2 if isinstance(cleanup, RuntimeError) else code)
                self.assert_revoked(result, 'failed' if cleanup else 'ok')
                self.assertEqual(result['revocation']['expires_at'], EXPIRY)
                if code == 2:
                    self.assertIn(type(error).__name__, ' '.join(result['reasons']))

    def test_unexpected_revocation_failure_reports_class_and_preserves_expiry(self):
        for expiry in (EXPIRY, None):
            for error in (RuntimeError(TOKEN), SystemExit(TOKEN)):
                self.http = FakeHTTP()
                self.http.token['expires_at'] = expiry
                self.http.errors[('DELETE', REVOKE)] = error
                code, out, err = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(err, 't3c publish check: command failed (' + type(error).__name__ +
                                 '); check local inputs and helper installation\n')
                result = json.loads(out)
                self.assert_revoked(result, 'failed')
                self.assertEqual(result['revocation']['expires_at'], expiry or 'unknown')
                self.assertIn('expires_at=' + (expiry or 'unknown'), result['reasons'][0])
                self.assertNotIn(TOKEN, out + err)

    def test_unexpected_listing_failure_writes_sanitized_stderr_after_cleanup(self):
        for error in (RuntimeError(TOKEN), SystemExit(TOKEN)):
            self.http = FakeHTTP()
            self.http.errors[('GET', REPOS)] = error
            code, out, err = self.invoke()
            self.assertEqual(code, 2)
            self.assertEqual(err, 't3c publish check: command failed (' + type(error).__name__ +
                             '); check local inputs and helper installation\n')
            self.assert_revoked(json.loads(out))
            self.assertNotIn(TOKEN, out + err)

    def test_http_errors_message_only_and_422_guidance(self):
        for status in (301, 302, 307, 308, 401, 403, 422, 500):
            self.http = FakeHTTP()
            self.http.errors[('POST', POST)] = (status, {'message': 'synthetic GitHub message'})
            result = self.checked()
            reasons = ' '.join(result['reasons'])
            self.assertIn('HTTP ' + str(status), reasons)
            self.assertIn('synthetic GitHub message', reasons)
            self.assertNotIn('untrusted reason', self.output)
            self.assertNotIn('X-Secret', self.output)
            if status == 422:
                self.assertIn('Shawn must add', reasons)
                self.assertIn('grant', reasons)

    def test_422_without_message_still_explains_scope_remedy(self):
        for body in ({}, {'message': None}, [], b'not-json', b'\xff'):
            self.http = FakeHTTP()
            self.http.errors[('POST', POST)] = (422, body)
            reasons = ' '.join(self.checked()['reasons'])
            self.assertIn('HTTP 422', reasons)
            self.assertIn('add the repository', reasons)
            self.assertIn('grant requested permissions', reasons)

    def test_secrets_in_http_errors_and_structured_fields_are_redacted_before_escape(self):
        key = self.key()
        for text in (False, True):
            self.http = FakeHTTP()
            self.http.token['expires_at'] = TOKEN + '\n' + key.decode()
            self.http.token['permissions'] = {TOKEN: key.decode()}
            self.http.repos = {'total_count': 1, 'repositories': [{'full_name': TOKEN}]}
            self.http.pages[0][0]['repository_selection'] = TOKEN
            code, out, err = self.invoke(raw=key, text=text)
            self.assertEqual(code, 1)
            self.assertIn('[redacted]', out)
            for secret in (TOKEN, key.decode(), *key.decode().splitlines()[1:-1]):
                self.assertNotIn(secret, out + err)
        def echo(request):
            jwt = request.get_header('Authorization').removeprefix('Bearer ')
            self.echoed_jwt = jwt
            return urllib.error.HTTPError(request.full_url, 422, TOKEN, {},
                Response({'message': 'x' * 950 + jwt + key.decode()}))
        self.http = FakeHTTP()
        self.http.errors[('POST', POST)] = echo
        self.checked(raw=key)
        self.assertNotIn(self.echoed_jwt, self.output)
        self.assertNotIn(self.echoed_jwt[:30], self.output)


class OfflineCrypto(OfflineAppBase):
    def test_thread_constructor_failure_closes_both_pipe_fds(self):
        original_pipe, fds = os.pipe, []
        def pipe():
            pair = original_pipe()
            fds.extend(pair)
            return pair
        try:
            with patch.object(self.ga.os, 'pipe', side_effect=pipe), \
                    patch.object(threading, 'Thread', side_effect=RuntimeError(TOKEN)):
                with self.assertRaises(RuntimeError):
                    self.ga.openssl_sign(b'synthetic-key', b'message')
            for fd in fds:
                with self.assertRaises(OSError):
                    os.fstat(fd)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def test_raw_hex_and_reflowed_keys(self):
        key = self.key()
        for raw in (key, key + b'\n', key.hex().encode() + b'\n',
                    key.replace(b'\n', b' '), key.replace(b'\n', b'\\n')):
            self.assertEqual(self.ga.decode_keychain_pem(raw), key)
        pkcs8 = process.run(['/usr/bin/openssl', 'pkcs8', '-topk8', '-nocrypt'],
                               input=key, capture_output=True, check=True).stdout
        self.assertTrue(self.ga.openssl_sign(pkcs8, b'synthetic message'))

    def test_invalid_keys_and_security_failures_never_echo(self):
        for raw in (b'', b'secret-key-value', b'-----BEGIN CERTIFICATE-----\nYWJjZA==\n-----END CERTIFICATE-----',
                    b'-----BEGIN PRIVATE KEY-----\n!bad\n-----END PRIVATE KEY-----'):
            result = self.checked(raw=raw)
            self.assertEqual(result['revocation']['status'], 'not_needed')
            if raw:
                self.assertNotIn(raw.decode(), self.output)
        for status in (44, 1):
            fake = FakeRunner({'security': lambda argv, **kw: subprocess.CompletedProcess(argv, status, TOKEN.encode(), TOKEN.encode())})
            with patch.object(process, 'run', fake):
                with self.assertRaises(self.ga.CheckError) as caught:
                    self.ga.read_keychain_secret('private-service', 'private-account')
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertNotIn('private-', str(caught.exception))
        for kind in ('timeout', 'not-found', 'os-error'):
            fake = FakeRunner({'security': lambda *a, **k: (_ for _ in ()).throw(process.ProcessError(kind))})
            with patch.object(process, 'run', fake), self.assertRaises(self.ga.CheckError):
                self.ga.read_keychain_secret('fixture', 'fixture')

    def test_pipe_only_transport_large_key_and_closed_fds(self):
        key = b'synthetic-large-key' * 100000
        seen = []
        def signer(argv, **kw):
            self.assertEqual(argv[:4], ['/usr/bin/openssl', 'dgst', '-sha256', '-sign'])
            fd, = kw['pass_fds']
            seen.append(fd)
            self.assertEqual(argv[4], '/dev/fd/' + str(fd))
            self.assertTrue(stat.S_ISFIFO(os.fstat(fd).st_mode))
            self.assertFalse(os.get_inheritable(fd))
            self.assertEqual(kw['input'], b'sign-me')
            self.assertNotIn('env', kw)
            self.assertEqual(kw['timeout'], 30)
            with os.fdopen(os.dup(fd), 'rb') as pipe:
                self.assertEqual(pipe.read(), key)
            return subprocess.CompletedProcess(argv, 0, b'signed', b'')
        with patch.object(process, 'run', FakeRunner({'openssl': signer})):
            self.assertEqual(self.ga.openssl_sign(key, b'sign-me'), b'signed')
        for fd in seen:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_launch_timeout_and_thread_start_failures_close_both_fds(self):
        original_pipe = os.pipe
        for failure in ('not-found', 'timeout', 'os-error', 'thread'):
            fds = []
            def pipe():
                pair = original_pipe()
                fds.extend(pair)
                return pair
            def fail(*a, **k):
                raise process.ProcessError(failure if failure != 'thread' else 'os-error')
            with patch.object(self.ga.os, 'pipe', side_effect=pipe), \
                    patch.object(process, 'run', FakeRunner({'openssl': fail})), contextlib.ExitStack() as stack:
                if failure == 'thread':
                    stack.enter_context(patch.object(threading.Thread, 'start', side_effect=RuntimeError(TOKEN)))
                with self.assertRaises((self.ga.CheckError, RuntimeError)):
                    self.ga.openssl_sign(b'x' * 2000000, b'message')
            for fd in fds:
                with self.assertRaises(OSError):
                    os.fstat(fd)
        self.assertFalse(any(t.name == 't3c-key-feeder' for t in threading.enumerate()))

    def test_real_openssl_timeout_closes_pipe_and_joins_feeder(self):
        key = self.key() + b'\n' * 2000000
        original_pipe, real_run, fds, failures = os.pipe, process.run, [], []
        def pipe():
            pair = original_pipe()
            if not fds:
                fds.extend(pair)
            return pair
        def run(argv, **kw):
            try:
                return real_run(argv, **kw)
            except process.ProcessError as error:
                failures.append(error.kind)
                raise
        # Zero makes the real child communication deadline deterministic; no
        # fake process or timer supplies the timeout exception.
        with patch.object(self.ga, 'OPENSSL_TIMEOUT', 0), \
                patch.object(self.ga.os, 'pipe', side_effect=pipe), \
                patch.object(process, 'run', side_effect=run):
            with self.assertRaises(self.ga.CheckError):
                self.ga.openssl_sign(key, b'synthetic message')
        self.assertEqual(failures, ['timeout'])
        for fd in fds:
            with self.assertRaises(OSError):
                os.fstat(fd)
        self.assertFalse(any(t.name == 't3c-key-feeder' for t in threading.enumerate()))

    def test_sign_failure_drops_stderr_and_repr_never_exposes_secrets(self):
        fake = FakeRunner({'openssl': lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b'', TOKEN.encode())})
        with patch.object(process, 'run', fake):
            with self.assertRaises(self.ga.CheckError) as caught:
                self.ga.openssl_sign(b'synthetic-key', b'message')
        self.assertNotIn(TOKEN, ''.join(traceback.format_exception(caught.exception)))
        secrets = self.ga.Secrets()
        secrets.add(TOKEN)
        self.assertNotIn(TOKEN, repr(secrets))


class OfflineMalformedMint(OfflineAppBase):
    def test_parse_refusals_revoke_observed_tokens(self):
        token = json.dumps(TOKEN)
        cases = [('"expires_at":' + bad, 'unknown') for bad in ('1e999', 'NaN', 'Infinity', '9' * 4301)]
        cases += [('"expires_at":"first","expires_at":"second"', 'unknown'),
                  ('"expires_at":' + json.dumps(EXPIRY) + ',"other":0,"other":1', EXPIRY),
                  ('"expires_at":' + json.dumps(EXPIRY) + ',"token":' + token, EXPIRY)]
        for suffix, expiry in cases:
            for fail in (False, True):
                self.http = FakeHTTP()
                self.http.token = ('{"token":' + token + ',' + suffix + '}').encode()
                if fail:
                    self.http.errors[('DELETE', REVOKE)] = (500, {'message': 'refused'})
                result = self.checked()
                self.assert_revoked(result, 'failed' if fail else 'ok')
                self.assertEqual(result['revocation']['expires_at'], expiry)
        other = TOKEN + '-other'
        self.http = FakeHTTP()
        self.http.token = ('{"token":' + token + ',"token":' + json.dumps(other) + '}').encode()
        result = self.checked()
        self.assertEqual(result['revocation']['status'], 'ok')
        self.assertEqual([r.get_header('Authorization') for m, p, r, _ in self.http.calls if m == 'DELETE'],
                         ['Bearer ' + TOKEN, 'Bearer ' + other])
        self.assertNotIn(other, self.output)

    def test_failed_revocation_evidence_survives_unsafe_facts(self):
        for field in ('expiry', 'permission', 'depth'):
            for text in (False, True):
                self.http = FakeHTTP()
                if field == 'expiry':
                    self.http.token['expires_at'] = '\ud800'
                elif field == 'permission':
                    self.http.token['permissions'] = {'contents': '\ud800' + TOKEN}
                else:
                    self.http.token = ('{"token":' + json.dumps(TOKEN) + ',"expires_at":' + json.dumps(EXPIRY) +
                        ',"permissions":{"contents":' + '[' * 1200 + '0' + ']' * 1200 + '}}').encode()
                self.http.errors[('DELETE', REVOKE)] = (500, {'message': 'refused\ud800' + TOKEN})
                code, out, err = self.invoke(text=text)
                self.assertEqual((code, err), (1, ''))
                if text:
                    self.assertIn('"status":"failed"', out)
                    self.assertIn('unknown' if field == 'expiry' else EXPIRY, out)
                else:
                    result = json.loads(out)
                    self.assertEqual(result['revocation'], {'status': 'failed',
                        'expires_at': 'unknown' if field == 'expiry' else EXPIRY})
                self.assertEqual(sum(m == 'DELETE' for m, _, _, _ in self.http.calls), 1)
                self.assertNotIn(TOKEN, self.output)


class OfflineHTTP(OfflineAppBase):
    def test_malformed_json_and_nonfinite_numbers_are_refused(self):
        for body in (b'{"duplicate":1,"duplicate":2}', b'{"value":NaN}',
                     b'{"value":Infinity}', b'{"value":-Infinity}', b'{"value":1e999}',
                     b'{"value":-1e999}', b'{"value":'):
            with self.subTest(body=body[:40]):
                api = self.ga.GitHubAPI(lambda *a, **k: Response(body), self.ga.Secrets())
                with self.assertRaises(self.ga.CheckError):
                    api.request('GET', REPOS, TOKEN)

    def test_redirect_handler_and_verified_tls(self):
        opener = self.ga.make_opener()
        redirect = next(h for h in opener.handlers if isinstance(h, self.ga.RejectRedirects))
        request = urllib.request.Request('https://api.github.com/app/installations')
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(redirect.redirect_request(request, None, code, '', {}, 'https://example.invalid/'))
        https = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler))
        import ssl
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)

    def test_bounded_body_invalid_json_empty_success_and_network_errors(self):
        secrets = self.ga.Secrets()
        secrets.add(TOKEN)
        for value in (b'x' * (8 * 1024 * 1024 + 1), b'not-json', b'\xff'):
            response = Response(value)
            api = self.ga.GitHubAPI(lambda *a, **k: response, secrets)
            with self.assertRaises(self.ga.CheckError):
                api.request('GET', '/app/installations', TOKEN)
            self.assertEqual(response.limits, [8 * 1024 * 1024 + 1])
        api = self.ga.GitHubAPI(lambda *a, **k: Response(b''), secrets)
        self.assertIsNone(api.request('DELETE', REVOKE, TOKEN))
        for error in (urllib.error.URLError(TOKEN), OSError(TOKEN)):
            def fail(*a, **k):
                raise error
            with self.assertRaises(self.ga.CheckError) as caught:
                self.ga.GitHubAPI(fail, secrets).request('GET', '/app/installations', TOKEN)
            self.assertNotIn(TOKEN, ''.join(traceback.format_exception(caught.exception)))
        response = Response({'message': TOKEN})
        def http_error(*a, **k):
            raise urllib.error.HTTPError('https://api.github.com/', 500, TOKEN, {}, response)
        with self.assertRaises(self.ga.CheckError) as caught:
            self.ga.GitHubAPI(http_error, secrets).request('GET', '/app/installations', TOKEN)
        self.assertEqual(response.limits, [65536])
        self.assertNotIn(TOKEN, repr(caught.exception))
        self.assertTrue(response.closed)
