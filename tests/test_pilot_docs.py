"""Synthetic, offline coverage for pilot documents and kickoff template."""
import contextlib
import hashlib
import io
import json
import re
import runpy
import sys
import unicodedata
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT
from t3compose import cli

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}')
PATH_RE = re.compile(r'(?:^|\s)/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]')
LONG_RE = re.compile(r'\b[a-z][a-z0-9]*:[A-Za-z0-9%._-]{8,}')
HEX_RE = re.compile(r'[0-9a-fA-F]{32,}')

DOC_PATHS = (
    'skill/templates/manager-kickoff.md', 'docs/restart.md',
    'docs/pilot.md', 'tests/test_pilot_docs.py',
)
PLACEHOLDERS = {
    'SKILL', 'REPOSITORY', 'RECORDS_PATH', 'ROUTING_POLICY_PATH',
    'TAKEOVER_DUMP_PATH', 'CHECKPOINT_SEQ', 'CHECKPOINT_DIGEST',
}


def read_doc(relpath):
    return (ROOT / relpath).read_text(encoding='utf-8')


def extract_section(text, heading):
    match = re.search(rf'^## {re.escape(heading)}\b', text, re.M)
    if not match:
        raise AssertionError(f'heading not found: {heading}')
    start = match.end()
    nxt = re.search(r'^##? ', text[start:], re.M)
    return text[start:start + nxt.start()] if nxt else text[start:]


SEPARATORS = set(' \t`;&|()<>')
PLAIN_WORD = re.compile(r'[A-Za-z][\w-]*')
DASHES = ''.join(chr(code) for code in (0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0xfe58, 0xfe63, 0xff0d))
EMPHASIS = re.compile(r'(?<![\w\\*_~])(\*{1,3}|_{1,3}|~{1,3})(?=[^\s*_~])(.+?)(?<=[^\s*_~])\1(?![\w*_~])')
OPTION = re.compile('(?:[^A-Za-z0-9 \\t]*?[' + DASHES + ']|[^ \\t]*?[-' + DASHES + ']{2})[A-Za-z0-9]')
EXECUTABLE = re.compile(r'(?:.*/)?t3c')
STANDALONE = re.compile(r'(?<![A-Za-z0-9])(?<![A-Za-z0-9][-_.])t3c(?![A-Za-z0-9])(?![-_.][A-Za-z0-9])')


def continues(char):
    """Whether this character, found after a run of delimiters and sentence punctuation, shows that the word goes on
    for a shell: a letter, a digit, a mark or an invisible character, or ASCII text that is not a separator."""
    if char.isalnum():
        return True
    if char.isascii():
        return bool(char) and char not in SEPARATORS
    return unicodedata.category(char)[0] in 'MC'


def delimiter(char):
    """Whether a character separates words at the start or the end of a word but stays in a word that goes on after
    it: a square bracket, a brace, or a character outside ASCII that is not a letter, a digit, a mark or an invisible
    format or control character."""
    return char in '[]{}' or not (char.isascii() or char.isalnum() or unicodedata.category(char)[0] in 'MC')


def unemphasize(line):
    """Replace each pair of emphasis marks by spaces: two whole runs of one mark, one to three long and equally long.

    A run with no partner, or with a partner of another length or kind, stays in its word."""
    while True:
        plain = EMPHASIS.sub(lambda m: ' ' + m.group(2) + ' ', line)
        if plain == line:
            return line
        line = plain


def split_words(line):
    """Split one line into words as a shell would: at a space, a tab, a backtick, a semicolon, an ampersand,
    a bar, a parenthesis or an angle bracket.

    Text inside a matched pair of quotes is data: it becomes one NUL in its word and is returned apart.
    An apostrophe between two letters is not a quote, and an empty pair of parentheses stays in its word.
    A shell keeps every other character in its word, and so does this function, except at the start or the
    end of a word, where Markdown, JSON and prose put delimiters: a square bracket, a brace and a character
    outside ASCII that is not a letter or digit (white space of other kinds, a typographic quote, a symbol)
    separate words there, a whole run of them at a time. So a name with such a character inside it is
    invalid. A mark and an invisible format or control character always stay in their word. A Unicode dash
    that starts a word and is followed by a letter, a digit or another dash is an option typed with the wrong
    dash, and it stays too."""
    stop = [len(line)] * (len(line) + 1)
    for k in range(len(line) - 1, -1, -1):
        stop[k] = stop[k + 1] if delimiter(line[k]) or line[k] in ',:.!?' else k
    words, word, data = [], '', []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch in '"\'':
            end = line.find(ch, i + 1)
            possessive = ch == "'" and line[i - 1:i].isalpha() and line[i + 1:i + 2].isalpha()
            if end != -1 and not possessive:
                data.append(line[i + 1:end])
                word += '\0'
                i = end + 1
                continue
        if ch == '(' and line[i + 1:i + 2] == ')':
            word += '()'
            i += 2
            continue
        after = line[i + 1:i + 2]
        glued = bool(word) and continues(line[stop[i + 1]:stop[i + 1] + 1])
        smart = ch in DASHES and bool(after) and (after.isalnum() or after in '-' + DASHES)
        if ch in SEPARATORS or delimiter(ch) and not (glued or smart):
            if word:
                words.append((word, data))
            word, data = '', []
        else:
            word += ch
        i += 1
    if word:
        words.append((word, data))
    return words


def scan_line(line):
    """Yield ('command', (group, command)), ('option', (command, option)) or ('malformed', line) for one line.

    A word is read with its quotes removed and without a closing , : . ! or ?; nothing else is trimmed, so a
    stray mark or bullet glued to a group, a command or an option name makes that name invalid. An option
    belongs to the nearest t3c command before it on the line, else to the global options (command ()); a
    command that is only mentioned in quotes counts as that command when no other is active. A word holds an
    option when it has two dashes (of any kind) and a letter or digit after any text without white space (a
    mark, punctuation, a glued value), or one Unicode dash and a letter or digit after marks or punctuation;
    the option's name is the whole word up to an equals sign after the dashes. A word that is exactly t3c,
    or a path ending in /t3c, needs a group and a command after it, unless it is the helper's pass-through
    (--local-dir, a value and "$@" at the end of the line). Any other word that holds t3c as a token of its own
    (a mark, bullet or punctuation glued before or after it, but not a longer name such as t3c-tmp) makes the
    line malformed. Other quoted text is data: it is scanned on its own and never changes which command an
    option belongs to."""
    words = []
    for word, data in split_words(unemphasize(line)):
        text = word
        for item in data:
            text = text.replace('\0', item, 1)
        words.append((word, data, text.rstrip(',:.!?')))
    command, mentioned, i = (), False, 0
    while i < len(words):
        word, data, text = words[i]
        option = OPTION.match(text)
        if option:
            yield 'option', (command, text[:option.end()] + text[option.end():].split('=')[0])
            if word.count('\0') != 1 or not word.startswith('\0'):
                for item in data:
                    yield from scan_line(item)
        elif text == 't3c()':
            pass
        elif EXECUTABLE.fullmatch(text):
            tail = [w[2] for w in words[i + 1:i + 4]]
            pair = len(tail) > 1 and all(PLAIN_WORD.fullmatch(t) for t in tail[:2])
            passthrough = tail[:1] == ['--local-dir'] and tail[2:] == ['$@'] and len(words) == i + 4
            if not (pair or passthrough):
                yield 'malformed', line.strip()
            if pair:
                command, mentioned = (tail[0], tail[1]), False
                yield 'command', command
                i += 3
                continue
        else:
            if STANDALONE.search(word):
                yield 'malformed', line.strip()
            for item in data:
                for event in scan_line(item):
                    yield event
                    if event[0] == 'command' and (mentioned or not command):
                        command, mentioned = event[1], True
        i += 1


def scan_text(text):
    """Scan a document line by line (a backslash at the end of a line joins the next one).

    Markdown blocks are not parsed, so no fence, span, comment or table can hide a token; only matching emphasis
    marks are removed. The scan is strict by design: the word t3c must be followed by a group and a command, and
    every --word must be a real option of the command before it on its line (or a global option), wherever it
    appears. A line ends only at a line feed or a carriage return; any other separator belongs to its word."""
    for line in re.sub(r'\\\n[ \t]*', ' ', text).replace('\r', '\n').split('\n'):
        yield from scan_line(line)


def documented_commands(text):
    return [value for kind, value in scan_text(text) if kind == 'command']


def documented_options(text):
    return [value for kind, value in scan_text(text) if kind == 'option']


def malformed_commands(text):
    return [value for kind, value in scan_text(text) if kind == 'malformed']


def normalize_ws(s):
    return ' '.join(s.split())


class OfflinePilotDocs(OfflineTestCase):

    def setUp(self):
        self.allow_program('grep')
        self.local = self.temp / 'synthetic-local'
        patterns = self.local / 'state/private-identifiers.txt'
        patterns.parent.mkdir(parents=True)
        patterns.write_text('SYNTHETIC_PRIVATE_SENTINEL\n')
        self.values_path, self.out_path = self.temp / 'values.json', self.temp / 'rendered.md'
        self.template_path = ROOT / 'skill/templates/manager-kickoff.md'
        self.values_dict = {
            'SKILL': 't3-compose-manager', 'REPOSITORY': 'synthetic/pilot-repo',
            'RECORDS_PATH': 'synthetic-records', 'ROUTING_POLICY_PATH': 'synthetic-policy.json',
            'TAKEOVER_DUMP_PATH': 'synthetic-dump.json', 'CHECKPOINT_SEQ': '3',
            'CHECKPOINT_DIGEST': 'sha256:' + ('0' * 64),
        }

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        argv = [str(ROOT / 'bin/t3c'), '--local-dir', str(self.local), *map(str, args)]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
            except SystemExit as result:
                code = result.code
            else:
                self.fail('entry point did not exit')
        return code, out.getvalue(), err.getvalue()

    def test_kickoff_renders_successfully(self):
        self.values_path.write_text(json.dumps(self.values_dict))
        code, out, err = self.invoke(
            'brief', 'render', '--template', str(self.template_path),
            '--values', str(self.values_path), '--out', str(self.out_path),
        )
        self.assertEqual(code, 0, err)
        rendered = self.out_path.read_bytes()
        digest = 'sha256:' + hashlib.sha256(rendered).hexdigest()
        self.assertEqual(json.loads(out), {'digest': digest})
        text = rendered.decode('utf-8')
        for val in self.values_dict.values():
            self.assertIn(val, text)
        self.assertNotIn('{{', text)
        self.assertNotIn('}}', text)

    def test_kickoff_placeholders(self):
        template = read_doc('skill/templates/manager-kickoff.md')
        self.assertEqual(set(re.findall(r'\{\{([A-Z][A-Z0-9_]*)\}\}', template)), PLACEHOLDERS)
        for missing in sorted(PLACEHOLDERS):
            bad = dict(self.values_dict)
            del bad[missing]
            self.values_path.write_text(json.dumps(bad))
            self.out_path.unlink(missing_ok=True)
            code, _, _ = self.invoke(
                'brief', 'render', '--template', str(self.template_path),
                '--values', str(self.values_path), '--out', str(self.out_path),
            )
            self.assertEqual(code, 2)
            self.assertFalse(self.out_path.exists())

    def test_kickoff_leaks_no_state(self):
        template = read_doc('skill/templates/manager-kickoff.md')
        stripped = re.sub(r'\{\{([A-Z][A-Z0-9_]*)\}\}', '', template)
        self.assertIsNone(re.search(r'[0-9a-fA-F]{40}', stripped))
        self.assertNotIn('sha256:', stripped)
        self.assertIsNone(re.search(r'\b[A-Z]{2,4}-\d+[a-z]?\b', stripped))
        self.assertIsNone(re.search(r'\b[a-z][a-z0-9]*:[A-Za-z0-9%._-]{8,}', stripped))
        self.assertNotIn('@', stripped)
        self.assertIsNone(re.search(r'(?:^|\s)/[A-Za-z0-9._~-]+/[A-Za-z0-9._~-]', stripped))
        self.assertIsNone(re.search(r'\d{2,}', stripped))
        self.assertLessEqual(len(template.splitlines()), 45)

    def test_named_commands_exist(self):
        valid = set(cli.command_names())
        found = set()
        for path in DOC_PATHS[:3]:
            text = read_doc(path)
            self.assertEqual(malformed_commands(text), [], path)
            for cmd in documented_commands(text):
                self.assertIn(cmd, valid, path)
                found.add(cmd)
        required = {
            ('work', 'snapshot'), ('work', 'reconcile'), ('work', 'show'),
            ('work', 'list'), ('event', 'add'), ('brief', 'render'),
        }
        self.assertTrue(required.issubset(found))

    def test_named_options_exist(self):
        help_cache = {}

        def help_of(*words):
            if words not in help_cache:
                code_rc, out, err = self.invoke(*words, '--help')
                self.assertEqual(code_rc, 0, err)
                help_cache[words] = json.loads(out)['help']
            return help_cache[words]

        for path in DOC_PATHS[:3]:
            for command, option in documented_options(read_doc(path)):
                self.assertRegex(help_of(*command), rf'(?<![\w-]){re.escape(option)}(?![\w-])', f'{path}: {option}')

    def test_scanner_line_contract(self):
        ws, ea, wl = ('work', 'snapshot'), ('event', 'add'), ('work', 'list')

        def cmd(value):
            return 'command', value

        def opt(value, name):
            return 'option', (value, name)

        cases = [
            ('Run `t3c work snapshot` with `--through-seq`.', [cmd(ws), opt(ws, '--through-seq')]),
            ('`t3c work snapshot` with `--through-seq.INVALID` and `--through-seq_invalid`.',
             [cmd(ws), opt(ws, '--through-seq.INVALID'), opt(ws, '--through-seq_invalid')]),
            ('Use `t3c work snapshot.INVALID` here.', [('malformed', 'Use `t3c work snapshot.INVALID` here.')]),
            ('t3c event add --file "t3c work snapshot" --through-seq 0',
             [cmd(ea), opt(ea, '--file'), cmd(ws), opt(ea, '--through-seq')]),
            ('t3c work list "--fake" x', [cmd(wl), opt(wl, '--fake')]),
            ("t3c work list '--a b'", [cmd(wl), opt(wl, '--a b')]),
            ("t3c event add --file='x --bad' --kind restart", [cmd(ea), opt(ea, '--file'), opt((), '--bad'), opt(ea, '--kind')]),
            ('t3c event add --kind=restart', [cmd(ea), opt(ea, '--kind')]),
            ('Use ``t3c work list ` --bad-double``.', [cmd(wl), opt(wl, '--bad-double')]),
            ('<!-- t3c brief render --bad-comment -->', [cmd(('brief', 'render')), opt(('brief', 'render'), '--bad-comment')]),
            ('t3c', [('malformed', 't3c')]),
            ('t3c --help', [('malformed', 't3c --help'), opt((), '--help')]),
            ('the t3c helper reads the records', [cmd(('helper', 'reads'))]),
            ('$ROOT/bin/t3c work list --repo x', [cmd(wl), opt(wl, '--repo')]),
            ('echo "$ROOT/bin/t3c"', [('malformed', 'echo "$ROOT/bin/t3c"')]),
            ('"$ROOT/bin/t3c" --local-dir "$STATE" "$@" work lisst',
             [('malformed', '"$ROOT/bin/t3c" --local-dir "$STATE" "$@" work lisst'), opt((), '--local-dir')]),
            ('****t3c work snapshot***', [('malformed', '****t3c work snapshot***')]),
            ('****t3c work snapshot --through-seq*** 0',
             [('malformed', '****t3c work snapshot --through-seq*** 0'), opt((), '--through-seq***')]),
            ('*_t3c work snapshot*_', [('malformed', '*_t3c work snapshot*_')]),
            ('*_t3c work snapshot --through-seq*_ 0',
             [('malformed', '*_t3c work snapshot --through-seq*_ 0'), opt((), '--through-seq*_')]),
            ('***t3c work snapshot**', [('malformed', '***t3c work snapshot**')]),
            ('**t3c work snapshot*', [('malformed', '**t3c work snapshot*')]),
            ('**_t3c work list_**', [('malformed', '**_t3c work list_**')]),
            ('***t3c work snapshot***', [cmd(ws)]),
            ('t3c" work snapshot', [('malformed', 't3c" work snapshot')]),
            ("t3c' work snapshot", [('malformed', "t3c' work snapshot")]),
            ('t3c" work lisst', [('malformed', 't3c" work lisst')]),
            ('$ROOT/bin/t3c" work lisst', [('malformed', '$ROOT/bin/t3c" work lisst')]),
            ('t3c$ work snapshot', [('malformed', 't3c$ work snapshot')]),
            ('t3c_ work snapshot', [('malformed', 't3c_ work snapshot')]),
            ('"t3c" work snapshot', [cmd(ws)]),
            ('t3c"" work snapshot', [cmd(ws)]),
            ('-t3c work snapshot', [('malformed', '-t3c work snapshot')]),
            ('_t3c work lisst', [('malformed', '_t3c work lisst')]),
            ('$t3c work lisst', [('malformed', '$t3c work lisst')]),
            ('Run:t3c work lisst', [('malformed', 'Run:t3c work lisst')]),
            ('t3c work snapshot ---through-seq 0', [cmd(ws), opt(ws, '---through-seq')]),
            ('t3c work snapshot *--through-seq 0', [cmd(ws), opt(ws, '*--through-seq')]),
            ('t3c work snapshot $--through-seq 0', [cmd(ws), opt(ws, '$--through-seq')]),
            ('t3c work snapshot ' + chr(0x2014) + 'through-seq 0', [cmd(ws), opt(ws, chr(0x2014) + 'through-seq')]),
            ('t3c work snapshot ' + chr(0x2013) + 'through-seq 0', [cmd(ws), opt(ws, chr(0x2013) + 'through-seq')]),
            ('Use the t3c_text field, refs/t3c-publication-snapshot/x and t3c.md.', []),
            ('t3c work snapshot --through-seq' + chr(0x2013) + 'extra 0', [cmd(ws), opt(ws, '--through-seq' + chr(0x2013) + 'extra')]),
            ('t3c work snapshot --through-seq' + chr(0x2026) + 'extra 0', [cmd(ws), opt(ws, '--through-seq' + chr(0x2026) + 'extra')]),
            ('t3c work snapshot --through-seq' + chr(0x200b) + 'extra 0', [cmd(ws), opt(ws, '--through-seq' + chr(0x200b) + 'extra')]),
            ('t3c work snapshot --through-seq' + chr(0x200b) + ' 0', [cmd(ws), opt(ws, '--through-seq' + chr(0x200b))]),
            ('t3c work snapshot ' + chr(0x200b) + '--through-seq 0', [cmd(ws), opt(ws, chr(0x200b) + '--through-seq')]),
            ('t3c work snapshot --through-seq' + chr(0x2013) + '"extra" 0', [cmd(ws), opt(ws, '--through-seq' + chr(0x2013) + 'extra')]),
            ('t3c work snapshot' + chr(0x301) + ' --through-seq 0',
             [('malformed', 't3c work snapshot' + chr(0x301) + ' --through-seq 0'), opt((), '--through-seq')]),
            ('t3c work snapshot' + chr(0x2013) + 'INVALID --through-seq 0',
             [('malformed', 't3c work snapshot' + chr(0x2013) + 'INVALID --through-seq 0'), opt((), '--through-seq')]),
            ('t3c' + chr(0x2013) + 'x work list', [('malformed', 't3c' + chr(0x2013) + 'x work list')]),
            ('t3c work snapshot' + chr(0xa0) + '--through-seq 0',
             [('malformed', 't3c work snapshot' + chr(0xa0) + '--through-seq 0'), opt((), 'snapshot' + chr(0xa0) + '--through-seq')]),
            ('t3c work snapshot --through-seq' + chr(0x2014) + ' 0', [cmd(ws), opt(ws, '--through-seq')]),
            ('t3c work snapshot ' + chr(0x2013) + '--through-seq 0', [cmd(ws), opt(ws, chr(0x2013) + '--through-seq')]),
            ('t3c work snapshot ' + chr(0x2010) + '-through-seq 0', [cmd(ws), opt(ws, chr(0x2010) + '-through-seq')]),
            ('t3c work snapshot --repo r--through-seq 0', [cmd(ws), opt(ws, '--repo'), opt(ws, 'r--through-seq')]),
            ('t3c work snapshot x--through-seq 0', [cmd(ws), opt(ws, 'x--through-seq')]),
            ('t3c work snapshot --through-seq' + chr(0x201d) + chr(0x201c) + 'extra 0',
             [cmd(ws), opt(ws, '--through-seq' + chr(0x201d) + chr(0x201c) + 'extra')]),
            ('t3c work snapshot --through-seq' + chr(0xa0) + chr(0x200b) + ' 0', [cmd(ws), opt(ws, '--through-seq' + chr(0xa0) + chr(0x200b))]),
            ('t3c work snapshot --through-seq' + chr(0x201d) + '.extra 0', [cmd(ws), opt(ws, '--through-seq' + chr(0x201d) + '.extra')]),
            ('Run ' + chr(0x201c) + 't3c work snapshot' + chr(0x201d) + '. Then stop.', [cmd(ws)]),
            ('t3c work snapshot --through-seq[x] 0', [cmd(ws), opt(ws, '--through-seq[x')]),
            ('t3c work snapshot{x} --through-seq 0', [('malformed', 't3c work snapshot{x} --through-seq 0'), opt((), '--through-seq')]),
            ('Usage: t3c work snapshot [--through-seq N]', [cmd(ws), opt(ws, '--through-seq')]),
            ('Steps S1' + chr(0x2013) + 'S4 and pages 3' + chr(0x2013) + '5.', []),
            ("t3'c' work lisst", [cmd(('work', 'lisst'))]),
            ("t'3'c work lisst", [cmd(('work', 'lisst'))]),
            ('t3c() { "$PY" -I -S "$ROOT/bin/t3c" --local-dir "$STATE" "$@"; }', [opt((), '--local-dir')]),
            ('|---|---|---|', []),
            ("the old manager's step: `t3c work list --bad`", [cmd(wl), opt(wl, '--bad')]),
            ('Run t3c work list && t3c event add --kind restart --nope',
             [cmd(wl), cmd(ea), opt(ea, '--kind'), opt(ea, '--nope')]),
            ('$(t3c work list --repo r)', [cmd(wl), opt(wl, '--repo')]),
            ('t3c work list --reason "unclosed --bad', [cmd(wl), opt(wl, '--reason'), opt(wl, '--bad')]),
            ('Run **t3c work snapshot** and _t3c work list_.', [cmd(ws), cmd(wl)]),
            ('`t3c work snapshot` takes **--through-seq** and _--through-seqq_.',
             [cmd(ws), opt(ws, '--through-seq'), opt(ws, '--through-seqq')]),
            ('~~t3c work list --repo x~~', [cmd(wl), opt(wl, '--repo')]),
            (chr(0x201c) + 't3c work snapshot' + chr(0x201d) + ' takes ' + chr(0x201c) + '--through-seq' + chr(0x201d) + '.',
             [cmd(ws), opt(ws, '--through-seq')]),
            ('"$ROOT/bin/t3c" work list --repo x', [cmd(wl), opt(wl, '--repo')]),
            ('"$PY" -I -S "$ROOT/bin/t3c" event add --kind restart', [cmd(ea), opt(ea, '--kind')]),
            ('t3c "work" "list" --repo x', [cmd(wl), opt(wl, '--repo')]),
            ('Is it t3c work list? Yes!', [cmd(wl)]),
            ('t3c work list --local-dir=$ROOT/bin/t3c', [cmd(wl), opt(wl, '--local-dir')]),
            (chr(0xab) + 't3c work list --repo x' + chr(0xbb) + ' ' + chr(0x300c) + 't3c event add' + chr(0x300d),
             [cmd(wl), opt(wl, '--repo'), cmd(ea)]),
            (chr(0x201e) + '--repo' + chr(0x201c) + ' of t3c work list' + chr(0x2026), [opt((), '--repo'), cmd(wl)]),
            ('t3c' + chr(0x200b) + 'work list', [('malformed', 't3c' + chr(0x200b) + 'work list')]),
            ('"$ROOT/bin/t3c" work snapshot.INVALID', [('malformed', '"$ROOT/bin/t3c" work snapshot.INVALID')]),
            ('"$ROOT/bin/t3c" work', [('malformed', '"$ROOT/bin/t3c" work')]),
            ('"$ROOT/bin/t3c" --local-dir "$STATE" work lisst',
             [('malformed', '"$ROOT/bin/t3c" --local-dir "$STATE" work lisst'), opt((), '--local-dir')]),
            ('t3c work snapshot_', [cmd(('work', 'snapshot_'))]),
            ('t3c work snapshot --through-seq_ 0', [cmd(ws), opt(ws, '--through-seq_')]),
            ('t3c work snapshot*', [('malformed', 't3c work snapshot*')]),
            ('t3c work snapshot~', [('malformed', 't3c work snapshot~')]),
            ("t3c work snapshot'", [('malformed', "t3c work snapshot'")]),
            ('t3c work snapshot --through-seq"', [cmd(ws), opt(ws, '--through-seq"')]),
            ('t3c work snapshot _x_ and *y*', [cmd(ws)]),
            ('Run "t3c work snapshot" with "--through-seq".', [cmd(ws), opt(ws, '--through-seq')]),
            ('Run "t3c work snapshot" with --through-seq and "t3c work list" with --repo.',
             [cmd(ws), opt(ws, '--through-seq'), cmd(wl), opt(wl, '--repo')]),
        ]
        for line, expected in cases:
            self.assertEqual(list(scan_line(line)), expected, line)
        broken = 't3c work snapshot' + chr(0x2028) + 'INVALID --through-seq 0'
        self.assertEqual(malformed_commands(broken), [broken])

    def test_scanner_reads_every_markdown_form(self):
        sample = (
            'Run `t3c work snapshot` with `--through-seqINVALID` and `--through-seq_invalid`.\n'
            '~~~sh\n'
            't3c event add --kind restart --bad-tilde\n'
            '~~~\n'
            '````\n'
            '```\n'
            't3c work show --bad-four\n'
            '```\n'
            '````\n'
            'Use ``t3c work list ` --bad-double``.\n'
            '<!-- t3c brief render --bad-comment -->\n'
            '**t3c work show --bad-bold**\n'
            '_t3c brief render --bad-italic_ and ~~t3c work list --bad-strike~~\n'
            't3c work snapshot \\\n'
            '  --repo r --bad-continued\n'
            't3c\n'
            'work new --bad-wrapped\n'
        )
        ws, show, render = ('work', 'snapshot'), ('work', 'show'), ('brief', 'render')
        self.assertEqual(list(scan_text(sample)), [
            ('command', ws), ('option', (ws, '--through-seqINVALID')), ('option', (ws, '--through-seq_invalid')),
            ('command', ('event', 'add')), ('option', (('event', 'add'), '--kind')), ('option', (('event', 'add'), '--bad-tilde')),
            ('command', show), ('option', (show, '--bad-four')),
            ('command', ('work', 'list')), ('option', (('work', 'list'), '--bad-double')),
            ('command', render), ('option', (render, '--bad-comment')),
            ('command', show), ('option', (show, '--bad-bold')),
            ('command', render), ('option', (render, '--bad-italic')),
            ('command', ('work', 'list')), ('option', (('work', 'list'), '--bad-strike')),
            ('command', ws), ('option', (ws, '--repo')), ('option', (ws, '--bad-continued')),
            ('malformed', 't3c'), ('option', ((), '--bad-wrapped')),
        ])
        self.assertEqual(malformed_commands(sample), ['t3c'])

    def test_restart_order(self):
        restart_doc = read_doc('docs/restart.md')
        before_sec = extract_section(restart_doc, 'Before the restart')
        self.assertLess(before_sec.index('work snapshot'), before_sec.index('"phase": "before"'))
        after_sec = extract_section(restart_doc, 'After the restart')
        p1, p2 = after_sec.index('--through-seq'), after_sec.index('task_status')
        p3, p4 = after_sec.index('"phase": "after"'), after_sec.index('beforeRef')
        self.assertTrue(p1 < p2 < p3 < p4)
        for token in ('t3_thread_send', 't3_thread_configure', '/clear', 'continuation', 'L7'):
            self.assertIn(token, restart_doc)
        rules = normalize_ws(extract_section(restart_doc, 'Rules'))
        self.assertRegex(rules, r'(?i)in-flight restart keeps the thread that dispatched the children')
        self.assertRegex(rules, r'(?i)same thread keeps managing its children')

    def test_checklist_order(self):
        pilot_doc = read_doc('docs/pilot.md')
        checklist = extract_section(pilot_doc, 'Pilot checklist')
        step_matches = list(re.finditer(r'\*\*S(\d+)\*\*', checklist))
        self.assertEqual([int(m.group(1)) for m in step_matches], list(range(1, 20)))
        steps = {}
        for i, m in enumerate(step_matches):
            end = step_matches[i + 1].start() if i + 1 < len(step_matches) else len(checklist)
            steps[f'S{m.group(1)}'] = checklist[m.end():end]
        self.assertIn('the hold is in force', steps['S1'].lower())
        self.assertIn('reconcil', steps['S4'].lower())
        self.assertIn('dispatch', steps['S8'].lower())
        p1, p3 = checklist.index('**S1**'), checklist.index('**S3**')
        p4, p8 = checklist.index('**S4**'), checklist.index('**S8**')
        self.assertTrue(p1 < p4 < p8 and p3 < p8)

    def test_criteria_map(self):
        design = read_doc('docs/superpowers/specs/2026-10-27-t3-compose-design.md')
        marker = 'The first live pilot must demonstrate:'
        idx = design.index(marker)
        sub = design[idx + len(marker):].strip().split('\n\n')[0]
        bullets = [normalize_ws(b) for b in re.findall(r'^-\s+(.*?)(?=\n-|\Z)', sub, re.S | re.M)]
        self.assertEqual(len(bullets), 5)
        pilot_doc = read_doc('docs/pilot.md')
        norm_pilot = normalize_ws(pilot_doc)
        all_steps = {f'S{i}' for i in range(1, 20)}
        expected_steps = ({'S8', 'S9'}, {'S10'}, {'S11'}, {'S12'}, {'S3', 'S13', 'S14'})
        for bullet, expected in zip(bullets, expected_steps):
            self.assertIn(bullet, norm_pilot)
            for line in pilot_doc.splitlines():
                if line.startswith('|') and bullet in normalize_ws(line):
                    steps = set(re.findall(r'\bS\d+\b', line))
                    self.assertTrue(steps.issubset(all_steps))
                    self.assertEqual(steps, expected)
                    break
            else:
                self.fail(f'criteria table row not found for: {bullet}')

    def test_takeover_dump_reconciliation_and_prose_hold(self):
        sources = 'against Git, GitHub, Linear and the open threads'
        hold = 'Until the writer transfer, the prose manager holds and dispatches nothing.'
        kickoff = normalize_ws(read_doc('skill/templates/manager-kickoff.md'))
        self.assertIn("Takeover dump (the old manager's handover document; it can be the temporary prose manager's):", kickoff)
        self.assertIn(f'first action on the work is to reconcile the takeover dump named above {sources}', kickoff)
        pilot_doc = read_doc('docs/pilot.md')
        gates = normalize_ws(extract_section(pilot_doc, 'Order of gates'))
        takeover = normalize_ws(extract_section(pilot_doc, 'Takeover'))
        checklist = normalize_ws(extract_section(pilot_doc, 'Pilot checklist'))
        for section in (kickoff, gates, takeover, checklist):
            self.assertIn(hold, section)
        self.assertIn(f'first action on the work is to reconcile the dump {sources}', takeover)
        self.assertIn(f'Reconcile the takeover dump {sources}', checklist)
        handoff = normalize_ws(extract_section(pilot_doc, 'The handoff record'))
        self.assertIn(f'"nextAction": "reconcile the takeover dump {sources}"', handoff)
        self.assertIn('It can be the handover document of the temporary prose manager (plan decision 16).', takeover)
        self.assertIn("The dump's path is private: it goes only into the kickoff's takeover-dump pointer and the handoff record's `takeoverDumpPath`, never into a tracked file.", takeover)
        step4 = re.search(r'\*\*S4\*\*(.*?)\*\*S5\*\*', checklist).group(1)
        self.assertIn('dispatch nothing before this reconciliation is done', step4)

    def test_kickoff_skill_name_is_the_manager_skill(self):
        front = re.match(r'---\n(.*?)\n---\n', read_doc('skill/SKILL.md'), re.DOTALL)
        self.assertIsNotNone(front)
        name = re.search(r'^name:\s*(\S+)\s*$', front.group(1), re.MULTILINE)
        self.assertIsNotNone(name)
        self.assertEqual(self.values_dict['SKILL'], name.group(1))

    def test_no_identifier_like_shapes(self):
        for relpath in DOC_PATHS:
            text = read_doc(relpath)
            self.assertIsNone(EMAIL_RE.search(text), f'email address found in {relpath}')
            self.assertIsNone(PATH_RE.search(text), f'absolute path found in {relpath}')
            self.assertIsNone(LONG_RE.search(text), f'long colon token found in {relpath}')
            self.assertIsNone(HEX_RE.search(text), f'hexadecimal string found in {relpath}')
