"""GitHub App authentication and scoped check; no configuration read on import."""
import base64
from dataclasses import dataclass
import http.client
import json
import math
import os
import re
import ssl
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import process
from .cli import CommandError, ExitCode, Outcome

API_BASE = 'https://api.github.com'
HTTP_TIMEOUT = 30
KEYCHAIN_TIMEOUT = 120
OPENSSL_TIMEOUT = 30
MAX_CONFIG_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
PER_PAGE = 100
MAX_PAGES = 20
_LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?')
_NAME = re.compile(r'[A-Za-z0-9._-]{1,100}')
_SLUG = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])?')
_KEYCHAIN = re.compile(r'(?!-)[\x21-\x7e][\x20-\x7e]{0,254}')
_TOKEN = re.compile(r'[\x21-\x7e]{16,4096}')
_HEX = re.compile(rb'(?:[0-9a-fA-F]{2})+')
_PEM = re.compile(rb'-----BEGIN ([A-Z0-9 ]{1,40})-----(.*?)-----END \1-----', re.DOTALL)
_BASE64 = re.compile(rb'[A-Za-z0-9+/]+={0,2}')


class ConfigError(CommandError):
    def __init__(self, message):
        super().__init__('App config: ' + message, ExitCode.INVALID_CONFIG)


class CheckError(Exception):
    """Expected operational failure; messages must already be public."""


class GitHubError(CheckError):
    def __init__(self, method, path, status, message):
        self.status = status
        super().__init__(f'GitHub API {method} {path} failed: HTTP {status}' +
                         (': ' + message if message else ''))


class Secrets:
    """Redact before any escaping/truncation, including nested JSON object keys."""
    def __init__(self):
        self._values = set()

    def add(self, value):
        if isinstance(value, bytes):
            value = value.decode('utf-8', 'replace')
        if isinstance(value, str):
            self._values.update(v for v in (value, value.strip()) if v)

    def add_key(self, pem):
        text = pem.decode('ascii', 'replace')
        self.add(text)
        lines = [line.strip() for line in text.splitlines() if not line.startswith('-----')]
        self.add(''.join(lines))
        for line in lines:
            self.add(line)

    def redact(self, text):
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, '[redacted]')
        return text

    def diagnostic(self, text):
        text = ' '.join(self.redact(text).split())
        text = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', text)
        return text if len(text) <= 1000 else text[:997] + '...'

    def public(self, data):
        if isinstance(data, str):
            return self.redact(data)
        if isinstance(data, dict):
            return {self.redact(k): self.public(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self.public(v) for v in data]
        return data


@dataclass(frozen=True, repr=False)
class Config:
    slug: str
    app_id: int
    installation_account: str
    keychain_service: str
    keychain_account: str
    owner: str
    name: str
    permissions: dict

    @property
    def full_name(self):
        return f'{self.owner}/{self.name}'

    @property
    def bot_login(self):
        return self.slug + '[bot]'


def _object(value, where, keys=None):
    if not isinstance(value, dict):
        raise ConfigError(where + ' must be an object')
    if keys is not None and set(value) != set(keys):
        raise ConfigError(where + ' has missing or unknown fields')
    return value


def _text(value, where, pattern):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ConfigError(where + ' is not a valid string')
    return value


def parse_config(data):
    top = _object(data, 'config', ('version', 'app', 'repository'))
    if type(top['version']) is not int or top['version'] != 1:
        raise ConfigError('version must be the integer 1')
    app = _object(top['app'], 'app', ('slug', 'appId', 'installationAccount', 'keychain'))
    keychain = _object(app['keychain'], 'app.keychain', ('service', 'account'))
    repo = _object(top['repository'], 'repository', ('owner', 'name', 'permissions'))
    permissions = _object(repo['permissions'], 'repository.permissions')
    if not permissions or any(k not in ('contents', 'pull_requests') for k in permissions):
        raise ConfigError('permissions must name contents and/or pull_requests only')
    if any(not isinstance(v, str) or v not in ('read', 'write') for v in permissions.values()):
        raise ConfigError('permission levels must be read or write')
    if type(app['appId']) is not int or app['appId'] <= 0:
        raise ConfigError('app.appId must be a positive integer')
    account = _text(app['installationAccount'], 'app.installationAccount', _LOGIN)
    owner = _text(repo['owner'], 'repository.owner', _LOGIN)
    name = _text(repo['name'], 'repository.name', _NAME)
    if name in ('.', '..'):
        raise ConfigError('repository.name is invalid')
    if owner.lower() != account.lower():
        raise ConfigError('repository.owner must equal app.installationAccount ignoring case')
    return Config(_text(app['slug'], 'app.slug', _SLUG), app['appId'], account,
                  _text(keychain['service'], 'app.keychain.service', _KEYCHAIN),
                  _text(keychain['account'], 'app.keychain.account', _KEYCHAIN),
                  owner, name, dict(permissions))


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _no_constant(value):
    raise ValueError('non-finite JSON number')


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('non-finite JSON number')
    return result


def load_config(path):
    """Strict v1 loader; no path, key name or supplied value enters diagnostics."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ConfigError('not a regular file')
            if info.st_uid != os.getuid():
                raise ConfigError('not owned by the current user')
            if info.st_mode & 0o022:
                raise ConfigError('writable by group or others; restrict file permissions')
            raw = b''
            while len(raw) <= MAX_CONFIG_BYTES:
                chunk = os.read(fd, MAX_CONFIG_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(fd)
    except OSError:
        raise ConfigError('cannot read file') from None
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError('file exceeds 65536 bytes')
    try:
        data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_keys,
                          parse_constant=_no_constant, parse_float=_finite_float)
    except (ValueError, RecursionError):
        raise ConfigError('invalid JSON') from None
    return parse_config(data)


def read_keychain_secret(service, account):
    try:
        result = process.run(['/usr/bin/security', 'find-generic-password', '-s', service,
                              '-a', account, '-w'], stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=KEYCHAIN_TIMEOUT, check=False)
    except process.ProcessError:
        raise CheckError('cannot read App key from Keychain (launch failure or timeout)') from None
    if result.returncode == 44:
        raise CheckError('Keychain item missing; Shawn must store the App private key')
    if result.returncode:
        raise CheckError(f'cannot read Keychain item: security exited with status {result.returncode}')
    return result.stdout


def decode_keychain_pem(raw):
    value = raw[:-1] if raw.endswith(b'\n') else raw
    if not value.strip():
        raise CheckError('the Keychain item is empty')
    if _HEX.fullmatch(value):
        value = bytes.fromhex(value.decode('ascii'))
    match = _PEM.search(value)
    if match is None or match[1] not in (b'RSA PRIVATE KEY', b'PRIVATE KEY'):
        raise CheckError('Keychain item must hold an unencrypted RSA PRIVATE KEY or PRIVATE KEY PEM')
    label = match[1]
    body = re.sub(rb'\s+', b'', match[2].replace(b'\\n', b'').replace(b'\\r', b''))
    if not _BASE64.fullmatch(body) or len(body) % 4:
        raise CheckError('the PEM private key is encrypted or malformed')
    lines = b'\n'.join(body[i:i + 64] for i in range(0, len(body), 64))
    return b'-----BEGIN ' + label + b'-----\n' + lines + b'\n-----END ' + label + b'-----\n'


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def build_jwt(app_id, now, sign):
    header = {'alg': 'RS256', 'typ': 'JWT'}
    claims = {'iat': int(now) - 60, 'exp': int(now) + 540, 'iss': str(app_id)}
    encode = lambda value: b64url(json.dumps(value, separators=(',', ':')).encode())
    signing_input = encode(header) + '.' + encode(claims)
    return signing_input + '.' + b64url(sign(signing_input.encode('ascii')))


def openssl_sign(key_pem, data):
    """Feed only the key pipe to the child; stdin carries the JWT signing input."""
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        raise CheckError('cannot create the signing pipe') from None
    written = []

    def feed():
        try:
            view = memoryview(key_pem)
            while view:
                view = view[os.write(write_fd, view):]
            written.append(True)
        except OSError:
            pass  # A child that exits without consuming the key breaks its pipe.
        finally:
            os.close(write_fd)

    started = False
    try:
        feeder = threading.Thread(target=feed, name='t3c-key-feeder', daemon=True)
        feeder.start()
        started = True
        result = process.run(['/usr/bin/openssl', 'dgst', '-sha256', '-sign', f'/dev/fd/{read_fd}'],
                             input=data, pass_fds=(read_fd,), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=OPENSSL_TIMEOUT, check=False)
    except process.ProcessError:
        raise CheckError('openssl could not sign the JWT (launch failure or timeout)') from None
    finally:
        # run waits/reaps even on timeout. Closing our reader unblocks a large
        # write if launch failed or the child did not consume the whole key.
        os.close(read_fd)
        if started:
            feeder.join()
        else:
            os.close(write_fd)
    if result.returncode or not result.stdout or not written:
        raise CheckError('openssl could not sign the JWT; the key may not be valid RSA')
    return result.stdout


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), RejectRedirects())


class GitHubAPI:
    """Fixed verified HTTPS origin, no redirects, bounded responses and timeouts."""
    def __init__(self, http, secrets):
        self._http = http
        self.secrets = secrets

    def request(self, method, path, bearer, *, query=None, body=None):
        url = API_BASE + path + ('?' + urllib.parse.urlencode(query) if query else '')
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 't3-compose/1', 'Authorization': 'Bearer ' + bearer}
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._http(request, timeout=HTTP_TIMEOUT) as response:
                payload = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            try:
                try:
                    value = json.loads(error.read(MAX_ERROR_BYTES).decode('utf-8'))
                    message = value.get('message') if isinstance(value, dict) else None
                except Exception:
                    message = None
                message = self.secrets.diagnostic(message) if isinstance(message, str) else ''
                status = error.code
            finally:
                error.close()
            raise GitHubError(method, path, status, message) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError):
            raise CheckError(f'cannot reach GitHub for {method} {path}') from None
        if len(payload) > MAX_RESPONSE_BYTES:
            raise CheckError(f'GitHub response to {method} {path} exceeds the body limit')
        if not payload:
            return None
        try:
            return json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_keys,
                              parse_constant=_no_constant, parse_float=_finite_float)
        except (ValueError, RecursionError):
            raise CheckError(f'GitHub response to {method} {path} is not valid JSON') from None


@dataclass(repr=False)
class Runtime:
    http: object
    clock: object = time.time


def default_runtime():
    return Runtime(make_opener().open)


def list_installations(api, jwt):
    installations = []
    for page in range(1, MAX_PAGES + 1):
        batch = api.request('GET', '/app/installations', jwt, query={'per_page': PER_PAGE, 'page': page})
        if not isinstance(batch, list):
            raise CheckError('GitHub response to GET /app/installations is not a list')
        installations.extend(batch)
        if len(batch) < PER_PAGE:
            return installations
    raise CheckError('App installation pagination limit reached; refusing to guess')


def select_installation(installations, account):
    matches = [item for item in installations if isinstance(item, dict)
               and isinstance(item.get('account'), dict) and item['account'].get('login') == account]
    if len(matches) != 1:
        raise CheckError('expected exactly one installation on the configured account; '
                         'Shawn must check the App installation')
    return matches[0]


def list_token_repositories(api, token):
    data = api.request('GET', '/installation/repositories', token, query={'per_page': PER_PAGE})
    repos = data.get('repositories') if isinstance(data, dict) else None
    total = data.get('total_count') if isinstance(data, dict) else None
    if not isinstance(repos, list) or type(total) is not int:
        raise CheckError('GitHub response to GET /installation/repositories is malformed')
    names = [r.get('full_name') if isinstance(r, dict) else None for r in repos]
    if not all(isinstance(name, str) for name in names):
        raise CheckError('GET /installation/repositories has a repository without full_name')
    return total, names


def _describe(error):
    if isinstance(error, CheckError):
        return str(error), ExitCode.CHECK_FAILED
    if isinstance(error, KeyboardInterrupt):
        return 'check interrupted', ExitCode.CHECK_FAILED
    return (f'command failed ({type(error).__name__}); check local inputs and helper installation',
            ExitCode.INVALID_INPUT)


@dataclass(repr=False)
class TokenLease:
    """Own cleanup around mint_token AND its consumer, including failed minting.

    Use ``with TokenLease(api) as lease: mint_token(config, rt, secrets, lease)``.
    Inspect status/reason after the block; cleanup never hides the primary error.
    A lease is single-use. Its token and response facts must never be logged raw.
    """
    api: GitHubAPI
    token: str | None = None
    expires_at: str = 'unknown'
    status: str = 'not_needed'
    reason: str | None = None
    unexpected: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.token:
            self.status = 'failed'
            try:
                self.api.request('DELETE', '/installation/token', self.token)
            except (Exception, KeyboardInterrupt, SystemExit) as error:
                detail, code = _describe(error)
                if code == ExitCode.INVALID_INPUT:
                    self.unexpected = detail
                self.reason = self.api.secrets.diagnostic(
                    f'could not revoke token; expires_at={self.expires_at}: {detail}')
            else:
                self.status = 'ok'
        return False


@dataclass(frozen=True, repr=False)
class Minted:
    installation_id: int
    installation_selection: str
    permissions: dict


def mint_token(config, rt, secrets, lease):
    """Mint only inside the caller's TokenLease so all post-response exits revoke."""
    raw = read_keychain_secret(config.keychain_service, config.keychain_account)
    secrets.add(raw)
    key = decode_keychain_pem(raw)
    secrets.add_key(key)
    jwt = build_jwt(config.app_id, rt.clock(), lambda data: openssl_sign(key, data))
    secrets.add(jwt)
    secrets.add(jwt.rsplit('.', 1)[-1])
    installation = select_installation(list_installations(lease.api, jwt), config.installation_account)
    identifier = installation.get('id')
    if type(identifier) is not int or identifier <= 0:
        raise CheckError('GitHub returned an installation without a valid id')
    for name, expected in (('app_id', config.app_id), ('app_slug', config.slug)):
        if name in installation and (type(installation[name]) is not type(expected) or installation[name] != expected):
            raise CheckError('installation ' + name + ' does not match the config')
    try:
        data = lease.api.request('POST', f'/app/installations/{identifier}/access_tokens', jwt,
                                body={'repositories': [config.name], 'permissions': dict(config.permissions)})
    except GitHubError as error:
        if error.status == 422:
            raise CheckError('Shawn must add the repository to the installation or grant requested permissions; '
                             + str(error)) from None
        raise
    token = data.get('token') if isinstance(data, dict) else None
    # Capture the credential before validating any other response field. Even a
    # malformed nonempty string gets a revocation attempt, not an abandoned lease.
    if isinstance(token, str) and token:
        lease.token = token
        secrets.add(token)
    expiry = data.get('expires_at') if isinstance(data, dict) else None
    if isinstance(expiry, str) and expiry:
        lease.expires_at = expiry
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise CheckError('GitHub token response has no usable token')
    if not isinstance(expiry, str) or not expiry:
        raise CheckError('GitHub token response has no expires_at')
    granted = data.get('permissions')
    selection = installation.get('repository_selection')
    return Minted(identifier, selection if isinstance(selection, str) else 'unknown',
                  granted if isinstance(granted, dict) else {})


def check(args):
    config = load_config(args.local_dir / 'github-app.json')
    secrets = Secrets()
    rt = default_runtime()
    lease = TokenLease(GitHubAPI(rt.http, secrets))
    facts = {'app_slug': config.slug, 'installation_account': config.installation_account}
    reasons = []
    diagnostics = []
    code = ExitCode.SUCCESS
    try:
        with lease:
            minted = mint_token(config, rt, secrets, lease)
            facts.update(installation_id=minted.installation_id,
                         installation_repository_selection=minted.installation_selection,
                         permissions=minted.permissions, expires_at=lease.expires_at)
            total, names = list_token_repositories(lease.api, lease.token)
            facts.update(total_count=total, repositories=names)
            if total != 1 or names != [config.full_name]:
                reasons.append('token must reach exactly the configured repository')
            if minted.permissions != dict(config.permissions, metadata='read'):
                reasons.append('token permissions must exactly match config plus metadata:read')
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        reason, code = _describe(error)
        reasons.append(secrets.diagnostic(reason))
        if code == ExitCode.INVALID_INPUT:
            diagnostics.append(reason)
    if lease.reason:
        reasons.insert(0, lease.reason)
    if lease.unexpected:
        code = ExitCode.INVALID_INPUT
        diagnostics.append(lease.unexpected)
    if reasons and code == ExitCode.SUCCESS:
        code = ExitCode.CHECK_FAILED
    for diagnostic in diagnostics:
        print(secrets.diagnostic('t3c publish check: ' + diagnostic), file=sys.stderr)
    facts.update(ok=code == ExitCode.SUCCESS, reasons=reasons,
                 revocation={'status': lease.status, 'expires_at': lease.expires_at})
    return Outcome(secrets.public(facts), code)


def register(registry):
    registry.group('publish').add_parser('check', help='verify App token scope and revocation').set_defaults(handler=check)
