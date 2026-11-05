"""Guarded publication contract tests; all external writes are injected."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

from t3compose import cli, evidence, jsonio, process, publish, records, token_run
from tests.support import ROOT, FakeRunner, OfflineTestCase

REPO = 'github.com/fixture-org/publish-fixture'
URL = 'https://' + REPO + '.git'
WRITER = 'manager@' + REPO
REF = 'refs/heads/fixture/topic'


class OfflinePublish(OfflineTestCase):
    def setUp(self):
        self.allow_program('grep')
        self.repo = self.temp / 'checkout'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'remote.origin.url', URL)
        self.git('commit', '--allow-empty', '-qm', 'Fixture base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (self.repo / 'file.txt').write_text('public fixture\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture head')
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.local = self.temp / 'local'
        (self.local / 'state').mkdir(parents=True)
        (self.local / 'state/private-identifiers.txt').write_text('PRIVATE_SENTINEL\n')
        jsonio.atomic_replace(self.local / 'github-app.json', {
            'version': 1, 'app': {'slug': 'fixture-worker', 'appId': 1,
            'installationAccount': 'fixture-org',
            'keychain': {'service': 'fixture-service', 'account': 'fixture-account'}},
            'repository': {'owner': 'fixture-org', 'name': 'publish-fixture',
                           'permissions': {'contents': 'write'}}})
        self.args = SimpleNamespace(local_dir=self.local, repo_path=str(self.repo),
                                    head=self.head, work='WORK', branch='fixture/topic')
        self.real_helper_info = cli.helper_info
        self.enterContext(patch.object(cli, 'helper_info', return_value={
            'commit': 'e' * 40, 'dirty': False}))
        self.serial = self.worker_round = 0
        self.current = self.base
        self.create_work()
        self.cover(self.head)
        self.refs = {'refs/heads/main': self.base}
        self.launches = []
        self.enterContext(patch.object(publish, '_remote', side_effect=self.remote))
        self.enterContext(patch.object(token_run, 'run_command', side_effect=self.run_child))

    def git(self, *args):
        return process.run(['git', *args], cwd=self.repo, capture_output=True,
                           text=True, check=True).stdout

    def add(self, kind, data):
        return records.append_event(self.local, REPO, 'WORK', kind, data, writer=WRITER)

    def create_work(self):
        records.create_work(self.local, REPO, 'WORK', {
            'workId': 'WORK', 'linearIssue': None, 'repository': REPO,
            'targetBranch': 'main', 'reviewBase': self.base, 'scope': 'Fixture push',
            'designRefs': [], 'ownedPaths': ['file.txt'], 'dependencies': [],
            'acceptance': [{'id': 'AC', 'text': 'Fixture acceptance'}], 'requiredChecks': [{'id': 'SUITE', 'command': 'fixture-check',
                                                'kind': 'fast'}], 'writer': WRITER}, writer=WRITER)

    def native(self, role, head):
        self.serial += 1
        task, request = f'fixture-task-{self.serial}', f'fixture-request-{self.serial}'
        start = head
        if role == 'worker':
            self.worker_round += 1
            start = self.current
        self.add('dispatch-intent', {'role': role, 'round': self.worker_round if role == 'worker' else self.serial,
            'clientRequestId': request, 'briefPath': 'fixture-brief.md',
            'briefDigest': 'sha256:' + 'a' * 64,
            'selection': {'providerInstanceId': 'fixture-provider', 'model': 'fixture-model', 'options': {}},
            'provisionedCommit': head, 'startCommit': start, 'reviewBase': self.base,
            'dispatchingCheckout': str(self.repo), 'isolation': 'worktree'})
        self.add('dispatch-confirmed', {'clientRequestId': request, 'taskId': task,
            'childThread': f'fixture-child-{self.serial}', 'worktreePath': str(self.repo)})
        self.add('result', {'taskId': task, 't3Status': {'state': 'completed', 'summary': 'Fixture'},
            'worktreePath': str(self.repo), 'resultCommit': head, 'parentCommit': self.base,
            'branchObserved': 'fixture/topic', 'workerClaims': {}, 'verifiedFacts': {}, 'questions': []})
        if role == 'worker':
            self.current = head
        return task

    def cover(self, head, lead=False):
        self.native('worker', head)
        self.add('criterion-evidence', {'criterionId': 'AC', 'commit': head,
            'source': 'manager', 'evidence': 'Fixture acceptance log'})
        self.add('check', {'checkId': 'SUITE', 'command': 'fixture-check', 'commit': head,
                          'outcome': 'pass', 'source': 'manager', 'evidencePath': 'fixture-check.log'})
        if lead:
            self.add('lead-assignment', {'thread': 'fixture-lead', 'assignedBy': WRITER,
                       'date': '2026-11-01', 'reason': 'Fixture assignment'})
            actor = {'kind': 'lead', 'thread': 'fixture-lead'}
        else:
            actor = {'kind': 'task', 'taskId': self.native('reviewer', head)}
        self.add('review', {'reviewer': actor, 'base': self.base, 'head': head,
            'verdict': 'approve', 'findings': [],
            'evidence': {'path': 'fixture-review.md', 'digest': 'sha256:' + 'a' * 64}})

    def remote(self, repo, url):
        self.assertEqual((repo, url), (self.repo, URL))
        return dict(self.refs)

    def run_child(self, argv, path, *, local_dir):
        self.assertEqual(Path.cwd(), self.repo)
        self.assertEqual(local_dir, self.local)
        self.assertFalse(path.exists())
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        # A real child inherits these descriptors, so its raw streams must be discarded.
        self.assertTrue(all(os.path.samestat(os.fstat(fd), os.stat(os.devnull)) for fd in (1, 2)))
        self.assertEqual(self.events()[-1]['kind'], 'op-intent')
        self.launches.append(argv)
        sha, _, ref = argv[-1].partition(':')
        self.refs[ref] = sha
        result = token_run.RunResult(True, 0, None, 'ok', 0)
        jsonio.atomic_replace(path, result.record())
        print('PRIVATE_SENTINEL child output')
        os.write(2, b'PRIVATE_SENTINEL raw child diagnostic\n')
        return result

    def events(self):
        return records.load_work(self.local, REPO, 'WORK')['events']

    def ops(self):
        return [e['kind'] for e in self.events() if e['kind'].startswith('op-')]

    def invoke(self):
        cwd, env = Path.cwd(), dict(os.environ)
        streams = [os.fstat(fd) for fd in (1, 2)]
        try:
            return publish.push(self.args)
        finally:
            self.assertEqual(Path.cwd(), cwd)
            self.assertEqual(dict(os.environ), env)
            self.assertTrue(all(map(os.path.samestat, streams, [os.fstat(fd) for fd in (1, 2)])))

    def entry(self):
        out, err = io.StringIO(), io.StringIO()
        argv = ['t3c', '--local-dir', str(self.local), 'publish', 'push',
                '--repo-path', str(self.repo), '--work', 'WORK', '--head', self.args.head,
                '--branch', self.args.branch]
        with patch.object(sys, 'argv', argv), redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as exit:
                runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
        return exit.exception.code, out.getvalue(), err.getvalue()

    def policy_command(self, **overrides):
        values = dict(local=self.local, repo=self.repo, base=self.base,
                      head=self.head, branch=self.args.branch, policy=self.temp / 'policy.json')
        values.update(overrides)
        return publish._policy_verify_command(**values)

    def policy_requirement(self, command):
        decision = self.add('decision', {'question': 'Change fixture check?',
            'answer': 'Use fixture verification command', 'decidedBy': 'manager',
            'date': '2026-11-01'})
        self.add('scope', {'decisionRef': decision['seq'],
            'requiredChecks': [{'id': 'SUITE', 'command': command, 'kind': 'fast'}]})

    def policy_pass(self, expected, **overrides):
        data = dict(checkId='SUITE', command=expected, commit=self.head,
                    outcome='pass', source='manager', evidencePath='verify.log')
        data.update(overrides)
        return self.add('check', data)

    def policy_join(self, command):
        return publish._policy_verify_check(self.local, REPO, 'WORK', self.head, command)

    def test_policy_command_arrays_and_no_content_reads(self):
        expected = ['t3c', '--local-dir', str(self.local), 'privacy', 'verify',
            '--repo-path', str(self.repo), '--range', self.base + '..' + self.head,
            '--branch', self.args.branch, '--privacy-policy', str(self.temp / 'policy.json')]
        with patch.object(Path, 'open', side_effect=AssertionError('content read')):
            self.assertEqual(self.policy_command(local=self.local / 'unused/..'), expected)
            texts = [('title', self.temp / 'title'), ('body', self.temp / 'body')]
            self.assertEqual(self.policy_command(text_files=texts), expected +
                ['--text-file', 'title=' + str(self.temp / 'title'),
                 '--text-file', 'body=' + str(self.temp / 'body')])

    def test_policy_command_rejects_untyped_and_invalid_arguments(self):
        for changes in ({'local': b'path'}, {'repo': ''}, {'policy': 1},
                {'policy': 'x\0y'}, {'policy': '\ud800'}, {'policy': 'x' * 4097},
                {'base': 'main'}, {'head': 'A' * 40}, {'branch': '-topic'},
                {'branch': 'a..b'}, {'branch': 'a b'}, {'branch': 'a.lock'},
                {'text_files': 'title=x'}, {'text_files': [('body', 'x'), ('title', 'y')]},
                {'text_files': [('title', 'x')]}, {'text_files': [('title', 'x'), ('body', 2)]}):
            with self.subTest(changes=changes), self.assertRaises(cli.CommandError):
                self.policy_command(**changes)

    def test_policy_join_requires_actual_current_requirement_and_whole_evidence(self):
        command = self.policy_command()
        self.assertFalse(self.policy_join(command))
        self.policy_requirement(command)
        self.assertFalse(self.policy_join(command))
        self.policy_pass(command)
        self.assertTrue(self.policy_join(command))
        self.add('scope', {'decisionRef': next(e['data']['decisionRef'] for e in reversed(self.events()) if e['kind'] == 'scope'),
            'acceptance': [{'id': 'OTHER', 'text': 'Additional fixture evidence'}]})
        self.assertFalse(self.policy_join(command))
        self.assertEqual(self.launches, [])

    def test_policy_join_rejects_command_variants_and_diagnostic_fallback(self):
        command = self.policy_command()
        self.policy_requirement(command)
        for changes in ({'commit': self.base}, {'source': 'worker'},
                {'source': 'feature-lead'}, {'outcome': 'fail'},
                {'command': ' '.join(command)}):
            with self.subTest(changes=changes):
                self.policy_pass(command, **changes)
                self.assertFalse(self.policy_join(command))
        # The actual record schema refuses nonconcrete evidence before the
        # current-record join; detached check dictionaries are not accepted.
        for missing in ('', None, {'unknown': 'No fixture log'}):
            with self.assertRaises(ValueError):
                self.policy_pass(command, evidencePath=missing)
        self.policy_pass(command)
        self.assertTrue(self.policy_join(command))
        changed_local = self.policy_command(local=self.temp / 'other-local')
        self.assertFalse(self.policy_join(changed_local))
        self.assertFalse(publish._policy_verify_check(self.local, REPO, 'WORK', self.base, command))
        for index, replacement in ((6, str(self.temp / 'other-repo')), (8, self.head + '..' + self.head),
                (10, 'other/topic'), (12, str(self.temp / 'other-policy'))):
            changed = command.copy()
            changed[index] = replacement
            self.assertFalse(self.policy_join(changed))
        for changed in (' '.join(command), tuple(command), command[:-1], command + ['--extra'],
                [*command[:3], 'verify', 'privacy', *command[5:]],
                [*command[:2], str(self.local / 'x/..'), *command[3:]]):
            with self.subTest(command=changed), self.assertRaises(cli.CommandError):
                self.policy_join(changed)
        pr = self.policy_command(text_files=[('title', self.temp / 'title'), ('body', self.temp / 'body')])
        self.policy_requirement(pr)
        self.policy_pass(pr)
        self.assertTrue(self.policy_join(pr))
        for index in (14, 16):
            changed = pr.copy()
            changed[index] += '-changed'
            self.assertFalse(self.policy_join(changed))
        with self.assertRaises(cli.CommandError):
            self.policy_join(pr[:13] + pr[15:] + pr[13:15])

    def test_policy_migration_keeps_raw_failure_and_requires_fresh_check(self):
        raw = self.policy_pass('fixture-check', outcome='fail')
        command = self.policy_command()
        old = self.policy_pass(command)
        self.policy_requirement(command)
        self.assertFalse(self.policy_join(command))
        self.policy_pass(command)
        self.assertTrue(self.policy_join(command))
        events = self.events()
        self.assertEqual(events[raw['seq'] - 1], raw)
        self.assertEqual(events[old['seq'] - 1], old)
        # A changed requirement invalidates earlier matching observations, even
        # if the same command later returns under a supported scope decision.
        self.policy_requirement('fixture-replacement')
        self.policy_requirement(command)
        self.assertFalse(self.policy_join(command))
        self.policy_pass(command)
        self.assertTrue(self.policy_join(command))
        self.add('lead-assignment', {'thread': 'new-fixture-lead', 'assignedBy': WRITER,
                 'date': '2026-11-01', 'reason': 'Fixture reassignment'})
        self.assertFalse(self.policy_join(command))

    def test_shared_scan_uses_actual_push_set_and_work_endpoints(self):
        # The remote already has the reviewed tip: the push set is empty,
        # although the work review range is not.
        self.refs[REF] = self.head
        scanner = publish.privacy.scan_commits
        with patch.object(publish.privacy, 'scan_commits', wraps=scanner) as scan:
            self.assertEqual(self.invoke().code, 0)
        self.assertTrue(scan.called)
        args, options = scan.call_args
        self.assertEqual(args, (self.repo, self.local, []))
        self.assertEqual(options, dict(base=self.base, head=self.head,
                                      branch=self.args.branch, texts=()))

    def test_late_push_preparation_changes_refuse_without_mint(self):
        original = publish._status_path
        for change in ('head', 'refs', 'evidence', 'selection', 'work'):
            with self.subTest(change=change):
                # Each subcase has an independent guarded synthetic fixture.
                def changed(*a):
                    path = original(*a)
                    if change == 'head':
                        self.git('checkout', '--detach', self.base)
                    elif change == 'refs':
                        self.refs['refs/tags/late'] = self.head
                    elif change == 'evidence':
                        self.add('check', {'checkId': 'SUITE', 'command': 'fixture-check',
                            'commit': self.head, 'outcome': 'fail', 'source': 'manager',
                            'evidencePath': 'fixture-failed.log'})
                    elif change == 'work':
                        self.add('lead-assignment', {'thread': 'fixture-new-lead',
                            'assignedBy': WRITER, 'date': '2026-11-01', 'reason': 'Fixture'})
                    return path
                with patch.object(publish, '_status_path', side_effect=changed):
                    if change == 'selection':
                        selected = publish._publication_set
                        calls = []
                        def changed_set(*a):
                            calls.append(1)
                            value = selected(*a)
                            return value if len(calls) == 1 else []
                        with patch.object(publish, '_publication_set', side_effect=changed_set):
                            result = self.invoke()
                    else:
                        result = self.invoke()
                self.assertEqual(result.data['outcome'], 'refused')
                self.assertFalse(self.launches)
                self.assertNotIn('statusSummary', self.events()[-1]['data'])
                # Restore only synthetic fixture state for the next subcase.
                self.git('checkout', '--detach', self.head)
                self.refs = {'refs/heads/main': self.base}
                self.cover(self.head)

    def test_shared_scanner_failures_never_mint(self):
        for kind in ('finding', 'binary', 'unsupported', 'read-error'):
            with self.subTest(kind=kind):
                kwargs = ({'return_value': {'ok': False}} if kind == 'finding' else
                          {'side_effect': cli.CommandError('privacy input refused')})
                with patch.object(publish.privacy, 'scan_commits', **kwargs) as scan:
                    if kind == 'finding':
                        self.assertEqual(self.invoke().data['outcome'], 'refused')
                    else:
                        with self.assertRaises(cli.CommandError):
                            self.invoke()
                self.assertTrue(scan.called)
                self.assertFalse(self.launches)

    def test_real_binary_and_gitlink_refuse_without_mint(self):
        original = self.head
        for kind in ('binary', 'gitlink'):
            with self.subTest(kind=kind):
                if kind == 'binary':
                    (self.repo / 'opaque.bin').write_bytes(b'\x00\xff')
                    self.git('add', '.')
                else:
                    self.git('update-index', '--add', '--cacheinfo',
                             '160000,' + self.base + ',external')
                self.git('commit', '-qm', 'Unsupported fixture')
                self.head = self.args.head = self.git('rev-parse', 'HEAD').strip()
                self.cover(self.head)
                with self.assertRaises(cli.CommandError):
                    self.invoke()
                self.assertFalse(self.launches)
                self.git('reset', '--hard', original)
                self.head = self.args.head = original

    def test_late_gate_scanner_workflow_and_error_refuse_without_mint(self):
        scanner, git = publish.privacy.scan_commits, publish._git
        scans = []
        def scanned(*a, **kw):
            scans.append(1)
            return scanner(*a, **kw)
        def workflow(repo, *a, **kw):
            if len(scans) == 2 and a[0] == 'diff-tree':
                return b'.github/workflows/fixture.lock\0'
            return git(repo, *a, **kw)
        with patch.object(publish.privacy, 'scan_commits', side_effect=scanned), \
                patch.object(publish, '_git', side_effect=workflow):
            result = self.invoke()
        self.assertEqual((result.code, result.data['outcome']), (5, 'refused'))
        self.assertFalse(self.launches)
        self.assertNotIn('statusSummary', self.events()[-1]['data'])

    def test_independent_workflow_walk_covers_lock_root_and_merge(self):
        path = self.repo / '.github/workflows/fixture.lock'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\x00PRIVATE_SENTINEL')
        self.git('add', '.')
        self.git('commit', '-qm', 'Workflow lock fixture')
        workflow = self.git('rev-parse', 'HEAD').strip()
        # Lock contents are excluded by the scanner, never by the workflow guard.
        self.assertEqual(publish._screen(self.repo, self.local, self.args.branch,
            [workflow], base=self.base, head=workflow).code, 5)
        self.git('checkout', '-qb', 'fixture-side', self.base)
        self.git('merge', '--no-ff', workflow, '-qm', 'Merge workflow fixture')
        merge = self.git('rev-parse', 'HEAD').strip()
        for sha in (merge, workflow):
            self.assertEqual(publish._screen(self.repo, self.local, self.args.branch,
                [sha], base=self.base, head=merge).code, 5)
        # A root workflow is also a transition, even without parents.
        self.git('checkout', '--orphan', 'fixture-root')
        self.git('commit', '-qm', 'Root workflow fixture')
        root = self.git('rev-parse', 'HEAD').strip()
        self.assertEqual(publish._screen(self.repo, self.local, self.args.branch,
            [root], base=root, head=root).code, 5)

    def test_success_entry_and_cd6_push_consumer(self):
        code, out, err = self.entry()
        self.assertEqual(code, 0)
        self.assertEqual(err, '')
        self.assertEqual(json.loads(out)['outcome'], 'succeeded')
        self.assertNotIn('PRIVATE_SENTINEL', out + err)
        self.assertEqual(self.launches, [['git', 'push', '--force-with-lease=' + REF + ':',
                                         URL, self.head + ':' + REF]])
        intent, result = self.events()[-2:]
        self.assertEqual((intent['kind'], intent['data']['expectedPriorHead'], result['kind'],
                          result['data']['intentRef']), ('op-intent', 'absent', 'op-result', intent['seq']))
        self.assertEqual(intent['helperCommit'], 'e' * 40)
        self.assertIsNone(evidence._result_gap(result, self.head))
        pair = evidence.check(self.local, REPO, 'WORK', self.head, pr_ready=True)
        self.assertEqual([g['code'] for g in pair['gaps']], ['pr-missing'])

    def test_check_then_intent_then_result(self):
        order, check, append = [], evidence.check, publish._append
        def checked(*args, **kwargs):
            order.append('check')
            return check(*args, **kwargs)
        def appended(local, repo, work, kind, data):
            order.append(kind)
            return append(local, repo, work, kind, data)
        with patch.object(evidence, 'check', checked), patch.object(publish, '_append', appended):
            self.assertEqual(self.invoke().code, 0)
        self.assertEqual(order, ['check', 'op-intent', 'check', 'op-result'])

    def test_noop_without_child_evidence(self):
        self.refs[REF] = self.head
        result = self.invoke()
        self.assertEqual(result.data['outcome'], 'no-op')
        self.assertEqual(self.launches, [])
        self.assertNotIn('statusSummary', self.events()[-1]['data'])

    def test_unrelated_operations_cannot_authorize_push(self):
        self.refs[REF] = self.base
        for operation in ('other', 'pull-request'):
            with self.subTest(operation=operation):
                intent = self.add('op-intent', {'opId': 'fixture-' + operation,
                    'operation': operation, 'target': URL + '#' + REF,
                    'commit': self.base, 'expectedPriorHead': None})
                self.add('op-result', {'intentRef': intent['seq'],
                    'opId': 'fixture-' + operation, 'operation': operation,
                    'target': URL + '#' + REF, 'commit': self.base,
                    'outcome': 'no-op', 'confirmation': 'Fixture unrelated operation'})
                before = self.ops()
                result = self.invoke()
                self.assertEqual((result.code, result.data['outcome']), (1, 'refused'))
                self.assertEqual((self.ops(), self.launches), (before, []))

    def test_untracked_helper_entry(self):
        (self.repo / 'untracked-note.txt').write_text('Fixture private note')
        with patch.object(cli, 'ROOT', self.repo), \
                patch.object(cli, 'helper_info', self.real_helper_info):
            self.assertTrue(cli.helper_info()['dirty'])
            self.assertEqual(self.git('status', '--porcelain', '--untracked-files=no'), '')
            code, out, err = self.entry()
        self.assertEqual((code, json.loads(out)['outcome'], err), (0, 'succeeded', ''))
        self.assertEqual(len(self.launches), 1)

    def test_dirty_helper_entry(self):
        for change in ('unstaged', 'staged', 'deleted'):
            with self.subTest(change=change):
                self.git('reset', '--hard', self.head)
                path = self.repo / 'file.txt'
                if change == 'deleted':
                    path.unlink()
                else:
                    path.write_text('Fixture tracked change')
                if change == 'staged':
                    self.git('add', 'file.txt')
                with patch.object(cli, 'ROOT', self.repo), \
                        patch.object(cli, 'helper_info', self.real_helper_info):
                    code, out, err = self.entry()
                self.assertEqual((code, json.loads(out)['outcome'], err), (1, 'refused', ''))
                self.assertEqual((self.ops(), self.launches), ([], []))

    def test_invalid_input_entry(self):
        for field, value in [('head', '--bad'), ('branch', '-bad'), ('work', '../bad')]:
            with self.subTest(field=field), patch.object(self.args, field, value):
                with self.assertRaises(cli.CommandError):
                    self.invoke()
        self.args.head = 'invalid'
        code, out, err = self.entry()
        self.assertEqual((code, out), (2, ''))
        self.assertNotIn('invalid\n', err)

    def test_config_and_identity_refused(self):
        (self.local / 'github-app.json').write_text('{}')
        code, out, err = self.entry()
        self.assertEqual((code, out), (3, ''))
        self.assertNotIn(str(self.local), err)

    def test_mismatched_origin(self):
        self.git('config', 'remote.origin.url', 'https://github.com/fixture-org/other.git')
        with self.assertRaises(cli.CommandError):
            self.invoke()
        self.assertFalse(self.launches)

    def test_checkout_must_be_the_reviewed_head_at_its_root(self):
        (self.repo / 'sub').mkdir()
        for field, value in [('head', self.base), ('repo_path', str(self.repo / 'sub'))]:
            with self.subTest(field=field), patch.object(self.args, field, value):
                with self.assertRaises(cli.CommandError):
                    self.invoke()
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_stale_checks_and_current_lead_coverage(self):
        self.add('check', {'checkId': 'SUITE', 'command': 'fixture-check', 'commit': self.head,
            'outcome': 'fail', 'source': 'manager', 'evidencePath': 'fixture.log'})
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_lead_review_and_reassignment(self):
        self.cover(self.head, lead=True)
        self.assertEqual(self.invoke().code, 0)
        self.add('lead-assignment', {'thread': 'fixture-new-lead', 'assignedBy': WRITER,
            'date': '2026-11-01', 'reason': 'Fixture reassignment'})
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual(len(self.launches), 1)

    def advance(self, text='public update', message='Fixture update'):
        (self.repo / 'file.txt').write_text(text)
        self.git('add', '.')
        self.git('commit', '-qm', message)
        self.head = self.git('rev-parse', 'HEAD').strip()
        self.args.head = self.head

    def test_ff_update_requires_fresh_evidence_and_explicit_prior(self):
        self.assertEqual(self.invoke().code, 0)
        prior = self.head
        self.advance()
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.cover(self.head)
        self.assertEqual(self.invoke().code, 0)
        self.assertEqual(self.launches[-1][2], '--force-with-lease=' + REF + ':' + prior)

    def test_privacy_on_update(self):
        self.assertEqual(self.invoke().code, 0)
        self.advance('PRIVATE_SENTINEL')
        self.cover(self.head)
        result = self.invoke()
        self.assertEqual(result.data['outcome'], 'refused')
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result.data))
        self.assertEqual((self.ops(), len(self.launches)), (['op-intent', 'op-result'], 1))

    def test_branch_privacy(self):
        self.args.branch = 'PRIVATE_SENTINEL'
        result = self.invoke()
        self.assertEqual(result.code, 1)
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result.data))
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_every_selected_commit_message_path_and_content(self):
        start = self.head
        for field in ('content', 'message', 'path'):
            with self.subTest(field):
                self.git('reset', '-q', '--hard', start)
                name = 'PRIVATE_SENTINEL.txt' if field == 'path' else 'file.txt'
                (self.repo / name).write_text('PRIVATE_SENTINEL' if field == 'content' else 'public edit')
                self.git('add', '.')
                self.git('commit', '-qm', 'PRIVATE_SENTINEL' if field == 'message' else 'Fixture edit')
                self.advance('public again')  # the private commit is not the head
                self.cover(self.head)
                self.assertEqual(self.invoke().code, 1)
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_every_advertised_ref_excludes_its_old_private_commit(self):
        start = self.head
        for number, name in enumerate(['refs/tags/fixture-publication', 'HEAD', 'refs/pull/7/head']):
            with self.subTest(name):
                # Rebuilt commits can be byte-identical, so advertise only this ref.
                self.refs = {'refs/heads/main': self.base}
                self.args.branch = f'fixture/excluded-{number}'
                self.git('reset', '-q', '--hard', start)
                self.advance('public', 'PRIVATE_SENTINEL')
                self.refs[name] = self.head
                if name.startswith('refs/tags/'):  # an annotated tag advertises its own object
                    self.git('tag', '-a', '-m', 'Fixture tag', 'fixture-publication')
                    self.refs[name] = self.git('rev-parse', 'fixture-publication').strip()
                self.advance('public next')
                self.cover(self.head)
                self.assertEqual(self.invoke().code, 0)

    def test_workflow_change_exit5(self):
        workflow = self.repo / '.github/workflows/fixture.yml'
        workflow.parent.mkdir(parents=True)
        workflow.write_text('name: fixture\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture workflow')
        self.head = self.args.head = self.git('rev-parse', 'HEAD').strip()
        self.cover(self.head)
        code, out, err = self.entry()
        self.assertEqual(code, 5)
        self.assertIn('Shawn', json.loads(out)['reason'])
        self.assertEqual(err, '')
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_unexpected_remote_and_other_branch_prior(self):
        self.refs[REF] = self.base
        self.assertEqual(self.invoke().code, 1)
        # A confirmed push of another branch never makes its head this branch's prior.
        self.args.branch = 'fixture/other'
        self.refs.pop(REF)
        self.assertEqual(self.invoke().code, 0)
        prior = self.head
        self.advance()
        self.cover(self.head)
        self.args.branch = 'fixture/topic'
        self.refs[REF] = prior
        self.assertEqual(self.invoke().code, 1)
        self.assertEqual(len(self.launches), 1)

    def test_remote_race_records_refusal(self):
        calls = 0
        def remote(repo, url):
            nonlocal calls
            calls += 1
            return dict(self.refs, **({REF: self.base} if calls > 1 else {}))
        with patch.object(publish, '_remote', side_effect=remote):
            self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertFalse(self.launches)
        self.assertEqual(self.events()[-1]['kind'], 'op-result')

    def test_nonff_refused(self):
        self.assertEqual(self.invoke().code, 0)
        self.git('checkout', '--detach', self.base)
        self.advance('divergent')
        self.cover(self.head)
        result = self.invoke()
        self.assertEqual((result.code, result.data['outcome']), (1, 'refused'))
        self.assertIn('non-fast-forward', result.data['reason'])
        self.assertEqual((self.ops(), len(self.launches)), (['op-intent', 'op-result'], 1))

    def test_missing_advertised_object_and_shallow_fail_closed(self):
        self.refs['refs/heads/unknown'] = 'f' * 40
        with self.assertRaises(cli.CommandError):
            self.invoke()
        self.refs.pop('refs/heads/unknown')
        (self.repo / '.git/shallow').write_text(self.base + '\n')
        with self.assertRaises(cli.CommandError):
            self.invoke()
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_child_outcome_matrix(self):
        cases = [token_run.RunResult(False, None, 'mint-failed', None, 1),
                 token_run.RunResult(True, 1, None, 'ok', 1),
                 token_run.RunResult(True, None, None, None, 1)]
        for result in cases:
            with self.subTest(result=result):
                # A distinct branch keeps unresolved attempts from other rows isolated.
                self.args.branch = 'fixture/case-' + str(cases.index(result))
                def child(argv, path, *, local_dir):
                    jsonio.atomic_replace(path, result.record())
                    self.refs['refs/heads/' + self.args.branch] = self.head
                    return result
                with patch.object(token_run, 'run_command', side_effect=child):
                    actual = self.invoke()
                expected = 'succeeded' if result.child_status == 0 else (
                    'unknown' if result.child_started and result.child_status is None else
                    'refused' if not result.child_started else 'failed')
                self.assertEqual(actual.data['outcome'], expected)
                if expected == 'unknown':
                    break  # Retained uncertainty correctly blocks all later branch attempts.

    def test_revocation_failure_retains_truth(self):
        def child(argv, path, *, local_dir):
            result = token_run.RunResult(True, 0, None, 'failed', 0)
            self.refs[REF] = self.head
            jsonio.atomic_replace(path, result.record())
            return result
        with patch.object(token_run, 'run_command', side_effect=child):
            result = self.invoke()
        self.assertEqual((result.code, result.data['outcome']), (0, 'succeeded'))
        self.assertIn('revocation failed', result.data['reason'])
        self.assertEqual(self.events()[-1]['data']['statusSummary']['revocation'], 'failed')
        self.assertIsNone(evidence._result_gap(self.events()[-1], self.head))

    def test_missing_status_does_not_prove_started(self):
        with patch.object(token_run, 'run_command', return_value=token_run.RunResult(True, 0, None, 'ok', 0)):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertTrue(records.uncertain_items(self.events()))

    def test_unknown_resume_no_second_push(self):
        def interrupted(argv, path, *, local_dir):
            self.launches.append(argv)
            self.refs[REF] = self.head
            jsonio.atomic_replace(path, token_run.RunResult(True, 0, None, 'ok', 0).record())
            raise InterruptedError('PRIVATE_SENTINEL')
        with patch.object(token_run, 'run_command', side_effect=interrupted):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertEqual(self.invoke().data['outcome'], 'succeeded')
        self.assertEqual(len(self.launches), 1)

    def test_true_null_stays_uncertain_without_second_push(self):
        def interrupted(argv, path, *, local_dir):
            self.launches.append(argv)
            jsonio.atomic_replace(path, {'childStarted': True, 'childStatus': None})
            raise InterruptedError()
        with patch.object(token_run, 'run_command', side_effect=interrupted):
            self.invoke()
        self.refs[REF] = self.head
        self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertEqual(len(self.launches), 1)

    def test_missing_or_wrong_head_confirmation_is_failed(self):
        for name, remote in [('missing', None), ('wrong', self.base)]:
            def child(argv, path, *, local_dir):
                result = token_run.RunResult(True, 0, None, 'ok', 0)
                jsonio.atomic_replace(path, result.record())
                if remote:
                    self.refs[argv[-1].partition(':')[2]] = remote
                return result
            with self.subTest(name), patch.object(token_run, 'run_command', side_effect=child):
                self.args.branch = 'fixture/' + name
                self.assertEqual(self.invoke().data['outcome'], 'failed')
                self.assertIsNotNone(evidence._result_gap(self.events()[-1], self.head))

    def test_result_persistence_failure_leaves_uncertainty_then_reconciles(self):
        original = publish._append
        def append(local, repo, work, kind, data):
            if kind == 'op-result':
                raise records.RecordError('PRIVATE_SENTINEL')
            return original(local, repo, work, kind, data)
        with patch.object(publish, '_append', side_effect=append):
            with self.assertRaises(cli.CommandError):
                self.invoke()
        self.assertTrue(records.uncertain_items(self.events()))
        self.assertEqual(self.invoke().code, 0)
        self.assertEqual(len(self.launches), 1)

    def test_malformed_actual_result(self):
        def child(argv, path, *, local_dir):
            jsonio.atomic_replace(path, {'childStarted': True, 'childStatus': 0, 'revocation': 'ok'})
            self.refs[REF] = self.head
            return SimpleNamespace(child_started=True, child_status=0, code=0)
        with patch.object(token_run, 'run_command', side_effect=child):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertTrue(records.uncertain_items(self.events()))

    def test_postpush_confirmation_read_error_is_unknown(self):
        counter = 0
        def remote(repo, url):
            nonlocal counter
            counter += 1
            if self.launches:
                raise cli.CommandError('synthetic unavailable')
            return dict(self.refs)
        with patch.object(publish, '_remote', side_effect=remote):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertTrue(records.uncertain_items(self.events()))
        self.assertEqual(self.invoke().code, 0)
        self.assertEqual(len(self.launches), 1)

    def test_absent_status_resume_is_no_start(self):
        with patch.object(token_run, 'run_command', side_effect=InterruptedError()):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual(self.events()[-1]['data']['statusSummary'],
                         {'childStarted': False, 'refusal': 'missing-status'})
        self.assertFalse(self.launches)

    def test_another_branch_cannot_resolve_unknown(self):
        with patch.object(token_run, 'run_command', side_effect=InterruptedError()):
            self.invoke()
        self.args.branch = 'fixture/another'
        self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertFalse(self.launches)

    def test_late_result_does_not_replace_latest_intent(self):
        self.assertEqual(self.invoke().code, 0)
        first = [e for e in self.events() if e['kind'] == 'op-intent'][-1]
        second = self.add('op-intent', {'opId': 'fixture-later', 'operation': 'push',
            'target': first['data']['target'], 'commit': self.head, 'expectedPriorHead': self.head})
        late = dict(self.events()[-2]['data'])
        late['intentRef'] = first['seq']
        self.add('op-result', late)
        loaded = records.load_work(self.local, REPO, 'WORK')
        current, result = publish._latest(loaded, first['data']['target'])
        self.assertEqual(current['seq'], second['seq'])
        self.assertIsNone(result)
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual(len(self.launches), 1)

    def test_intent_persistence_failure_starts_nothing(self):
        with patch.object(publish, '_append', side_effect=records.RecordError('fixture failure')):
            with self.assertRaises(cli.CommandError):
                self.invoke()
        self.assertFalse(self.launches)
        self.assertFalse(any(e['kind'] == 'op-intent' for e in self.events()))

    def test_unsafe_config_no_publish(self):
        refused, watched = 'unsafe Git configuration', 'unsafe publication overrides'
        for key, value, expected in [
                ('url.https://example.invalid/.insteadOf', 'https://github.com/', refused),
                ('http.extraHeader', 'fixture header', refused),
                ('push.followTags', 'true', watched), ('core.hooksPath', '/fixture/hooks', watched),
                ('protocol.ssh.allow', 'always', watched),
                ('http.https://github.com/.followRedirects', 'true', watched)]:
            with self.subTest(key=key):
                self.git('config', key, value)
                try:
                    shown = self.invoke().data['reason']
                except cli.CommandError as error:
                    shown = str(error)
                finally:
                    self.git('config', '--unset-all', key)
                self.assertIn(expected, shown)
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_controlled_values_are_allowed_from_any_source(self):
        for key, value in publish._SAFE:
            self.git('config', key, value)
        self.assertEqual(self.invoke().code, 0)

    def test_selected_set_includes_merge_side_history(self):
        self.git('checkout', '-qb', 'fixture/side', self.base)
        (self.repo / 'side.txt').write_text('PRIVATE_SENTINEL')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture side')
        side = self.git('rev-parse', 'HEAD').strip()
        self.git('checkout', '--detach', self.head)
        self.git('merge', '--no-ff', '-qm', 'Fixture merge', side)
        self.head = self.args.head = self.git('rev-parse', 'HEAD').strip()
        self.cover(self.head)
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertFalse(self.launches)

    def test_retry_update_retains_last_confirmed_prior(self):
        self.assertEqual(self.invoke().code, 0)
        prior = self.head
        self.advance('public correction')
        self.cover(self.head)
        def refused(argv, path, *, local_dir):
            result = token_run.RunResult(False, None, 'mint-failed', None, 1)
            jsonio.atomic_replace(path, result.record())
            return result
        with patch.object(token_run, 'run_command', side_effect=refused):
            self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual(self.invoke().code, 0)
        self.assertEqual(self.launches[-1][2], '--force-with-lease=' + REF + ':' + prior)
        self.assertEqual(self.events()[-1]['helperCommit'], 'e' * 40)

    def test_included_publication_override_refused(self):
        included = self.temp / 'included-git-config'
        included.write_text('[push]\n followTags = true\n')
        self.git('config', 'include.path', str(included))
        with self.assertRaisesRegex(cli.CommandError, 'unsafe publication overrides'):
            self.invoke()
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_ancestry_read_error_is_distinct_from_negative(self):
        self.assertEqual(self.invoke().code, 0)
        self.advance()
        self.cover(self.head)
        original = process.run
        def run(argv, **kw):
            if argv[1:3] == ['merge-base', '--is-ancestor']:
                return subprocess.CompletedProcess(argv, 128, b'', b'PRIVATE_SENTINEL')
            return original(argv, **kw)
        with patch.object(process, 'run', side_effect=run):
            with self.assertRaises(cli.CommandError) as error:
                self.invoke()
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))

    def test_late_included_config_refuses_without_launch(self):
        remote = self.remote
        calls = []
        def changed(*args):
            calls.append(1)
            if len(calls) == 2:
                extra = self.repo / '.git/late-config'
                extra.write_text('[core]\n hooksPath = /synthetic/hooks\n')
                self.git('config', 'include.path', str(extra))
            return remote(*args)
        with patch.object(publish, '_remote', side_effect=changed):
            result = self.invoke()
        self.assertEqual((result.code, result.data['outcome']), (1, 'refused'))
        self.assertIn('no child launched', result.data['reason'])
        self.assertEqual(self.ops(), ['op-intent', 'op-result'])
        self.assertNotIn('statusSummary', self.events()[-1]['data'])
        self.assertFalse(self.launches)

    def test_validated_refusal_kind_is_public_but_backend_is_not(self):
        def refused(argv, path, **kw):
            result = token_run.RunResult(False, None, 'git-extraheader', None, 1)
            jsonio.atomic_replace(path, result.record())
            return result
        with patch.object(token_run, 'run_command', side_effect=refused):
            result = self.invoke()
        self.assertIn('refusal git-extraheader', result.data['reason'])
        self.assertIn('coordinator must repair', result.data['reason'])
        self.assertEqual(self.events()[-1]['data']['statusSummary'],
                         {'childStarted': False, 'refusal': 'git-extraheader'})
        def unknown(argv, path, **kw):
            result = token_run.RunResult(False, None, 'PRIVATE_SENTINEL', None, 1)
            jsonio.atomic_replace(path, result.record())
            return result
        with patch.object(token_run, 'run_command', side_effect=unknown):
            result = self.invoke()
        self.assertEqual(result.data['outcome'], 'unknown')
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result.data))
        self.assertIn('reconcile', result.data['reason'])


class OfflinePublishAdapters(OfflineTestCase):
    def test_push_is_registered(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertIn(('publish', 'push'), cli.command_names())

    def test_private_git_config_holds_only_controlled_values(self):
        cwd, env = Path.cwd(), dict(os.environ)
        with publish._environment(self.temp):
            config = Path(os.environ['HOME'], '.gitconfig')
            self.assertEqual(os.environ['GIT_CONFIG_NOSYSTEM'], '1')
            self.assertEqual(stat.S_IMODE(config.parent.stat().st_mode), 0o700)
            listed = process.run(['git', 'config', '--file', str(config), '--list'],
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(listed.splitlines(), [f'{k.lower()}={v}' for k, v in publish._SAFE])
        self.assertEqual((Path.cwd(), dict(os.environ)), (cwd, env))
        self.assertFalse(config.exists())

    def listing(self, raw):
        return FakeRunner({'git': lambda argv, **kw: subprocess.CompletedProcess(argv, 0, raw, b'')})

    def test_remote_parses_head_tags_and_peeled_rows_with_controlled_settings(self):
        rows = [('a', 'HEAD'), ('b', 'refs/tags/v1'), ('c', 'refs/tags/v1^{}'), ('d', 'refs/pull/7/head')]
        runner = self.listing(''.join(f'{sha * 40}\t{name}\n' for sha, name in rows).encode())
        with patch.object(process, 'run', runner):
            refs = publish._remote(Path.cwd(), URL)
        self.assertEqual(refs, {name: sha * 40 for sha, name in rows})
        # HEAD is not a ref spelling; a peeled row is validated as its tag name.
        self.assertEqual([call[0][-1] for call in runner.calls[1:]],
                         ['refs/tags/v1', 'refs/tags/v1', 'refs/pull/7/head'])
        argv, kwargs = runner.calls[0]
        env = kwargs['env']
        settings = {env[f'GIT_CONFIG_KEY_{i}']: env[f'GIT_CONFIG_VALUE_{i}']
                    for i in range(int(env['GIT_CONFIG_COUNT']))}
        self.assertEqual((argv, settings['credential.helper'], settings['protocol.ssh.allow']),
                         (['git', 'ls-remote', '--', URL], '', 'never'))

    def test_remote_reads_a_real_git_listing(self):
        def git(cwd, *args):
            return process.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                *args], cwd=cwd, capture_output=True, text=True, check=True).stdout
        bare, work = self.temp / 'remote.git', self.temp / 'work'
        git(self.temp, 'init', '-q', str(work))
        git(work, 'commit', '-q', '--allow-empty', '-m', 'Fixture')
        git(work, 'tag', '-a', '-m', 'Fixture tag', 'v1')
        git(work, 'branch', '-m', 'topic')
        git(self.temp, 'clone', '-q', '--bare', '--no-hardlinks', str(work), str(bare))
        git(bare, 'symbolic-ref', 'HEAD', 'refs/heads/topic')
        refs = publish._remote(work, str(bare))
        self.assertEqual(set(refs), {'HEAD', 'refs/heads/topic', 'refs/tags/v1', 'refs/tags/v1^{}'})
        self.assertEqual({refs['HEAD'], refs['refs/tags/v1^{}']}, {refs['refs/heads/topic']})
        self.assertNotEqual(refs['refs/tags/v1'], refs['refs/heads/topic'])  # the tag object itself

    def test_remote_malformed_and_reader_errors(self):
        row = b'f' * 40 + b'\trefs/heads/x'
        for raw in (b'bad\n', (row + b'\n') * 2, row, b'f' * 40 + b'\t--bad\n',
                    b'f' * 40 + '\trefs/heads/é\n'.encode(), b'f' * 40 + b'\trefs/heads/x^{}\n'):
            with self.subTest(raw=raw), patch.object(process, 'run', self.listing(raw)):
                with self.assertRaises(cli.CommandError):
                    publish._remote(Path.cwd(), URL)
        with patch.object(process, 'run', side_effect=process.ProcessError('timeout')):
            with self.assertRaises(cli.CommandError):
                publish._remote(Path.cwd(), URL)


class OfflinePublishPR(OfflineTestCase):
    setUp = OfflinePublish.setUp
    git = OfflinePublish.git
    add = OfflinePublish.add
    create_work = OfflinePublish.create_work
    native = OfflinePublish.native
    cover = OfflinePublish.cover
    remote = OfflinePublish.remote
    run_child = OfflinePublish.run_child
    events = OfflinePublish.events
    ops = OfflinePublish.ops
    # Reuse the guarded fixture, without repeating push cases for the PR handler.
    def test_pr_registered(self):
        self.assertIn(('publish', 'pr'), cli.command_names())

    def setUp(self):
        OfflinePublish.setUp(self)
        self.args.base = 'main'
        self.args.title_file = self.temp / 'title.txt'
        self.args.body_file = self.temp / 'body.txt'
        self.args.title_file.write_text('Fixture title')
        self.args.body_file.write_text('Fixture body\n')
        self.prs, self.reads = [], []
        self.original_run = process.run
        self.enterContext(patch.object(process, 'run', side_effect=self.read))
        self.enterContext(patch.object(token_run, 'run_command', side_effect=self.create))

    def row(self):
        return {'number': 7, 'head': {'sha': self.head, 'ref': self.args.branch,
                'repo': {'full_name': 'fixture-org/publish-fixture'}},
                'base': {'ref': 'main', 'repo': {'full_name': 'fixture-org/publish-fixture'}},
                'state': 'open', 'draft': False, 'user': {'login': 'fixture-worker[bot]'}}

    def read(self, argv, **kw):
        if argv[0] != 'gh':
            return self.original_run(argv, **kw)
        self.assertEqual(argv[:6], ['gh', 'api', '--hostname', 'github.com', '--method', 'GET'])
        self.assertEqual(Path.cwd(), self.repo)
        self.reads.append(argv)
        data = self.prs if '?' in argv[-1] else self.prs[0]
        return subprocess.CompletedProcess(argv, 0, json.dumps(data).encode(), b'PRIVATE_SENTINEL')

    def create(self, argv, path, *, local_dir):
        self.assertEqual(Path.cwd(), self.repo)
        self.assertEqual(local_dir, self.local)
        self.assertFalse(path.exists())
        self.assertEqual(self.events()[-1]['kind'], 'op-intent')
        self.assertTrue(all(os.path.samestat(os.fstat(fd), os.stat(os.devnull)) for fd in (1, 2)))
        self.assertEqual(argv[:10], ['gh', 'pr', 'create', '--repo', 'fixture-org/publish-fixture',
                                   '--head', self.args.branch, '--base', 'main', '--title=Fixture title'])
        self.assertEqual(argv[10], '--body-file')
        body = Path(argv[11])
        self.assertEqual(body.read_bytes(), b'Fixture body\n')
        self.assertEqual(stat.S_IMODE(body.stat().st_mode), 0o600)
        self.assertNotEqual(os.environ['HOME'], str(self.temp / 'home'))
        self.assertFalse(any(k in os.environ for k in ('GH_TOKEN', 'GITHUB_TOKEN')))
        self.launches.append(argv)
        self.prs = [self.row()]
        result = token_run.RunResult(True, 0, None, 'ok', 0)
        jsonio.atomic_replace(path, result.record())
        os.write(1, b'PRIVATE_SENTINEL arbitrary URL\n')
        return result

    def invoke(self):
        cwd, env = Path.cwd(), dict(os.environ)
        descriptors = [os.fstat(fd) for fd in (1, 2)]
        try:
            os.chdir(self.repo)
            return publish.pr(self.args)
        finally:
            self.assertEqual(Path.cwd(), self.repo)
            os.chdir(cwd)
            self.assertEqual(dict(os.environ), env)
            self.assertTrue(all(map(os.path.samestat, descriptors, [os.fstat(fd) for fd in (1, 2)])))

    def entry(self):
        argv = ['t3c', '--local-dir', str(self.local), 'publish', 'pr']
        for name in ('work', 'head', 'branch', 'base', 'title_file', 'body_file'):
            argv.extend(['--' + name.replace('_', '-'), str(getattr(self.args, name))])
        out, err, cwd = io.StringIO(), io.StringIO(), Path.cwd()
        try:
            os.chdir(self.repo)
            with patch.object(sys, 'argv', argv), redirect_stdout(out), redirect_stderr(err):
                with self.assertRaises(SystemExit) as exited:
                    runpy.run_path(str(ROOT / 'bin/t3c'), run_name='__main__')
        finally:
            os.chdir(cwd)
        return exited.exception.code, out.getvalue(), err.getvalue()

    def pair(self):
        return evidence.check(self.local, REPO, 'WORK', self.head, pr_ready=True)

    def push_pair(self, noop=False):
        if noop:
            self.refs[REF] = self.head
        with patch.object(token_run, 'run_command', side_effect=self.run_child):
            self.assertEqual(publish.push(self.args).code, 0)
        self.launches.clear()

    def test_pr_workflow_range_refuses_without_mint(self):
        path = self.repo / '.github/workflows/fixture.lock'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\x00')
        self.git('add', '.')
        self.git('commit', '-qm', 'Workflow fixture')
        self.head = self.args.head = self.git('rev-parse', 'HEAD').strip()
        self.cover(self.head)
        self.assertEqual(self.invoke().code, 5)
        self.assertFalse(self.launches)
        self.assertFalse(self.ops())

    def test_shared_pr_scan_range_and_ordered_metadata(self):
        scanner = publish.privacy.scan_commits
        with patch.object(publish.privacy, 'scan_commits', wraps=scanner) as scan:
            self.assertEqual(self.invoke().code, 0)
        self.assertTrue(scan.called)
        args, options = scan.call_args
        self.assertEqual(args, (self.repo, self.local, [self.head]))
        self.assertEqual(options, dict(base=self.base, head=self.head,
            branch=self.args.branch, texts=(('title', 'Fixture title'),
                                           ('body', 'Fixture body\n'))))

    def test_late_pr_discovery_changes_refuse_without_mint(self):
        discover = publish._discover
        for change in ('head', 'evidence', 'branch', 'title', 'body'):
            with self.subTest(change=change):
                branch = self.args.branch
                def changed(*a):
                    result = discover(*a)
                    if change == 'head':
                        self.git('checkout', '--detach', self.base)
                    elif change == 'evidence':
                        self.add('check', {'checkId': 'SUITE', 'command': 'fixture-check',
                            'commit': self.head, 'outcome': 'fail', 'source': 'manager',
                            'evidencePath': 'fixture-failed.log'})
                    elif change == 'branch':
                        self.args.branch = 'fixture/changed'
                    else:
                        getattr(self.args, change + '_file').write_text('Changed public text')
                    return result
                with patch.object(publish, '_discover', side_effect=changed):
                    self.assertEqual(self.invoke().data['outcome'], 'refused')
                self.assertFalse(self.launches)
                self.assertNotIn('statusSummary', self.events()[-1]['data'])
                self.git('checkout', '--detach', self.head)
                self.args.branch = branch
                self.args.title_file.write_text('Fixture title')
                self.args.body_file.write_text('Fixture body\n')
                self.cover(self.head)

    def test_create_entry_exact_pair_and_order(self):
        self.push_pair()
        order, check, append, verify = [], evidence.check, publish._append, publish.pullrequest.verify
        def checked(*a, **kw):
            order.append('check')
            return check(*a, **kw)
        def appended(*a, **kw):
            order.append(a[3])
            return append(*a, **kw)
        def verified(args):
            order.append('verify')
            self.assertEqual((args.head, args.base, args.author), (self.head, 'main', 'fixture-worker[bot]'))
            return verify(args)
        with patch.object(evidence, 'check', checked), patch.object(publish, '_append', appended), \
                patch.object(publish.pullrequest, 'verify', verified):
            code, out, err = self.entry()
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(json.loads(out), {'outcome': 'succeeded',
            'reason': 'Exact PR head, base, open state and App author verified',
            'number': 7, 'url': 'https://github.com/fixture-org/publish-fixture/pull/7'})
        self.assertEqual(order, ['check', 'op-intent', 'check', 'verify', 'op-result'])
        self.assertTrue(self.pair()['verified'])
        self.assertEqual(len(self.launches), 1)
        self.assertFalse(Path(self.launches[0][-1]).exists())

    def test_reuse_noop_pair_no_child(self):
        self.push_pair(noop=True)
        self.prs = [self.row()]
        self.assertEqual(self.invoke().data['outcome'], 'no-op')
        self.assertNotIn('statusSummary', self.events()[-1]['data'])
        self.assertEqual(self.launches, [])
        self.assertTrue(self.pair()['verified'])

    def _draft_gate_matrix(self, mode):
        self.push_pair()
        for value in (True, None, 0, 1, 'false', {}, [], 'missing', False):
            with self.subTest(mode=mode, draft=value):
                row = self.row()
                if value == 'missing':
                    del row['draft']
                else:
                    row['draft'] = value
                self.prs = [row] if mode == 'reuse' else []
                self.launches.clear()
                create = self.create
                def child(*args, **kwargs):
                    result = create(*args, **kwargs)
                    self.prs = [row]
                    if mode == 'recovery':
                        raise InterruptedError('PRIVATE_SENTINEL')
                    return result
                with patch.object(token_run, 'run_command', side_effect=child):
                    if mode == 'recovery':
                        self.assertEqual(self.invoke().data['outcome'], 'unknown')
                        self.assertFalse(self.pair()['verified'])
                    code, output, error = self.entry()
                expected = 'no-op' if mode == 'reuse' else 'succeeded'
                self.assertEqual((code, error), (0 if value is False else 1, ''))
                self.assertEqual(json.loads(output)['outcome'], expected if value is False else 'failed')
                self.assertEqual(self.pair()['verified'], value is False)
                result = self.events()[-1]['data']
                self.assertEqual(result['outcome'], expected if value is False else 'failed')
                if mode == 'reuse':
                    self.assertNotIn('statusSummary', result)
                else:
                    self.assertEqual(result['statusSummary'],
                                     {'childStarted': True, 'childStatus': 0, 'revocation': 'ok'})
                self.assertEqual(len(self.launches), 0 if mode == 'reuse' else 1)

    def test_draft_gate_reuse_entry(self):
        self._draft_gate_matrix('reuse')

    def test_draft_gate_creation_entry(self):
        self._draft_gate_matrix('create')

    def test_draft_gate_recovery_entry(self):
        self._draft_gate_matrix('recovery')

    def test_privacy_text_and_branch_before_intent_entry(self):
        for field in ('title_file', 'body_file', 'branch'):
            with self.subTest(field):
                original = getattr(self.args, field)
                if field == 'branch':
                    self.args.branch = 'PRIVATE_SENTINEL'
                else:
                    original.write_text('PRIVATE_SENTINEL')
                code, out, err = self.entry()
                self.assertEqual((code, err), (1, ''))
                self.assertNotIn('PRIVATE_SENTINEL', out)
                if field == 'branch':
                    self.args.branch = original
                else:
                    original.write_text('Fixture title' if field == 'title_file' else 'Fixture body\n')
        self.assertEqual((self.ops(), self.launches, self.reads), ([], [], []))

    def test_invalid_text_inputs(self):
        for raw in (b'\xff', b'\0', b'x' * (publish._LIMIT + 1)):
            self.args.body_file.write_bytes(raw)
            with self.assertRaises(cli.CommandError):
                self.invoke()
        self.args.body_file.unlink()
        with self.assertRaises(cli.CommandError):
            self.invoke()
        self.args.body_file.mkdir()
        with self.assertRaises(cli.CommandError):
            self.invoke()
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_full_published_review_range_and_stale_corrections(self):
        (self.repo / 'file.txt').write_text('PRIVATE_SENTINEL')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture correction')
        self.head = self.args.head = self.git('rev-parse', 'HEAD').strip()
        self.assertEqual(self.invoke().data['outcome'], 'refused')  # stale evidence
        self.cover(self.head)
        self.refs[REF] = self.head  # every private commit is already published
        self.prs = [self.row()]
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual((self.ops(), self.launches), ([], []))

    def test_wrong_pr_fields_fail_verification_and_pair(self):
        self.push_pair()
        for field, value in [('head', self.base), ('base', 'other'), ('state', 'closed'), ('author', 'someone')]:
            row = self.row()
            if field == 'head': row['head']['sha'] = value
            elif field == 'base': row['base']['ref'] = value
            elif field == 'author': row['user']['login'] = value
            else: row[field] = value
            self.prs = [row]
            self.assertEqual(self.invoke().data['outcome'], 'failed')
            self.assertFalse(self.pair()['verified'])
        self.assertFalse(self.launches)

    def test_verifier_exceptions_or_malformed_never_approve(self):
        self.prs = [self.row()]
        for result in (None, True, {'ok': True}, {'ok': 1, 'mismatches': [], 'action': None},
                       SimpleNamespace(ok=True),
                       cli.Outcome({'ok': False}, 1)):
            with patch.object(publish.pullrequest, 'verify', return_value=result):
                self.assertEqual(self.invoke().data['outcome'], 'failed')
            self.assertFalse(self.pair()['verified'])
        with patch.object(publish.pullrequest, 'verify', side_effect=RuntimeError('PRIVATE_SENTINEL')):
            self.assertEqual(self.invoke().data['outcome'], 'failed')
        self.assertFalse(self.launches)

    def test_discovery_ambiguity_failure_and_shape_never_create(self):
        for raw in (b'[{},{}]', b'{', b'{}', b'[{"number":true}]',
                    b'x' * (publish._LIMIT + 1), b'[{"number":7,"number":8}]'):
            # Resolve the synthetic unknown between cases by retained no-start status.
            with patch.object(process, 'run', side_effect=lambda argv, **kw:
                    subprocess.CompletedProcess(argv, 0, raw, b'') if argv[0] == 'gh'
                    else self.original_run(argv, **kw)):
                self.assertEqual(self.invoke().data['outcome'], 'unknown')
            self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertFalse(self.launches)

    def test_interrupted_create_reconciles_without_duplicate(self):
        def interrupted(*a, **kw):
            self.create(*a, **kw)
            raise InterruptedError('PRIVATE_SENTINEL')
        with patch.object(token_run, 'run_command', side_effect=interrupted):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertEqual(self.invoke().data['outcome'], 'succeeded')
        self.assertEqual(len(self.launches), 1)

    def test_missing_true_null_and_malformed_status(self):
        for status in (None, {'childStarted': True, 'childStatus': None}, {'bad': True}):
            def interrupted(argv, path, **kw):
                if status is not None: jsonio.atomic_replace(path, status)
                self.prs = [self.row()]
                raise InterruptedError()
            with patch.object(token_run, 'run_command', side_effect=interrupted):
                self.assertEqual(self.invoke().data['outcome'], 'unknown')
            expected = 'refused' if status is None else 'unknown'
            self.assertEqual(self.invoke().data['outcome'], expected)
            self.assertFalse(self.pair()['verified'])
            if status is not None: break  # unknown must remain blocking
            self.prs = []
        self.assertFalse(self.launches)

    def test_nonzero_and_refusal_same_code(self):
        self.push_pair()
        for result in (token_run.RunResult(False, None, 'mint-failed', None, 1),
                       token_run.RunResult(True, 1, None, 'ok', 1)):
            self.prs = []
            def child(argv, path, **kw):
                self.prs = [self.row()]
                jsonio.atomic_replace(path, result.record())
                return result
            with patch.object(token_run, 'run_command', side_effect=child):
                outcome = self.invoke().data['outcome']
            self.assertEqual(outcome, 'failed' if result.child_started else 'refused')
            self.assertFalse(self.pair()['verified'])

    def test_revocation_failure_is_truthful(self):
        original = self.create
        def child(*a, **kw):
            result = original(*a, **kw)
            result.revocation = 'failed'
            jsonio.atomic_replace(a[1], result.record())
            return result
        with patch.object(token_run, 'run_command', side_effect=child):
            result = self.invoke()
        self.assertEqual(result.data['outcome'], 'succeeded')
        self.assertIn('revocation failed', result.data['reason'])

    def test_actual_and_durable_disagreement_remains_unknown(self):
        def child(argv, path, **kw):
            jsonio.atomic_replace(path, {'childStarted': True, 'childStatus': 0, 'revocation': 'ok'})
            return object()
        with patch.object(token_run, 'run_command', side_effect=child):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertFalse(self.pair()['verified'])

    def test_persistence_failure_preserves_uncertainty(self):
        append = publish._append
        def fail(*a, **kw):
            if a[3] == 'op-result': raise records.RecordError('PRIVATE_SENTINEL')
            return append(*a, **kw)
        with patch.object(publish, '_append', side_effect=fail):
            with self.assertRaises(cli.CommandError): self.invoke()
        self.assertTrue(records.uncertain_items(self.events()))
        self.assertEqual(self.invoke().data['outcome'], 'succeeded')
        self.assertEqual(len(self.launches), 1)

    def test_other_branch_and_late_results_do_not_resolve(self):
        self.assertEqual(self.invoke().code, 0)
        first, result = self.events()[-2:]
        second = self.add('op-intent', dict(first['data'], opId='fixture-next'))
        self.add('op-result', result['data'])
        self.args.branch = 'fixture/other'
        self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.args.branch = 'fixture/topic'
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertEqual(self.events()[-1]['data']['intentRef'], second['seq'])
        self.assertEqual(len(self.launches), 1)

    def test_current_lead_provenance_still_applies(self):
        self.cover(self.head, lead=True)
        self.prs = [self.row()]
        self.assertEqual(self.invoke().code, 0)
        self.add('lead-assignment', {'thread': 'fixture-replacement', 'assignedBy': WRITER,
            'date': '2026-11-01', 'reason': 'Fixture replacement'})
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertFalse(self.pair()['verified'])

    def test_late_pr_config_refuses_without_creation(self):
        discover = publish._discover
        def changed(*args):
            result = discover(*args)
            self.git('config', 'push.followTags', 'true')
            return result
        with patch.object(publish, '_discover', side_effect=changed):
            result = self.invoke()
        self.assertEqual((result.code, result.data['outcome']), (1, 'refused'))
        self.assertIn('no child launched', result.data['reason'])
        self.assertEqual(self.ops(), ['op-intent', 'op-result'])
        self.assertNotIn('statusSummary', self.events()[-1]['data'])
        self.assertFalse(self.launches)
        self.assertFalse(self.pair()['verified'])

    def test_screened_bytes_bound_despite_source_changes(self):
        discover = publish._discover
        def changed(*a, **kw):
            self.args.title_file.write_text('PRIVATE_SENTINEL')
            self.args.body_file.write_text('PRIVATE_SENTINEL')
            return discover(*a, **kw)
        with patch.object(publish, '_discover', side_effect=changed):
            self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.assertFalse(self.launches)

    def test_reader_keeps_synthetic_auth_home_only_for_reads(self):
        read = self.read
        def inspect(argv, **kw):
            if argv[0] == 'gh':
                self.assertEqual(os.environ['HOME'], str(self.temp / 'home'))
                self.assertEqual(os.environ['GH_TOKEN'], 'synthetic-reader-token')
            return read(argv, **kw)
        with patch.dict(os.environ, {'GH_TOKEN': 'synthetic-reader-token'}), \
                patch.object(process, 'run', side_effect=inspect):
            self.assertEqual(self.invoke().code, 0)

    def test_discovery_failed_reads_and_wrong_targets(self):
        for data in ([dict(self.row(), number=True)], [self.row(), self.row()]):
            self.prs = data
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.prs = [self.row()]
        self.prs[0]['head']['ref'] = 'other'
        self.assertEqual(self.invoke().data['outcome'], 'unknown')
        with patch.object(publish, '_discover', side_effect=cli.CommandError('PRIVATE_SENTINEL')):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertFalse(self.launches)

    def test_invalid_base_head_and_helper_entry(self):
        self.args.base = 'other'
        self.assertEqual(self.entry()[0], 2)
        self.args.base = 'main'
        self.args.head = self.base
        self.assertEqual(self.entry()[0], 2)
        self.args.head = self.head
        with patch.object(cli, 'helper_info', return_value={'commit': 'e' * 40, 'dirty': True,
                                                          'root': str(self.repo)}):
            (self.repo / 'file.txt').write_text('dirty')
            self.assertEqual(self.entry()[0], 1)
        self.assertFalse(self.launches)

    def test_intent_failure_starts_nothing(self):
        with patch.object(publish, '_append', side_effect=records.RecordError('PRIVATE_SENTINEL')):
            with self.assertRaises(cli.CommandError): self.invoke()
        self.assertEqual((self.launches, self.ops()), ([], []))

    def test_true_null_existing_pr_never_counts(self):
        self.push_pair()
        def child(argv, path, **kw):
            jsonio.atomic_replace(path, {'childStarted': True, 'childStatus': None})
            self.prs = [self.row()]
            return token_run.RunResult(True, None, None, None, 1)
        with patch.object(token_run, 'run_command', side_effect=child):
            self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertEqual(self.invoke().data['outcome'], 'unknown')
        self.assertFalse(self.pair()['verified'])

    def test_wrong_head_pair_and_reopened_old_uncertainty(self):
        self.push_pair()
        self.assertEqual(self.invoke().code, 0)
        old = self.events()[-2]
        self.assertEqual(self.invoke().data['outcome'], 'no-op')
        self.add('reconciliation', {'item': {'kind': 'operation', 'ref': old['seq']},
            'outcome': 'in-flight', 't3Facts': {'state': 'unknown'}, 'gitFacts': {'state': 'unknown'},
            'notes': 'Fixture reopened'})
        self.assertFalse(self.pair()['verified'])
        self.assertEqual(self.invoke().data['outcome'], 'unknown')
