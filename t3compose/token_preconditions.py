"""Token-free checks of command form and effective Git configuration."""
import os
from pathlib import Path
import re
import shutil
import subprocess

from . import process

KINDS = frozenset({'shell-command', 'git-directory', 'git-config-option', 'git-option',
                   'git-config', 'git-config-overrides', 'git-extraheader',
                   'git-url-rewrite', 'git-trace-env', 'git-ssl-verify',
                   'home-unset', 'netrc-unreadable', 'netrc-github', 'netrc-default'})
_SHELLS = frozenset('sh bash rbash zsh dash ash ksh ksh93 mksh pdksh csh tcsh fish yash'.split())
_GIT_FLAGS = frozenset(('-p', '--paginate', '-P', '--no-pager', '--no-replace-objects',
                        '--no-optional-locks', '--literal-pathspecs', '--glob-pathspecs',
                        '--noglob-pathspecs', '--icase-pathspecs', '--no-lazy-fetch',
                        '--version', '-v', '--help', '-h'))
_KEY = re.compile(rb'[a-zA-Z][a-zA-Z0-9-]*(?:\.[^\x00\n]*)?\.[a-zA-Z][a-zA-Z0-9-]*')
_SSL = r'^http(\..*)?\.sslverify$'


class Refusal(Exception):
    """Only a member of the closed public vocabulary can cross this boundary."""
    def __init__(self, kind):
        if kind not in KINDS:
            raise ValueError('invalid preflight refusal kind')
        self.kind = kind
        super().__init__(kind)


def _resolved(program, env, cwd):
    if os.path.dirname(program):
        return (Path(cwd) / program).resolve()
    search = os.pathsep.join(str(Path(cwd) / part) for part in os.get_exec_path(env))
    found = shutil.which(program, path=search)
    return Path(found).resolve() if found else None


def _command_git(command, env, cwd):
    supplied = Path(command[0]).name
    if supplied in _SHELLS:
        raise Refusal('shell-command')
    try:
        resolved = _resolved(command[0], env, cwd)
        path_git = _resolved('git', env, cwd)
    except OSError:
        raise Refusal('git-config') from None
    if resolved is not None and resolved.name in _SHELLS:
        raise Refusal('shell-command')
    is_git = (supplied == 'git' or
              (resolved is not None and (resolved.name == 'git' or resolved == path_git)))
    if not is_git:
        return 'git'
    for arg in command[1:]:
        if not arg.startswith('-'):
            break  # Subcommand options, such as commit -c, are not global.
        if arg.startswith('-C'):
            raise Refusal('git-directory')
        if arg.startswith('-c') or arg == '--config-env' or arg.startswith('--config-env='):
            raise Refusal('git-config-option')
        if arg not in _GIT_FLAGS:
            # Includes --git-dir, --work-tree, --bare, --namespace,
            # --exec-path and unknown future options. No argument skipping.
            raise Refusal('git-option')
    return command[0]  # Inspect the same explicit Git or PATH alias as execution.


def _git(git, args, env, cwd):
    try:
        result = process.run([git, 'config', *args], env=env, cwd=cwd,
                             stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, timeout=5, check=False)
    except process.ProcessError as error:
        if error.kind not in {'not-found', 'timeout', 'os-error', 'exit'}:
            raise  # Programming errors retain CD-2's unexpected-failure contract.
        raise Refusal('git-config') from None
    if result.returncode != 0:
        raise Refusal('git-config')
    return _entries(result.stdout)


def _entries(data):
    # NUL terminates each entry. Only its first newline separates key/value;
    # no newline means a valueless key (a true boolean in Git).
    if not isinstance(data, bytes) or (data and not data.endswith(b'\0')):
        raise Refusal('git-config')
    entries = []
    for entry in data.split(b'\0')[:-1]:
        key, separator, value = entry.partition(b'\n')
        if not _KEY.fullmatch(key):
            raise Refusal('git-config')
        entries.append((key.lower(), value if separator else None))
    return entries


def _setting_kind(key):
    section, _, rest = key.partition(b'.')
    variable = rest.rsplit(b'.', 1)[-1]
    if section == b'http' and variable == b'extraheader':
        return 'git-extraheader'
    if section == b'url' and b'.' in rest and variable in (b'insteadof', b'pushinsteadof'):
        return 'git-url-rewrite'
    if key == b'trace2.envvars':
        return 'git-trace-env'
    return None


def _ssl_key(key):
    return key.startswith(b'http.') and key.rsplit(b'.', 1)[-1] == b'sslverify'


def _netrc(env, cwd):
    home = env.get('HOME')
    if not home:
        raise Refusal('home-unset')
    try:
        data = (Path(cwd) / home / '.netrc').read_bytes()
    except FileNotFoundError:
        return
    except OSError:
        raise Refusal('netrc-unreadable') from None
    # Preserve the reference's conservative token scan, including comments
    # and quoted/case-insensitive matches, without interpreting credentials.
    tokens = {token.strip(b'"').lower() for token in data.split()}
    if b'github.com' in tokens:
        raise Refusal('netrc-github')
    if b'default' in tokens:
        raise Refusal('netrc-default')


def check(command, *, env, cwd):
    """Return None or raise Refusal; env is the sanitized token-free base.

    command is a nonempty argv list; cwd is the runner's captured starting
    directory. Neither input is mutated. This is a guardrail, not a sandbox
    for arbitrary wrapper programs, aliases, or configuration changes later.
    """
    git = _command_git(command, env, cwd)
    _netrc(env, cwd)
    entries = _git(git, ['--null', '--list', '--includes'], env, cwd)
    for key, value in entries:
        kind = _setting_kind(key)
        if kind:
            raise Refusal(kind)
    ssl_keys = [key for key, value in entries if _ssl_key(key)]
    if ssl_keys:
        # Git itself defines boolean spellings, numbers, whitespace and errors.
        booleans = _git(git, ['--null', '--includes', '--type=bool', '--get-regexp', _SSL], env, cwd)
        if ([key for key, value in booleans] != ssl_keys or
                any(value not in (b'true', b'false') for key, value in booleans)):
            raise Refusal('git-config')
        if any(value == b'false' for key, value in booleans):
            raise Refusal('git-ssl-verify')
    # Probe actual override support, not a version string. Random token-free
    # markers prevent an old Git's file entries from impersonating the tail.
    marker = os.urandom(16).hex()
    probe = dict(env, GIT_CONFIG_COUNT='2', GIT_CONFIG_KEY_0='t3compose.preflight',
                 GIT_CONFIG_VALUE_0=marker, GIT_CONFIG_KEY_1='t3compose.preflight',
                 GIT_CONFIG_VALUE_1=marker + '-last')
    tail = _git(git, ['--null', '--list', '--includes'], probe, cwd)[-2:]
    if tail != [(b't3compose.preflight', marker.encode()),
                (b't3compose.preflight', (marker + '-last').encode())]:
        raise Refusal('git-config-overrides')
