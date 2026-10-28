"""Literal brief rendering and validation; no Git movement or agent dispatch."""
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from . import cli, identifier_list, jsonio
from .paths import ROOT

LIMIT = 1048576
UNSAFE = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]')
TOKEN = re.compile(r'\{\{([A-Z][A-Z0-9_]*)\}\}')
FIELDS = ('Role', 'Tier', 'Provider', 'Model', 'Reason', 'Repository', 'Provisioned',
          'Start', 'Review-base', 'Review-head', 'Round', 'Privacy-findings',
          'Unpublished-range')
STRUCTURE = re.compile(r'^\s*(?:#|(?:' + '|'.join(FIELDS) +
                       r'|Move-rule|Inline-instructions):)', re.I | re.M)
MOVE = ('Move-rule: three-case-v1\n'
        '1. HEAD=start => continue.\n'
        '2. HEAD=provisioned AND clean AND start exists AND fresh own branch => move own branch to start only.\n'
        '3. Otherwise OR missing start => stop; never fetch.')
MOVE_BLOCK = re.compile('^' + re.escape(MOVE) + '$', re.M)
SECTIONS = {
    'worker': ('Role and routing', 'Worktree and holds', 'Repository and commits',
               'Read first', 'Owned paths', 'Objective', 'Interfaces', 'Acceptance',
               'Test first', 'Validation', 'Commits', 'Return'),
    'reviewer': ('Independence', 'Role and routing', 'Repository and commits',
                 'Read first', 'Review questions', 'Checks', 'Findings format',
                 'Verdict', 'Return'),
}


def _fail(message='invalid brief input'):
    raise cli.CommandError(message) from None


def _read_text(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                _fail()
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            _fail('brief input exceeds byte limit')
        text = raw.decode('utf-8')
        if UNSAFE.search(text):
            _fail()
        return text
    except (OSError, UnicodeError, ValueError):
        _fail('brief input is unreadable or invalid')


def _screen(patterns, text):
    if identifier_list.match_identifiers(patterns, text):
        _fail('brief privacy check failed')


def _digest(text):
    return {'digest': 'sha256:' + hashlib.sha256(text.encode('utf-8')).hexdigest()}


def _privacy_section(meta):
    findings, span = meta['Privacy-findings'], meta['Unpublished-range']
    if findings == span == 'none':
        return ''
    if (meta['Role'] != 'worker' or meta['Round'] != 'corrections' or
            not re.fullmatch(r'[A-Z][A-Z0-9]*-[1-9][0-9]*(?:,[A-Z][A-Z0-9]*-[1-9][0-9]*)*', findings) or
            span != meta['Review-base'] + '..' + meta['Start'] or
            meta['Review-base'] == meta['Start']):
        _fail('invalid privacy Corrections scope')
    return ('## Privacy Corrections\n'
            'Enumerated privacy matches: ' + findings + '.\n'
            'You may amend or rebase only unpublished commits in ' + span + '\n'
            'to remove those matches. Preserve all other work; never rewrite published commits.\n')


def _metadata(meta):
    role = meta.get('Role')
    expected = set(FIELDS) - (set() if role == 'reviewer' else {'Review-head'})
    if role not in SECTIONS or set(meta) != expected:
        _fail('invalid brief metadata')
    if any(not value.strip() or len(value) > 512 or
           any(ord(c) < 32 or ord(c) == 127 for c in value) for value in meta.values()):
        _fail('invalid brief metadata')
    if meta['Tier'] not in ('easy', 'medium', 'challenging'):
        _fail('invalid brief tier')
    if meta['Provider'] not in ('agy', 'pi', 'claude', 'codex'):
        _fail('invalid brief provider family')
    rounds = ('implementation', 'corrections') if role == 'worker' else (
        'earlier-review', 'corrections-review', 'unled-final-review')
    if meta['Round'] not in rounds:
        _fail('invalid brief round')
    if role == 'reviewer' and (meta['Tier'] == 'easy' or
                               meta['Provider'] not in ('claude', 'codex')):
        _fail('invalid Reviewer selection')
    for name in ('Provisioned', 'Start', 'Review-base', *(['Review-head'] if role == 'reviewer' else [])):
        if not re.fullmatch(r'[0-9a-fA-F]{40}', meta[name]):
            _fail('brief commits must be full 40-hex values')
    return _privacy_section(meta)


def _validate(text):
    if UNSAFE.search(text):
        _fail('invalid brief text boundaries')
    if '{{' in text or '}}' in text:
        _fail('unresolved brief placeholder')
    lines = text.split('\n')
    if not lines or lines[0] != '# t3c brief v1':
        _fail('unsupported brief structure')
    meta, pos = {}, 1
    while pos < len(lines) and lines[pos]:
        key, separator, value = lines[pos].partition(': ')
        if not separator or key not in FIELDS or key in meta:
            _fail('invalid or duplicate brief metadata')
        meta[key] = value
        pos += 1
    privacy = _metadata(meta)
    body = '\n'.join(lines[pos:])
    headings = list(re.finditer(r'^## (.+)$', body, re.M))
    names = [match[1] for match in headings]
    expected = list(SECTIONS[meta['Role']])
    if privacy:
        expected.insert(-1, 'Privacy Corrections')
    if names != expected or body[:headings[0].start()].strip():
        _fail('missing, duplicate or unordered brief sections')
    sections = {}
    for index, match in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        content = body[match.end():end].strip()
        if not content:
            _fail('empty required brief section')
        sections[match[1]] = content
    # Metadata-like lines cannot hide inside untrusted multiline section content.
    content = re.sub(r'^## .+$', '', body, flags=re.M)
    content = MOVE_BLOCK.sub('', content, count=1)
    if meta['Role'] == 'worker':
        routing = sections['Role and routing']
        if (routing.count('Inline-instructions:') != 1 or
                not re.search(r'^Inline-instructions:(?:\n|$)', routing, re.M)):
            _fail('missing inline instructions slot')
        inline = routing.split('Inline-instructions:', 1)[1].strip()
        if meta['Provider'] in ('agy', 'pi') and not inline:
            _fail('inline instructions required for this Worker')
        content = content.replace('Inline-instructions:', '', 1)
    if STRUCTURE.search(content):
        _fail('duplicate brief structure')
    if not MOVE_BLOCK.search(sections['Repository and commits']):
        _fail('invalid three-case move rule')
    if privacy and ('## Privacy Corrections\n' + sections['Privacy Corrections'] + '\n') != privacy:
        _fail('invalid privacy Corrections authorization')


def _write_raw(path, text):
    """Publish raw Markdown with mode 0600, file fsync and directory fsync."""
    path = Path(path)
    pending = None
    try:
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.t3c-brief-')
        pending = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        _fail('brief output could not be published')
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def render(args):
    patterns = identifier_list.load_identifiers(args.local_dir)
    builtin = args.template in SECTIONS
    template_path = ROOT / 'skill/templates' / (args.template + '.md') if builtin else Path(args.template)
    template = _read_text(template_path)
    _screen(patterns, template)
    _screen(patterns, _read_text(args.values))
    try:
        values, _ = jsonio.read_json_with_digest(args.values, max_bytes=LIMIT)
    except (OSError, ValueError, UnicodeError):
        _fail('invalid brief values JSON')
    if (not isinstance(values, dict) or any(
            not isinstance(key, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) or
            not isinstance(value, str) or UNSAFE.search(value) for key, value in values.items())):
        _fail('brief values must be an object of named strings')
    try:
        _screen(patterns, jsonio.canonical_json(values).decode('utf-8'))
    except UnicodeError:
        _fail('invalid brief values encoding')
    structured = builtin or template.startswith('# t3c brief v1\n')
    if structured:
        if 'PRIVACY_CORRECTIONS' in values:
            _fail('privacy authorization is generated from structured scope only')
        if any(STRUCTURE.search(value) for value in values.values()):
            _fail('values contain reserved brief structure')
        values.setdefault('INLINE_INSTRUCTIONS', '')
        values.setdefault('PRIVACY_FINDINGS', 'none')
        values.setdefault('UNPUBLISHED_RANGE', 'none')
        values['PRIVACY_CORRECTIONS'] = ''
    def substitute(match):
        if match[1] not in values:
            _fail('missing brief value')
        return values[match[1]]
    def fill():
        # Bound the final byte count before allocating an expanded artifact.
        size = len(template.encode('utf-8'))
        for match in TOKEN.finditer(template):
            size += len(substitute(match).encode('utf-8')) - len(match[0])
        if size > LIMIT:
            _fail('expanded brief exceeds byte limit')
        return TOKEN.sub(substitute, template)

    text = fill()
    if structured:
        # Obtain validated metadata before generating the exceptional section.
        header = text.split('\n\n', 1)[0].split('\n')[1:]
        meta = dict(line.split(': ', 1) for line in header)
        privacy = _metadata(meta)
        if privacy:
            values['PRIVACY_CORRECTIONS'] = '\n' + privacy
            text = fill()
        _validate(text)
    elif '{{' in text or '}}' in text:
        _fail('unresolved brief placeholder')
    _screen(patterns, text)
    _write_raw(args.out, text)
    return _digest(text)


def check(args):
    patterns = identifier_list.load_identifiers(args.local_dir)
    text = _read_text(args.file)
    _screen(patterns, text)
    _validate(text)
    return _digest(text)


def register(registry):
    group = registry.group('brief')
    parser = group.add_parser('render')
    parser.add_argument('--template', required=True)
    parser.add_argument('--values', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.set_defaults(handler=render)
    parser = group.add_parser('check')
    parser.add_argument('file', type=Path)
    parser.set_defaults(handler=check)
