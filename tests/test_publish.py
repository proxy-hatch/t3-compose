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

    def test_policy_reserved_head_refuses_builder_and_checker(self):
        with self.assertRaises(cli.CommandError):
            self.policy_command(branch='HEAD')
        command = self.policy_command()
        command[10] = 'HEAD'
        with self.assertRaises(cli.CommandError):
            self.policy_join(command)
        self.assertEqual(self.launches, [])

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
            with patch.object(process, 'run', side_effect=lambda argv, **kw:
                    subprocess.CompletedProcess(argv, 0, raw, b'') if argv[0] == 'gh'
                    else self.original_run(argv, **kw)):
                self.assertEqual(self.invoke().data['outcome'], 'refused')
            self.assertNotIn('statusSummary', self.events()[-1]['data'])
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
            self.assertEqual(self.invoke().data['outcome'], 'refused')
        self.prs = [self.row()]
        self.prs[0]['head']['ref'] = 'other'
        self.assertEqual(self.invoke().data['outcome'], 'refused')
        with patch.object(publish, '_discover', side_effect=cli.CommandError('PRIVATE_SENTINEL')):
            self.assertEqual(self.invoke().data['outcome'], 'refused')
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


class OfflinePolicyObservation(OfflineTestCase):
    """Direct process-private projection controls; no scanner/authority I/O."""
    def setUp(self):
        from t3compose import privacy, privacy_dispositions as pd
        self.privacy, self.pd = privacy, pd
        self.digest = privacy._canonical_digest
        self.d = 'sha256:' + 'd' * 64
        self.time = '2026-11-05T00:00:00+00:00'
        self.ids = privacy._policy_identifiers(('synthetic-private',))
        self.args = dict(operation='push', repository=REPO, base='a'*40, head='b'*40,
            commits=['b'*40], branch='fixture/topic', texts=[], check_command=['synthetic-check'],
            identifiers=self.ids, list_observed_at='2026-11-05T00:00:03+00:00')
        self.context = dict(version='selection-context/v1', repository=REPO,
            base='a'*40, head='b'*40, commits=['b'*40], branchDigest=privacy._digest(b'fixture/topic'), texts=[])

    def authority(self):
        return dict(authorityEventCount=1, authorityCheckpoint=dict(lastSeq=1, digest=self.d),
            authorityWorkDigest=self.d, authorityEventsDigest=self.d,
            observedStart=self.time, observedEnd=self.time)

    def facts(self, mode='artifact'):
        a = self.authority()
        provenance = dict(receiptId='synthetic', authorityWorkId='AUTH', approvalSeq=1,
            inventoryContextDigest=self.d, contextDigest=self.d, policyFileDigest=self.d,
            manifestDigest=self.d, receiptDigest=self.d, sourceDigest=self.d, verificationDigest=self.d,
            baseline='a'*40, **self.ids, **a)
        coverage = dict(version='artifact-coverage/v1', **{k:v for k,v in self.context.items() if k!='version'},
            selectionDigest=self.digest(self.args['commits']), provenance=provenance,
            screened=[], approvedUnscanned=[{'file': 'synthetic-private/path'}], excluded=[])
        f = dict(version='policy-evaluation/v1', policySatisfied=True, rawStatus='policy-evaluation-required',
            raw=dict(ok=False, commits=1, findings=[], action='synthetic-private'), coverage=coverage,
            authority=dict(start=self.authority(), end=self.authority()),
            observation=dict(start=self.time, end='2026-11-05T00:00:02+00:00'))
        if mode != 'artifact':
            f['version'] = 'policy-evaluation/v2'
            f['selection'] = dict(self.context, selectionContext=self.digest(self.context),
                listDigest=self.ids['listDigest'], patternCount=1, localeDigest=self.d)
            bindings = dict(ruleId='synthetic-rule', ruleContext=self.d, repository=REPO,
                scope='public-product-text', fields=['file'], policyVersion=self.pd.POLICY_VERSION,
                policyDigest=self.pd.digest(self.pd.DESCRIPTOR), authorityWorkId='AUTH', approvalSeq=1,
                publicCommit='c'*40, listDigest=self.ids['listDigest'], selectionContext=self.digest(self.context),
                artifact=['synthetic-policy', self.d] if mode=='combined' else [None,None],
                ruleDigest=self.d, publicationDigest=self.d, dispositionsDigest=self.d,
                authorityWorkDigest=self.d, policyFileDigest=self.d)
            f['publicText'] = dict(bindings=bindings, authority=dict(start=self.authority(), end=self.authority()),
                verdicts=[{'private-proof': object()}], resolved=1, unresolved=0)
            f.update(rawStatus='findings', raw=dict(ok=False, commits=1,
                findings=[{'private-finding': object()}], action='synthetic-private'))
            if mode == 'public':
                f.update(coverage=None, authority=None)
        return f

    def project(self, facts=None, **args):
        return publish._policy_observation(self.privacy.PolicyEvaluation(facts or self.facts()), **dict(self.args, **args))

    def refuse(self, f=None, **args):
        with self.assertRaises(cli.CommandError) as error:
            self.project(f, **args)
        self.assertEqual(str(error.exception), 'publication: invalid private policy observation')

    def test_projection_versions_closed_output_and_honest_raw_nonpass(self):
        keys = ('version operation base head selectionDigest contextDigest checkCommandDigest listDigest patternCount '
            'policyBindingDigest artifactAuthorityStartDigest artifactAuthorityEndDigest publicAuthorityStartDigest '
            'publicAuthorityEndDigest rawStatus rawExit commitCount findingCount approvedUnscannedCount '
            'policySatisfied observedStart observedEnd listObservedAt').split()
        for mode in ('artifact','public','combined'):
            with self.subTest(mode=mode):
                value = self.project(self.facts(mode))
                self.assertEqual(set(value), set(keys))
                self.assertEqual(value['version'], 'publication-policy-observation/v1')
                self.assertEqual(value['rawExit'], 1)
                self.assertIs(value['policySatisfied'], True)
                self.assertEqual(value['contextDigest'], self.digest(self.context))
                self.assertEqual(value['selectionDigest'], self.digest(self.args['commits']))
                encoded = json.dumps(value)
                self.assertLessEqual(len(encoded.encode()), 16384)
                for secret in ('synthetic-private','private-proof','private-finding','AUTH','synthetic-policy',str(self.temp)):
                    self.assertNotIn(secret, encoded)
                self.assertEqual(value['artifactAuthorityStartDigest'] is None, mode=='public')
                self.assertEqual(value['publicAuthorityStartDigest'] is None, mode=='artifact')
        clean = self.facts()
        clean['coverage']['approvedUnscanned'] = []
        clean.update(rawStatus='no-text-findings', raw=dict(ok=True, commits=1, findings=[], action=None))
        self.assertEqual(self.project(clean)['rawExit'], 0)

    def test_projection_context_list_and_binding_mismatches(self):
        for mode in ('artifact','public','combined'):
            for args in (dict(repository='github.com/other/project'), dict(base='c'*40), dict(head='c'*40),
                dict(commits=[]), dict(branch='other'), dict(texts=[('title','private')])):
                with self.subTest(mode=mode,args=args): self.refuse(self.facts(mode), **args)
            for key in self.ids:
                ids = dict(self.ids, **{key: 2 if key=='patternCount' else self.d})
                with self.subTest(mode=mode,key=key):
                    # Public-only compares current list/count; descriptor keys remain validated.
                    self.refuse(self.facts(mode), identifiers=ids)
        for mode in ('public','combined'):
            for key in ('selectionContext','listDigest','policyDigest','authorityWorkDigest'):
                f=self.facts(mode); f['publicText']['bindings'][key]='sha256:'+'e'*64
                self.refuse(f)
        f=self.facts('combined'); f['publicText']['bindings']['artifact'][1]='sha256:'+'e'*64
        self.refuse(f)
        f=self.facts(); f['coverage']['commits']=['c'*40,'b'*40]; self.refuse(f)

    def test_projection_explicit_type_closed_shapes_and_bounds(self):
        for result in (True, {}, {'policySatisfied':True}, object(), SimpleNamespace(policy_satisfied=True)):
            with self.assertRaises(cli.CommandError): publish._policy_observation(result, **self.args)
        for verdict in (False, 1, 'true', None):
            f=self.facts(); f['policySatisfied']=verdict; self.refuse(f)
        for field,value in (('version','policy-evaluation/v9'),('rawStatus','private-status')):
            f=self.facts(); f[field]=value; self.refuse(f)
        for target in ('root','raw','coverage','provenance','observation','selection','publicText','bindings'):
            f=self.facts('combined')
            obj={'root':f,'raw':f['raw'],'coverage':f['coverage'],'provenance':f['coverage']['provenance'],
                'observation':f['observation'],'selection':f['selection'],'publicText':f['publicText'],
                'bindings':f['publicText']['bindings']}[target]
            obj['private-extra']=object(); self.refuse(f)
        for key,value in (('ok',1),('commits',True),('commits',-1),('commits',2**63),('findings',{})):
            f=self.facts(); f['raw'][key]=value; self.refuse(f)
        for ids in (dict(self.ids, patternCount=True),dict(self.ids, patternCount=-1),
                    dict(self.ids, patternCount=2**63),dict(self.ids, listDigest='bad'),dict(self.ids, extra=1)):
            self.refuse(identifiers=ids)
        for args in (dict(operation='other'), dict(commits=['b'*40,'b'*40]),dict(commits=[1]),
            dict(check_command='shell command'),dict(check_command=['x'*16385]),dict(branch='\ud800')):
            self.refuse(**args)
        for mode in ('public','combined'):
            for key,value in (('resolved',True),('unresolved',1)):
                f=self.facts(mode); f['publicText'][key]=value; self.refuse(f)
            f=self.facts(mode); f['publicText']['bindings']['artifact']=[None,self.d]; self.refuse(f)

    def test_projection_six_field_authorities_and_stable_binding(self):
        for mode in ('artifact','public','combined'):
            original = self.project(self.facts(mode))
            groups = (['authority'] if mode=='artifact' else
                      ['publicText'] if mode=='public' else ['authority','publicText'])
            for group in groups:
                for side in ('start','end'):
                    for field in self.authority():
                        f=self.facts(mode); a=f['authority'] if group=='authority' else f[group]['authority']
                        del a[side][field]; self.refuse(f)
                    for field,value in (('extra',1),('authorityEventCount',True),('authorityEventCount',-1),
                        ('authorityEventsDigest','bad'),('observedStart','yesterday'),
                        ('observedStart','2026-11-05T00:00:01+00:00'),('observedEnd',self.time[:-6]),
                        ('observedEnd','2026-11-05T01:00:00+01:00')):
                        f=self.facts(mode); a=f['authority'] if group=='authority' else f[group]['authority']
                        a[side][field]=value; self.refuse(f)
                    f=self.facts(mode); a=f['authority'] if group=='authority' else f[group]['authority']
                    a[side]['observedEnd']='2026-11-05T00:00:00.1+00:00'
                    got=self.project(f)
                    prefix='artifact' if group=='authority' else 'public'
                    key=prefix+'Authority'+side.title()+'Digest'
                    self.assertNotEqual(got[key],original[key])
                    self.assertEqual(got['policyBindingDigest'],original['policyBindingDigest'])
                f=self.facts(mode); a=f['authority'] if group=='authority' else f[group]['authority']
                a['end'].update(authorityEventCount=2,authorityCheckpoint=dict(lastSeq=2,digest=self.d),
                    authorityEventsDigest='sha256:'+'f'*64)
                got=self.project(f)
                self.assertEqual(got['policyBindingDigest'],original['policyBindingDigest'])
                self.assertNotEqual(got[prefix+'AuthorityEndDigest'],original[prefix+'AuthorityEndDigest'])
        f=self.facts(); f['authority']['start']['authorityCheckpoint']['extra']=1; self.refuse(f)
        f=self.facts(); f['authority']['end']['authorityCheckpoint']['lastSeq']=2; self.refuse(f)
        for value in ('2026-11-05T00:00:01+00:00','bad','2026-11-05T00:00:03',self.time+'x'):
            self.refuse(list_observed_at=value)
        f=self.facts(); f['observation']['end']='2026-11-04T00:00:00+00:00'; self.refuse(f)

    def test_projection_pr_order_and_stable_policy_change(self):
        f=self.facts('public'); texts=[('title','private title'),('body','private body')]
        expected=dict(self.context,texts=[dict(labelDigest=self.privacy._digest(k.encode()),
            textDigest=self.privacy._digest(v.encode())) for k,v in texts])
        f['selection'].update(expected,selectionContext=self.digest(expected))
        f['publicText']['bindings']['selectionContext']=self.digest(expected)
        value=self.project(f,operation='pr',texts=texts)
        self.assertEqual(value['contextDigest'],self.digest(expected))
        self.refuse(f,operation='pr',texts=list(reversed(texts)))
        self.refuse(f,operation='push',texts=texts)
        changed=self.facts(); changed['coverage']['provenance']['sourceDigest']='sha256:'+'f'*64
        self.assertNotEqual(self.project(changed)['policyBindingDigest'],self.project()['policyBindingDigest'])

    def test_projection_required_fields_types_and_private_diagnostics(self):
        for mode in ('artifact', 'public', 'combined'):
            f = self.facts(mode)
            targets = [(), ('raw',), ('observation',)]
            if mode != 'public':
                targets += [('coverage',), ('coverage','provenance')]
            if mode != 'artifact':
                targets += [('selection',), ('publicText',), ('publicText','bindings')]
            for path in targets:
                obj = f
                for key in path:
                    obj = obj[key]
                for key in obj:
                    broken = self.facts(mode)
                    target = broken
                    for part in path:
                        target = target[part]
                    del target[key]
                    with self.subTest(mode=mode,path=path,key=key): self.refuse(broken)
        for key,value in (('receiptId','bad/id'),('authorityWorkId','x'*129),('approvalSeq',True),
            ('approvalSeq',0),('approvalSeq',2),('baseline','a'*39),('policyFileDigest',object()),
            ('sourceDigest','sha256:'+'D'*64),('patternCount',True)):
            f=self.facts(); f['coverage']['provenance'][key]=value; self.refuse(f)
        for key,value in (('fields',[]),('fields',['text','file']),('fields',['file','file']),
            ('fields',['unknown']),('fields',[True]),('ruleId','bad/id'),('scope','credential'),
            ('repository','https://'+REPO),('policyVersion',self.privacy.POLICY_VERSION),
            ('approvalSeq',0),('approvalSeq',True),('publicCommit','main'),('artifact',['../policy',self.d])):
            f=self.facts('combined'); f['publicText']['bindings'][key]=value; self.refuse(f)
        for key,value in (('patternCount',True),('patternCount',2),('localeDigest','bad'),
            ('version','selection-context/v9'),('texts',[{}]),('commits',['b'*40,'b'*40])):
            f=self.facts('public'); f['selection'][key]=value; self.refuse(f)
        for key,value in (('selectionDigest','bad'),('branchDigest',self.d),('texts',[{}]),
            ('screened',{}),('approvedUnscanned',None),('excluded',1)):
            f=self.facts(); f['coverage'][key]=value; self.refuse(f)
        for key,value in (('ok',True),('findings',[{}])):
            f=self.facts(); f['raw'][key]=value; self.refuse(f)
        # Even formatting a private object is outside this projection's contract.
        class Private:
            def __repr__(self):
                raise AssertionError('private representation inspected')
        f=self.facts('public')
        f['raw']['findings']=[Private()]
        f['publicText']['verdicts']=[Private()]
        self.assertEqual(self.project(f)['findingCount'],1)
        f['publicText']['bindings']['policyFileDigest']=Private()
        self.refuse(f)

    def test_projection_ordered_selection_metadata_and_empty_push(self):
        f=self.facts()
        self.args['commits']=['c'*40,'b'*40]
        f['coverage']['commits']=list(self.args['commits'])
        f['coverage']['selectionDigest']=self.digest(self.args['commits'])
        f['raw']['commits']=2
        expected=self.project(f)
        self.refuse(f, commits=list(reversed(self.args['commits'])))
        self.args['commits']=[]
        f['coverage']['commits']=[]
        f['coverage']['selectionDigest']=self.digest([])
        f['raw']['commits']=0
        got=self.project(f)
        self.assertEqual(got['commitCount'],0)
        self.assertNotEqual(got['contextDigest'],expected['contextDigest'])
        self.assertNotEqual(got['policyBindingDigest'],expected['policyBindingDigest'])
        self.refuse(f, operation='pr', texts=[])
        self.args['commits']=['b'*40]
        f=self.facts('public')
        texts=[('title','private title'),('body','private body')]
        hashes=[dict(labelDigest=self.privacy._digest(label.encode()),
                     textDigest=self.privacy._digest(body.encode())) for label,body in texts]
        context=dict(self.context,texts=hashes)
        f['selection'].update(texts=hashes,selectionContext=self.digest(context))
        f['publicText']['bindings']['selectionContext']=self.digest(context)
        self.assertEqual(self.project(f,operation='pr',texts=texts)['contextDigest'],self.digest(context))
        for bad in ([('title','changed'),texts[1]], [texts[0],('body','changed')],
                    [('other','private title'),texts[1]], [texts[0]], [*texts,('extra','text')]):
            self.refuse(f,operation='pr',texts=bad)
        for command in ([],[''],[1],['\ud800'],['x\n']): self.refuse(check_command=command)

    def test_projection_authority_time_checkpoint_and_size_boundaries(self):
        for mode in ('artifact','public','combined'):
            groups=['authority'] if mode=='artifact' else ['publicText'] if mode=='public' else ['authority','publicText']
            for group in groups:
                for value in ('', '2026-02-30T00:00:00Z','2026-11-05T00:00:00.1234567Z',
                    '2026-11-05T00:00:00-01:00','2026-11-05 00:00:00+00:00',True):
                    f=self.facts(mode)
                    a=f['authority'] if group=='authority' else f[group]['authority']
                    a['end']['observedEnd']=value
                    self.refuse(f)
                for value in (True,-1,2**31,'1'):
                    f=self.facts(mode)
                    a=f['authority'] if group=='authority' else f[group]['authority']
                    a['end']['authorityCheckpoint']['lastSeq']=value
                    self.refuse(f)
                f=self.facts(mode)
                a=f['authority'] if group=='authority' else f[group]['authority']
                a['end']['authorityWorkDigest']='sha256:'+'e'*64
                self.refuse(f)
        f=self.facts()
        f['observation']=dict(start='2026-11-05T00:00:00Z',end='2026-11-05T00:00:02Z')
        self.assertEqual(self.project(f)['observedStart'],f['observation']['start'])
        later=self.project(f,list_observed_at='2026-11-05T00:00:04Z')
        self.assertEqual(later['policyBindingDigest'],self.project(f)['policyBindingDigest'])
        for value in (None,True,'2026-11-05T00:00:02.1234567Z','x'*16385):
            self.refuse(f,list_observed_at=value)
        # Bound command strings and their aggregate before hashing. Many fields
        # cannot inflate output: the closed result contains fixed-size digests.
        self.refuse(check_command=['x'*4097])
        self.refuse(check_command=['x'*4096]*4)
        for mode in ('artifact','public','combined'):
            self.assertLess(len(json.dumps(self.project(self.facts(mode))).encode()), 2048)

    def test_projection_and_writer_are_dormant_without_production_call(self):
        import ast
        source=(ROOT/'t3compose/publish.py').read_text()
        module=ast.parse(source)
        definitions={node.name for node in module.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}
        self.assertIn('_policy_observation',definitions)
        seals=[node for node in module.body if isinstance(node, ast.FunctionDef) and
               node.name == '_seal_policy_context']
        self.assertEqual(len(seals), 1)
        calls=[node for node in ast.walk(module) if isinstance(node,ast.Call) and
               isinstance(node.func,ast.Name) and node.func.id=='_policy_observation']
        seal_calls=[node for node in ast.walk(seals[0]) if isinstance(node,ast.Call) and
                    isinstance(node.func,ast.Name) and node.func.id=='_policy_observation']
        self.assertEqual(len(seal_calls), 1)
        self.assertEqual(calls, seal_calls)
        self.assertIn('_write_policy_observation', definitions)
        dormant = {'_policy_gate_seal', '_prepare_policy_gate', '_finalize_policy_gate',
                   '_policy_gate_capture', '_policy_gate_inputs', '_policy_gate_evaluate'}
        guarded = dormant | {'_seal_policy_context', '_write_policy_observation'}
        owners = []
        for function in module.body:
            if isinstance(function, ast.FunctionDef):
                for node in ast.walk(function):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in guarded:
                        owners.append(function.name)
        self.assertTrue(owners)
        self.assertTrue(set(owners) <= dormant)

    def test_projection_does_not_invent_authority_prefix_continuity(self):
        for mode in ('artifact','public','combined'):
            f=self.facts(mode)
            original=self.project(f)
            groups=[]
            if mode!='public': groups.append(f['authority'])
            if mode!='artifact': groups.append(f['publicText']['authority'])
            for group in groups:
                group['start'].update(authorityEventCount=2,
                    authorityCheckpoint=dict(lastSeq=2,digest=self.d))
            self.assertEqual(self.project(f)['policyBindingDigest'],original['policyBindingDigest'])


class OfflinePolicyObservationWriter(OfflineTestCase):
    """Catch permissive storage, overwrites, partial-success and descriptor leaks."""
    def setUp(self):
        OfflinePolicyObservation.setUp(self)
        self.local = self.temp / 'observations'
        self.local.mkdir(mode=0o700)
        _, self.work_path = records.location(self.local, REPO, 'WORK')
        self.work_path.mkdir(parents=True, mode=0o700)
        # pathlib intermediate parents use default permissions; model record dirs.
        for path in self.work_path.parents:
            if path == self.local: break
            path.chmod(0o700)
        self.parent = self.work_path / 'publication-policy'

    authority = OfflinePolicyObservation.authority
    facts = OfflinePolicyObservation.facts
    project = OfflinePolicyObservation.project

    def write(self, value=None, op='operation'):
        return publish._write_policy_observation(self.local, REPO, 'WORK', op,
                                                self.project() if value is None else value)

    def refused(self, value=None, op='operation'):
        with self.assertRaises(cli.CommandError) as caught:
            self.write(value, op)
        self.assertEqual(str(caught.exception),
                         'publication: invalid private policy observation storage')

    def test_writer_stores_current_producer_versions_exactly_and_exclusively(self):
        for mode in ('artifact', 'public', 'combined'):
            value = self.project(self.facts(mode))
            path = self.write(value, mode)
            self.assertEqual(path, self.parent / (mode + '.json'))
            expected = json.dumps(value, sort_keys=True, separators=(',', ':'),
                                  ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(path.stat().st_uid, os.getuid())
            self.refused(value, mode)
            self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), 0o700)

    def test_writer_rejects_closed_schema_scalar_and_relational_errors_before_io(self):
        good = self.project()
        changes = dict(version=['other', None], operation=['run', [], True],
            base=['A'*40, 'a'*39, None], head=['b'*64], policySatisfied=[1, False],
            patternCount=[True, -1, 2**31, 1.0], commitCount=[True, 2**31, 25576],
            findingCount=[-1, 1048577, 1], approvedUnscannedCount=[1048577, True],
            rawExit=[True, 0, 2], rawStatus=['no-text-findings', 'findings', None],
            observedStart=['2026-02-30T00:00:00Z', '2026-11-06T00:00:00Z',
                           '2026-11-05T00:00:00+01:00', 'x\ud800'],
            observedEnd=['2026-11-04T00:00:00Z'], listObservedAt=['2026-11-04T00:00:00Z'])
        for key in ('selectionDigest', 'contextDigest', 'checkCommandDigest', 'listDigest',
                    'policyBindingDigest', 'artifactAuthorityStartDigest'):
            changes[key] = [None, 'sha256:'+'A'*64, 'x'*16385, {}]
        malformed = [dict(good, extra='private'), {k:v for k,v in good.items() if k!='head'},
                     dict(good, artifactAuthorityStartDigest=None, artifactAuthorityEndDigest=None)]
        malformed += [dict(good, **{key:value}) for key, values in changes.items() for value in values]
        for value in malformed:
            with self.subTest(value=list(value)):
                self.refused(value)
                self.assertFalse(self.parent.exists())
        public = self.project(self.facts('public'))
        self.refused(dict(public, approvedUnscannedCount=1))
        self.refused(dict(public, publicAuthorityEndDigest=None))
        clean = dict(good, approvedUnscannedCount=0, rawExit=0, rawStatus='no-text-findings')
        self.write(clean)

    def test_writer_encoded_limit_and_encoding_failure_are_independent(self):
        # All real closed outputs are <2KiB. Inflate only the encoder result,
        # after a valid shape, to isolate the independent byte-size guard.
        good = self.project()
        for size in (16384, 16385):
            with patch.object(jsonio, 'canonical_json', return_value=b' '*size):
                if size == 16384:
                    self.assertEqual(len(publish._encode_policy_observation(good)), size)
                else:
                    self.refused(good)
        with patch.object(jsonio, 'canonical_json', side_effect=UnicodeError('synthetic-private')):
            self.refused(good)
        self.assertFalse(self.parent.exists())

    def test_writer_malformed_components_and_symlink_ancestors(self):
        for op in ('', '.', '..', '../escape', '/escape', 'x/y', 'a'*129, 'a\0', 'é'):
            self.refused(op=op)
        self.assertFalse(self.parent.exists())
        original = self.work_path.with_name('saved')
        self.work_path.rename(original)
        self.work_path.symlink_to(original, target_is_directory=True)
        self.refused()
        self.assertFalse((original / 'publication-policy').exists())
        self.work_path.unlink(); original.rename(self.work_path)
        self.work_path.chmod(0o755)
        self.refused()
        self.assertEqual(stat.S_IMODE(self.work_path.stat().st_mode), 0o755)

    def test_writer_preserves_unsafe_parents_and_existing_entries(self):
        self.parent.mkdir(mode=0o700)
        self.parent.chmod(0o755)
        self.refused()
        self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), 0o755)
        self.parent.chmod(0o700)
        path = self.parent / 'operation.json'
        target = self.temp / 'target'; target.write_bytes(b'preserve')
        for kind in ('file', 'link', 'dangling', 'directory', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'file': path.write_bytes(b'preserve')
                elif kind == 'link': path.symlink_to(target)
                elif kind == 'dangling': path.symlink_to(self.temp / 'absent')
                elif kind == 'directory': path.mkdir()
                elif kind == 'hardlink': os.link(target, path)
                else: os.mkfifo(path)
                before = path.lstat()
                self.refused()
                self.assertEqual(path.lstat().st_ino, before.st_ino)
                self.assertEqual(target.read_bytes(), b'preserve')
                if kind == 'directory': path.rmdir()
                else: path.unlink()
        self.parent.rmdir(); self.parent.symlink_to(self.temp, target_is_directory=True)
        self.refused()
        self.assertTrue(self.parent.is_symlink())

    def test_writer_rejects_foreign_owners_without_chown(self):
        self.parent.mkdir(mode=0o700)
        real = os.fstat
        def foreign(fd):
            info = real(fd)
            if info.st_ino == self.parent.stat().st_ino:
                fields = list(info); fields[4] = os.getuid()+1
                return os.stat_result(fields)
            return info
        with patch.object(os, 'fstat', side_effect=foreign): self.refused()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_writer_short_writes_complete_and_zero_progress_cleans(self):
        real = os.write
        with patch.object(os, 'write', side_effect=lambda fd, data: real(fd, data[:7])):
            path = self.write()
        self.assertEqual(json.loads(path.read_bytes()), self.project())
        with patch.object(os, 'write', return_value=0): self.refused(op='zero')
        self.assertFalse((self.parent/'zero.json').exists())

    def test_writer_fault_stages_close_descriptors_and_never_return_success(self):
        self.parent.mkdir(mode=0o700)
        for stage in ('write', 'file-sync', 'directory-sync', 'read', 'close', 'stat'):
            opened, closed = [], []
            real_open, real_close, real_sync = os.open, os.close, os.fsync
            real_read, real_stat = os.read, os.fstat
            fired, file_stats = [], []
            def opening(*args, **kw):
                fd = real_open(*args, **kw); opened.append(fd); return fd
            def closing(fd):
                regular = stat.S_ISREG(real_stat(fd).st_mode)
                real_close(fd); closed.append(fd)
                if stage == 'close' and regular and not fired:
                    fired.append(stage); raise OSError('synthetic-private')
            def sync(fd):
                regular = stat.S_ISREG(real_stat(fd).st_mode)
                if not fired and ((stage=='file-sync' and regular) or
                                  (stage=='directory-sync' and os.fstat(fd).st_ino == self.parent.stat().st_ino)):
                    fired.append(stage); raise OSError('synthetic-private')
                return real_sync(fd)
            def writing(fd, data):
                if stage=='write':
                    real(fd, data[:3]); fired.append(stage); raise OSError('synthetic-private')
                return real(fd, data)
            def reading(fd, count):
                if stage=='read': fired.append(stage); raise OSError('synthetic-private')
                return real_read(fd, count)
            def stating(fd):
                info = real_stat(fd)
                if stage=='stat' and stat.S_ISREG(info.st_mode):
                    file_stats.append(fd)
                    if len(file_stats) == 2:
                        fired.append(stage); raise OSError('synthetic-private')
                return info
            real = os.write
            with patch.object(os,'open',side_effect=opening), patch.object(os,'close',side_effect=closing), \
                 patch.object(os,'fsync',side_effect=sync), patch.object(os,'write',side_effect=writing), \
                 patch.object(os,'read',side_effect=reading), patch.object(os,'fstat',side_effect=stating):
                self.refused(op=stage)
            self.assertTrue(fired, stage)
            self.assertEqual(sorted(opened), sorted(closed), stage)
            self.assertFalse((self.parent/(stage+'.json')).exists(), stage)

    def test_writer_cleanup_failure_retains_partial_and_refuses_reuse(self):
        real = os.write
        def partial(fd, data):
            real(fd, data[:3]); raise OSError('synthetic-private')
        with patch.object(os, 'write', side_effect=partial), \
             patch.object(os, 'unlink', side_effect=OSError('synthetic-private')):
            self.refused()
        path = self.parent/'operation.json'
        self.assertEqual(path.read_bytes(), b'{"a')
        self.refused()
        self.assertEqual(path.read_bytes(), b'{"a')

    def test_writer_detects_changed_file_identity_links_and_readback(self):
        self.parent.mkdir(mode=0o700)
        real_sync = os.fsync
        for change in ('replace', 'hardlink', 'mode', 'bytes'):
            path = self.parent/(change+'.json')
            fired = []
            def sync(fd):
                if stat.S_ISREG(os.fstat(fd).st_mode) and not fired:
                    fired.append(change)
                    if change == 'replace':
                        path.unlink(); path.write_bytes(b'preserve-replacement')
                    elif change == 'hardlink': os.link(path, self.parent/'alias')
                    elif change == 'mode': path.chmod(0o644)
                    else:
                        with path.open('r+b') as stream: stream.write(b'bad')
                return real_sync(fd)
            with patch.object(os, 'fsync', side_effect=sync): self.refused(op=change)
            self.assertTrue(fired)
            if change == 'replace':
                self.assertEqual(path.read_bytes(), b'preserve-replacement')
            else: self.assertFalse(path.exists())
            if change == 'hardlink': (self.parent/'alias').unlink()

    def test_writer_directory_close_error_retains_complete_exclusive_bytes(self):
        self.parent.mkdir(mode=0o700)
        inode = self.parent.stat().st_ino
        real_close, real_stat = os.close, os.fstat
        fired = []
        def close(fd):
            info = real_stat(fd)
            real_close(fd)
            if info.st_ino == inode and not fired:
                fired.append(True); raise OSError('synthetic-private')
        with patch.object(os, 'close', side_effect=close): self.refused()
        self.assertTrue(fired)
        path = self.parent/'operation.json'
        self.assertEqual(json.loads(path.read_bytes()), self.project())
        self.refused()

    def test_writer_refuses_invalid_identity_work_and_nonexistent_work(self):
        for identity, work in (('not-an-identity', 'WORK'), (REPO, '..'),
                               (REPO, 'missing'), (REPO, 'x'*129)):
            with self.assertRaises(cli.CommandError):
                publish._write_policy_observation(self.local, identity, work, 'op', self.project())
        self.assertFalse(self.parent.exists())

    def test_writer_setup_failure_never_creates_file_and_closes_descriptors(self):
        real_sync = os.fsync
        fired = []
        def sync(fd):
            if os.fstat(fd).st_ino == self.work_path.stat().st_ino:
                fired.append(True); raise OSError('synthetic-private')
            return real_sync(fd)
        with patch.object(os, 'fsync', side_effect=sync): self.refused()
        self.assertTrue(fired)
        self.assertTrue(self.parent.is_dir())
        self.assertEqual(list(self.parent.iterdir()), [])
        # An existing safe dedicated parent remains usable after setup failure.
        self.write()

    def test_writer_existing_parent_still_syncs_work_entry_after_failed_creator(self):
        self.parent.mkdir(mode=0o700)
        real_sync = os.fsync
        def sync(fd):
            if os.fstat(fd).st_ino == self.work_path.stat().st_ino:
                raise OSError('synthetic-private')
            return real_sync(fd)
        with patch.object(os, 'fsync', side_effect=sync): self.refused()
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_writer_cleanup_never_unlinks_replacement_during_creation(self):
        self.parent.mkdir(mode=0o700)
        path = self.parent/'operation.json'
        real_open = os.open
        def opening(name, *args, **kwargs):
            fd = real_open(name, *args, **kwargs)
            if name == 'operation.json':
                path.unlink(); path.write_bytes(b'preserve-replacement')
            return fd
        with patch.object(os, 'open', side_effect=opening): self.refused()
        self.assertEqual(path.read_bytes(), b'preserve-replacement')

    def _root_close_fault(self, fail_open):
        sentinel = self.temp/'root-close-sentinel'
        sentinel.write_bytes(b'synthetic')
        value = self.project()
        real_open, real_close = os.open, os.close
        opened, attempts, recycled, fired = [], [], [], []
        def opening(*args, **kwargs):
            if fail_open and len(opened) == 2:
                raise OSError('synthetic open failure')
            fd = real_open(*args, **kwargs)
            opened.append(fd)
            return fd
        def closing(fd):
            attempts.append(fd)
            real_close(fd)
            if not fired and (fail_open or fd == opened[0]):
                fired.append(fd)
                recycled.append(real_open(sentinel, os.O_RDONLY))
                self.assertEqual(recycled[0], fd)
                raise OSError('synthetic close after release')
        try:
            with patch.object(os, 'open', side_effect=opening), patch.object(
                    os, 'close', side_effect=closing):
                self.refused(value)
            self.assertTrue(fired)
            self.assertTrue(all(attempts.count(fd) == 1 for fd in attempts), attempts)
            self.assertEqual(os.fstat(recycled[0]).st_ino, sentinel.stat().st_ino)
            for fd in set(opened)-set(recycled):
                with self.assertRaises(OSError): os.fstat(fd)
            path = self.parent/'operation.json'
            if fail_open:
                self.assertFalse(self.parent.exists())
            else:
                self.assertEqual(path.read_bytes(), publish._encode_policy_observation(value))
        finally:
            for fd in set(opened+recycled):
                try: real_close(fd)
                except OSError: pass

    def test_writer_root_entry_failure_owns_all_descriptors(self):
        self._root_close_fault(True)

    def test_writer_root_close_error_never_retries_recycled_descriptor(self):
        self._root_close_fault(False)


class OfflinePublisherPolicyFixtures(OfflineTestCase):
    def test_builder_contract(self):
        fixture = PublisherPolicyFixture(self, mode='combined')
        push, pr = fixture.context('push'), fixture.context('pr')
        self.assertEqual(push.commits, [fixture.head])
        self.assertEqual(pr.commits, [fixture.head, fixture.middle])
        self.assertEqual(pr.texts, (('title', fixture.title), ('body', fixture.body)))
        self.assertEqual(push.texts, ())
        for context in (push, pr):
            self.assertEqual(context.identity, REPO)
            self.assertEqual(context.work, 'WORK')
            self.assertEqual(context.base, fixture.base)
            self.assertEqual(context.head, fixture.head)
            self.assertEqual(context.repo, fixture.repo)
            self.assertEqual(context.local, fixture.local)
            self.assertEqual(context.refs, fixture.refs)
            self.assertEqual(context.branch, fixture.args.branch)
            self.assertEqual(fixture.git('config', '--get', 'remote.origin.url').strip(), URL)
            app = json.loads((context.local / 'github-app.json').read_bytes())
            target = app['repository']
            self.assertEqual('github.com/' + target['owner'] + '/' + target['name'], REPO)
            work = records.load_work(context.local, REPO, context.work)['effectiveWork']
            self.assertEqual((work['repository'], work['reviewBase']), (REPO, context.base))
            self.assertTrue(context.policy.is_file())
            self.assertEqual(context.check_command, publish._policy_verify_command(
                context.local, context.repo, context.base, context.head, context.branch,
                context.policy, text_files=context.text_files))


    def test_six_real_evaluations_and_current_projection(self):
        from t3compose import privacy
        fields = set(('version operation base head selectionDigest contextDigest checkCommandDigest '
            'listDigest patternCount policyBindingDigest artifactAuthorityStartDigest '
            'artifactAuthorityEndDigest publicAuthorityStartDigest publicAuthorityEndDigest '
            'rawStatus rawExit commitCount findingCount approvedUnscannedCount policySatisfied '
            'observedStart observedEnd listObservedAt').split())
        authority_fields = set(('authorityEventCount authorityCheckpoint authorityWorkDigest '
                                'authorityEventsDigest observedStart observedEnd').split())
        for mode in ('artifact', 'public', 'combined'):
            f = PublisherPolicyFixture(self, mode=mode)
            observations = []
            for operation in ('push', 'pr'):
                with self.subTest(mode=mode, operation=operation):
                    c = f.context(operation)
                    with patch.object(privacy, '_literal_proofs', wraps=privacy._literal_proofs) as proofs:
                        result = f.evaluate(c)
                    self.assertIs(type(result), privacy.PolicyEvaluation)
                    self.assertIs(result.policy_satisfied, True)
                    facts = result.facts
                    self.assertIs(facts['raw']['ok'], False)
                    self.assertEqual(facts['raw']['commits'], len(c.commits))
                    expected_findings = 0 if mode == 'artifact' else len(c.commits) + len(c.texts)
                    self.assertEqual(len(facts['raw']['findings']), expected_findings)
                    self.assertEqual(proofs.call_count, 0 if mode == 'artifact' else 1 + len(c.texts))
                    if mode != 'artifact':
                        public = facts['publicText']
                        self.assertEqual((public['resolved'], public['unresolved']), (expected_findings, 0))
                        self.assertTrue(all(v['resolved'] and v['occurrences'] == 1
                                            for v in public['verdicts']))
                        self.assertEqual(list(facts['selection']['commits']), c.commits)
                        self.assertEqual(public['bindings']['repository'], REPO)
                    for authority in (facts['authority'], None if mode == 'artifact' else
                                      facts['publicText']['authority']):
                        if authority is not None:
                            for side in ('start', 'end'):
                                self.assertEqual(set(authority[side]), authority_fields)
                                self.assertEqual(authority[side]['authorityEventCount'], 1)
                    observation = f.observation(c, result)
                    observations.append(observation)
                    self.assertEqual(set(observation), fields)
                    self.assertEqual((observation['operation'], observation['rawExit'],
                        observation['commitCount'], observation['findingCount']),
                        (operation, 1, len(c.commits), expected_findings))
                    expected_opaque = 0 if mode == 'public' else len(c.commits)
                    self.assertEqual(observation['approvedUnscannedCount'], expected_opaque)
                    self.assertEqual(observation['rawStatus'], 'policy-evaluation-required'
                                     if mode == 'artifact' else 'findings')
                    self.assertEqual(observation['selectionDigest'], f.digest(c.commits))
                    self.assertEqual(observation['checkCommandDigest'], f.digest(c.check_command))
                    self.assertEqual(observation['patternCount'], 1)
                    self.assertEqual(observation['listDigest'], f.digest(f.rows))
                    # Actual current list, rather than a saved verdict, binds projection.
                    list_file = f.local / 'state/private-identifiers.txt'
                    list_file.write_text('OTHER_SYNTHETIC_ROW\n')
                    with self.assertRaises(cli.CommandError):
                        f.observation(c, result)
                    list_file.write_text(f.rows[0] + '\n')
                    self.assertEqual(f.observation(c, result)['policyBindingDigest'],
                                     observation['policyBindingDigest'])
            self.assertNotEqual(observations[0]['contextDigest'], observations[1]['contextDigest'])
            self.assertNotEqual(observations[0]['policyBindingDigest'], observations[1]['policyBindingDigest'])
            self.assertEqual(f.launches, [])

    def test_captured_command_current_join_and_migration(self):
        for mode in ('artifact', 'public', 'combined'):
            f = PublisherPolicyFixture(self, mode=mode)
            raw = f.policy_pass('fixture-check', outcome='fail')
            for operation in ('push', 'pr'):
                with self.subTest(mode=mode, operation=operation):
                    c = f.context(operation)
                    self.assertTrue(f.evaluate(c).policy_satisfied)
                    command = c.check_command
                    old = f.policy_pass(command)
                    f.policy_requirement(command)
                    self.assertFalse(f.policy_join(command))  # pre-scope pass is stale
                    for changes in ({'commit': f.base}, {'command': 'different-command'},
                                    {'outcome': 'fail'}, {'source': 'worker'}):
                        f.policy_pass(command, **changes)
                        self.assertFalse(f.policy_join(command))
                    f.policy_pass(command)
                    self.assertTrue(f.policy_join(command))
                    changed = command.copy()
                    changed[10] = 'fixture/other'
                    self.assertFalse(f.policy_join(changed))
                    self.assertFalse(publish._policy_verify_check(f.local, REPO, 'WORK', f.base, command))
                    events = f.events()
                    self.assertEqual(events[raw['seq'] - 1], raw)
                    self.assertEqual(events[old['seq'] - 1], old)
                    f.policy_requirement('intervening-command')
                    f.policy_requirement(command)
                    self.assertFalse(f.policy_join(command))
                    f.policy_pass(command)
                    self.assertTrue(f.policy_join(command))
            # The canonical range invocation is executed only with its PR policy:
            # the separately attested push set is deliberately not that range.
            c = f.context('pr')
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = cli.main(c.check_command[1:])
            self.assertEqual((code, err.getvalue()), (0, ''))
            summary = json.loads(out.getvalue())
            self.assertEqual((summary['policySatisfied'], summary['rawExit']), (True, 1))
            self.assertEqual(f.launches, [])

    def test_context_and_occurrence_invalidation_with_valid_controls(self):
        f = PublisherPolicyFixture(self, mode='combined')
        push, pr = f.context('push'), f.context('pr')
        self.assertTrue(f.evaluate(push).policy_satisfied)
        for changes in ({'commits': pr.commits}, {'branch': 'fixture/other'},
                        {'texts': tuple(reversed(pr.texts))}, {'privacy_policy': pr.policy}):
            with self.subTest(changes=tuple(changes)):
                bad = f.evaluate(push, **changes)
                self.assertIs(bad.policy_satisfied, False)
                self.assertIn('selection', {v['reason'] for v in bad.facts['publicText']['verdicts']})
        self.assertTrue(f.evaluate(pr).policy_satisfied)
        bad = f.evaluate(pr, commits=list(reversed(pr.commits)))
        self.assertIs(bad.policy_satisfied, False)
        entry = f.dispositions['pr']['entries'][0]
        offset = entry['occurrences'][0]['byteOffset']
        entry['occurrences'][0]['byteOffset'] = offset + 1
        f.pin_public('pr')
        bad = f.evaluate(pr)
        self.assertIs(bad.policy_satisfied, False)
        self.assertIn('mismatch', {v['reason'] for v in bad.facts['publicText']['verdicts']})
        entry['occurrences'][0]['byteOffset'] = offset
        f.pin_public('pr')
        self.assertTrue(f.evaluate(pr).policy_satisfied)
        # Repin malformed publication evidence: refusal must be semantic, not a raw hash mismatch.
        path = f.temp / 'pr-publication'
        saved = path.with_name('saved-publication')
        path.rename(saved)
        with self.assertRaises(cli.CommandError):
            f.evaluate(pr)
        saved.rename(path)
        self.assertTrue(f.evaluate(pr).policy_satisfied)
        # A policy made for push cannot stand in for ordered PR metadata.
        bad = f.evaluate(pr, privacy_policy=push.policy)
        self.assertFalse(bad.policy_satisfied)
        self.assertIn('selection', {v['reason'] for v in bad.facts['publicText']['verdicts']})
        f.publication['method'] = 'unattested'
        f.pin_public('pr')
        with self.assertRaises(cli.CommandError):
            f.evaluate(pr)
        f.publication['method'] = 'prior-publication'
        f.pin_public('pr')
        self.assertTrue(f.evaluate(pr).policy_satisfied)
        self.assertEqual(f.launches, [])

    def test_current_authority_required_for_both_policy_components(self):
        for mode, work, question in (
                ('artifact', 'ARTIFACT', 'privacy-artifact/v1 revoke synthetic-inventory '),
                ('public', 'PUBLIC', 'privacy-public-text/v1 revoke synthetic-product-rule '),
                ('combined', 'ARTIFACT', 'privacy-artifact/v1 revoke synthetic-inventory ')):
            f = PublisherPolicyFixture(self, mode=mode)
            c = f.context('pr')
            self.assertTrue(f.evaluate(c).policy_satisfied)
            _, directory = records.location(f.local, REPO, work)
            path = directory / 'events/1.json'
            saved = path.with_name('saved-authority')
            path.rename(saved)
            with self.assertRaises(cli.CommandError):
                f.evaluate(c)
            saved.rename(path)
            self.assertTrue(f.evaluate(c).policy_satisfied)
            context = f.rule_context if work == 'PUBLIC' else f.artifact_context
            f.authority_decision(work, question + context, 'SYNTHETIC ONLY: revoked.')
            with self.assertRaises(cli.CommandError):
                f.evaluate(c)
            self.assertEqual(f.launches, [])

    def test_strict_actual_entry_still_refuses_policy_satisfiable_content(self):
        for mode in ('artifact', 'public', 'combined'):
            f = PublisherPolicyFixture(self, mode=mode)
            c = f.context('push')
            self.assertTrue(f.evaluate(c).policy_satisfied)
            f.policy_requirement(c.check_command)
            f.policy_pass(c.check_command)
            self.assertTrue(f.policy_join(c.check_command))
            code, out, err = f.entry()
            if mode == 'public':
                self.assertEqual((code, err), (1, ''))
                self.assertEqual(json.loads(out)['outcome'], 'refused')
            else:
                self.assertEqual(code, 2)
                self.assertEqual(out, '')
                self.assertEqual(err, 'privacy input must be bounded UTF-8 text without NUL\n')
            self.assertEqual(f.launches, [])


class OfflinePolicyContextSeal(OfflineTestCase):
    def test_real_unchanged_modes_without_repeating_expensive_work(self):
        from t3compose import privacy
        for mode in ('artifact', 'public', 'combined'):
            with self.subTest(mode=mode):
                f = PublisherPolicyFixture(self, mode=mode)
                for name in ('push', 'pr'):
                    c = f.context(name)
                    result = f.evaluate(c)
                    self.assertIs(result.policy_satisfied, True)
                    expected = f.observation(c, result)
                    with patch.object(privacy, '_scan', side_effect=AssertionError('scan')), \
                         patch.object(privacy, 'scan_commits', side_effect=AssertionError('scan')), \
                         patch.object(privacy, 'evaluate_policy', side_effect=AssertionError('evaluate')), \
                         patch.object(privacy, '_literal_proofs', side_effect=AssertionError('proof')):
                        got = self.seal(c, result)
                    self.assertEqual(set(got), set(expected))
                    self.assertGreaterEqual(got.pop('listObservedAt'), expected.pop('listObservedAt'))
                    self.assertEqual(got, expected)
                    self.assertEqual(got['rawExit'], 1)
                self.assertEqual(f.launches, [])

    def test_bad_actual_result_context_list_locale_and_kind_refuse(self):
        from t3compose import privacy
        f = PublisherPolicyFixture(self, mode='combined')
        c = f.context('push')
        result = f.evaluate(c)
        for changes in ({'branch': 'different'}, {'commits': list(reversed(f.context('check').commits))},
                        {'repository': 'github.com/other/repository'}, {'operation': 'pr'},
                        {'texts': (('body', 'wrong'),)}, {'base': f.middle}):
            with self.subTest(changes=changes):
                self.refuses(c, result, **changes)
        self.refuses(c, result.summary())
        self.refuses(c, privacy.PolicyEvaluation(dict(result.facts, policySatisfied=1)))
        with patch.object(privacy, '_locale_digest', return_value='sha256:' + '0'*64):
            self.refuses(c, result)
        original = privacy.prepare_policy_identifiers
        calls = []
        def changed(local):
            ids = original(local)
            calls.append(local)
            return dict(ids, patternCount=ids['patternCount'] + 1)
        with patch.object(privacy, 'prepare_policy_identifiers', side_effect=changed):
            self.refuses(c, result)
        self.assertTrue(calls)
        with patch.object(privacy, '_text_policy', return_value=None):
            self.refuses(c, result)
        artifact = f.context('check')
        artifact.policy.write_text('{}')
        self.refuses(artifact, result)
        self.assertEqual(f.launches, [])

    def refuses(self, c, result, **overrides):
        with self.assertRaises(cli.CommandError) as caught:
            self.seal(c, result, **overrides)
        self.assertEqual(str(caught.exception), 'publication: invalid current policy context')

    def test_current_binding_and_final_list_read_cutoffs(self):
        from t3compose import privacy
        f = PublisherPolicyFixture(self, mode='combined')
        c = f.context('push')
        result = f.evaluate(c)
        original = privacy.load_artifact_policy
        for key in privacy._BINDINGS:
            def changed(*args, **kwargs):
                value = dict(original(*args, **kwargs))
                value[key] = None
                return value
            with self.subTest(binding=key), patch.object(privacy, 'load_artifact_policy', side_effect=changed):
                self.refuses(c, result)
        real_ids = privacy.prepare_policy_identifiers
        calls = []
        def ids(local):
            value = real_ids(local)
            calls.append('list')
            if len(calls) == 2:
                value['patternCount'] += 1
            return value
        with patch.object(privacy, 'prepare_policy_identifiers', side_effect=ids):
            self.refuses(c, result)
        self.assertEqual(calls, ['list', 'list'])
        order = []
        def observe(name, function):
            def wrapped(*args, **kwargs):
                order.append(name)
                return function(*args, **kwargs)
            return wrapped
        with patch.object(privacy, 'prepare_policy_identifiers', side_effect=observe('list', real_ids)), \
             patch.object(privacy, '_text_policy', side_effect=observe('public', privacy._text_policy)), \
             patch.object(privacy, 'load_artifact_policy', side_effect=observe('artifact', original)), \
             patch.object(publish, '_policy_observation', side_effect=observe('projection', publish._policy_observation)):
            self.seal(c, result)
        self.assertEqual(order, ['list', 'public', 'artifact', 'list', 'projection'])

    def test_actual_canonical_command_and_scoped_pr_preparation(self):
        for mode in ('artifact', 'public', 'combined'):
            with self.subTest(mode=mode):
                f = PublisherPolicyFixture(self, mode=mode)
                check, push = f.context('check'), f.context('push')
                self.assertEqual(check.commits, [f.head, f.middle])
                self.assertEqual(push.commits, [f.head])
                self.assertEqual(check.texts, ())
                self.assertNotIn('--text-file', check.check_command)
                self.assertEqual(f.parent, f.middle)
                results = [e for e in f.events() if e['kind'] == 'result']
                self.assertTrue(results)
                self.assertTrue(all(e['data']['parentCommit'] == f.parent for e in results))
                for path in (f.local, f.local / 'state'):
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
                code, summary = f.execute_check()
                self.assertEqual(code, 0)
                self.assertEqual(summary['rawExit'], 1)
                self.assertIs(f.evaluate(push).policy_satisfied, True)
                if mode != 'artifact':
                    mismatched = f.evaluate(push, privacy_policy=check.policy)
                    self.assertIs(mismatched.policy_satisfied, False)
                with f.pr_adapter(), patch.object(token_run, 'run_command') as token:
                    code, out, err = f.pr_entry()
                self.assertIn(code, (1, 2))
                token.assert_not_called()
                self.assertEqual(f.launches, [])

    def test_both_contexts_after_all_proofs_and_after_storage(self):
        # Each subcase gets independent real evidence; no mutation is undone.
        from t3compose import privacy
        for mode in ('artifact', 'public', 'combined'):
            mutations = ['unchanged', 'policy', 'evidence', 'list', 'artifact-authority']
            if mode == 'public':
                mutations.remove('artifact-authority')
            if mode != 'artifact':
                mutations += ['public-authority']
            if mode == 'combined':
                mutations += ['artifact-evidence']
            for stage in ('proof', 'persistence'):
                for mutation in mutations:
                    with self.subTest(mode=mode, stage=stage, mutation=mutation):
                        self.both_context_case(mode, stage, mutation)

    def both_context_case(self, mode, stage, mutation):
        from t3compose import privacy
        from tempfile import TemporaryDirectory
        # Fixture paths stay unique across subcases, without inherited suites.
        with TemporaryDirectory(dir=self.temp) as directory:
            holder = SimpleNamespace(temp=Path(directory))
            class Case:
                temp = holder.temp
                def __getattr__(_, name):
                    return getattr(self, name)
            f = PublisherPolicyFixture(Case(), mode=mode)
            first, second = f.context('check'), f.context('push')
            prior = f.evaluate(first)
            self.seal(first, prior)
            injected = []
            def change():
                if injected:
                    return
                injected.append((stage, mutation))
                if mutation == 'policy':
                    first.policy.write_bytes(first.policy.read_bytes() + b' ')
                elif mutation == 'evidence':
                    name = 'check-source' if mode == 'artifact' else 'check-publication'
                    path = f.temp / name
                    path.write_bytes(path.read_bytes() + b' ')
                elif mutation == 'artifact-evidence':
                    path = f.temp / 'check-source'
                    path.write_bytes(path.read_bytes() + b' ')
                elif mutation == 'list':
                    (f.local / 'state/private-identifiers.txt').write_text('OTHER_SYNTHETIC\n')
                elif mutation == 'public-authority':
                    f.authority_decision('CHECKPUBLIC',
                        'privacy-public-text/v1 revoke synthetic-product-rule ' + f.check_rule_context,
                        'SYNTHETIC ONLY: revoked.')
                elif mutation == 'artifact-authority':
                    f.authority_decision('CHECKARTIFACT',
                        'privacy-artifact/v1 revoke synthetic-inventory ' + f.check_artifact_context,
                        'SYNTHETIC ONLY: revoked.')
            if stage == 'proof':
                name = '_scan' if mode == 'artifact' else '_literal_proofs'
                original = getattr(privacy, name)
                def boundary(*args, **kwargs):
                    value = original(*args, **kwargs)
                    change()
                    return value
                with patch.object(privacy, name, side_effect=boundary):
                    later = f.evaluate(second)
                self.assertIs(later.policy_satisfied, True)
            else:
                later = f.evaluate(second)
                for c, r in ((first, prior), (second, later)):
                    self.seal(c, r)
                # Direct dormant storage, not a production caller or fake event.
                publish._write_policy_observation(f.local, REPO, 'WORK', 'synthetic-review',
                                                 f.observation(first, prior))
                change()
            self.assertEqual(injected, [(stage, mutation)])
            with patch.object(privacy, '_scan', side_effect=AssertionError('scan')), \
                 patch.object(privacy, '_literal_proofs', side_effect=AssertionError('proof')), \
                 patch.object(privacy, 'evaluate_policy', side_effect=AssertionError('evaluate')):
                if mutation == 'unchanged':
                    self.seal(first, prior)
                    self.seal(second, later)
                else:
                    self.refuses(first, prior)
                    if mutation != 'list':
                        self.seal(second, later)
                    else:
                        self.refuses(second, later)
            self.assertEqual(f.launches, [])

    @staticmethod
    def seal(c, result, **overrides):
        options = dict(repo=c.repo, local=c.local, policy=c.policy,
            operation='pr' if c.texts else 'push', repository=c.identity,
            base=c.base, head=c.head, commits=c.commits, branch=c.branch,
            texts=c.texts, check_command=c.check_command)
        options.update(overrides)
        return publish._seal_policy_context(result, **options)


class PublisherPolicyFixture:
    """Reusable synthetic preparation; no cached verdict or publication permission.

    context() prepares independently pinned push and PR policies over actual Git
    selections. Check records are synthetic evidence, never real agent runs.
    Borrow individual publisher helpers without inheriting any test suite.
    """
    git = OfflinePublish.git
    add = OfflinePublish.add
    create_work = OfflinePublish.create_work
    cover = OfflinePublish.cover
    events = OfflinePublish.events
    remote = OfflinePublish.remote
    run_child = OfflinePublish.run_child
    entry = OfflinePublish.entry
    policy_requirement = OfflinePublish.policy_requirement
    policy_pass = OfflinePublish.policy_pass
    policy_join = OfflinePublish.policy_join

    def __getattr__(self, name):
        return getattr(self.case, name)

    def __init__(self, case, *, mode):
        from t3compose import privacy
        if mode not in ('artifact', 'public', 'combined'):
            raise ValueError('unknown fixture mode')
        self.case, self.mode = case, mode
        self.temp = case.temp / mode
        self.temp.mkdir()
        self.allow_program('grep')
        self.repo, self.local = self.temp / 'checkout', self.temp / 'local'
        self.repo.mkdir()
        (self.local / 'state').mkdir(parents=True, mode=0o700)
        self.local.chmod(0o700)
        self.rows = ['SYNTHETIC_PRODUCT']
        (self.local / 'state/private-identifiers.txt').write_text(self.rows[0] + '\n')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Synthetic Publisher')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('config', 'remote.origin.url', URL)
        self.product = 'SYNTHETIC_PRODUCT public description\n'
        self.opaque = b'\0synthetic opaque asset'
        if mode != 'public':
            (self.repo / 'asset.bin').write_bytes(self.opaque)
        if mode != 'artifact':
            (self.repo / 'product.txt').write_text(self.product)
        self.base = self.commit('Synthetic public baseline')
        self.middle = self.commit('Synthetic intermediate')
        (self.repo / 'file.txt').write_text('public fixture\n')
        self.head = self.commit('Synthetic final')
        self.parent = self.git('rev-parse', self.head + '^').strip()
        self.args = SimpleNamespace(local_dir=self.local, repo_path=str(self.repo),
            head=self.head, work='WORK', branch='fixture/topic')
        self.git('branch', self.args.branch)
        self.refs = {'refs/heads/main': self.base, 'refs/heads/advertised': self.middle}
        jsonio.atomic_replace(self.local / 'github-app.json', {
            'version': 1, 'app': {'slug': 'fixture-worker', 'appId': 1,
            'installationAccount': 'fixture-org',
            'keychain': {'service': 'fixture-service', 'account': 'fixture-account'}},
            'repository': {'owner': 'fixture-org', 'name': 'publish-fixture',
                           'permissions': {'contents': 'write'}}})
        self.enterContext(patch.object(cli, 'helper_info', return_value={
            'commit': 'e' * 40, 'dirty': False}))
        self.serial = self.worker_round = 0
        self.current = self.base
        self.create_work()
        self.cover(self.head)
        self.launches = []
        self.enterContext(patch.object(publish, '_remote', side_effect=self.remote))
        self.enterContext(patch.object(token_run, 'run_command', side_effect=self.run_child))
        self.title = 'Synthetic title' if mode == 'artifact' else 'SYNTHETIC_PRODUCT title'
        self.body = 'Synthetic body' if mode == 'artifact' else 'SYNTHETIC_PRODUCT body'
        self.text_files = tuple((label, self.temp / label) for label in ('title', 'body'))
        for (label, path), text in zip(self.text_files, (self.title, self.body)):
            path.write_text(text)
        self.identifiers = privacy.prepare_policy_identifiers(self.local)
        if mode != 'public':
            self.prepare_artifact()
        if mode != 'artifact':
            self.prepare_rule()
        self.contexts = {}
        for operation in ('push', 'pr'):
            commits = (publish._publication_set(self.repo, self.head, self.refs) if operation == 'push'
                       else publish._review_set(self.repo, self.base, self.head))
            texts = () if operation == 'push' else (('title', self.title), ('body', self.body))
            policy = self.temp / ('artifact-policy' if mode == 'artifact' else operation + '-policy')
            files = () if operation == 'push' else self.text_files
            self.contexts[operation] = SimpleNamespace(repo=self.repo, local=self.local,
                identity=REPO, work='WORK', base=self.base, head=self.head, commits=commits,
                refs=dict(self.refs), branch=self.args.branch, texts=texts, text_files=files,
                policy=policy, check_command=publish._policy_verify_command(self.local, self.repo,
                    self.base, self.head, self.args.branch, policy, text_files=files))
            if mode != 'artifact':
                self.prepare_public(operation)

        # A range CHECK has no PR metadata and independent authority/evidence.
        review = self.contexts['pr']
        check = SimpleNamespace(**dict(vars(review), texts=(), text_files=(),
                                      policy=self.temp / 'check-policy'))
        self.contexts['check'] = check
        if mode != 'public':
            self.prepare_artifact(prefix='check-', work='CHECKARTIFACT')
        if mode == 'artifact':
            check.policy.write_bytes((self.temp / 'check-artifact-policy').read_bytes())
        else:
            rule, context = self.rule, self.rule_context
            self.authority_work('CHECKPUBLIC')
            self.rule = dict(rule, authorityWorkId='CHECKPUBLIC')
            self.rule_context = self.digest({k:v for k,v in self.rule.items() if k != 'approvalSeq'})
            self.check_rule_context = self.rule_context
            self.authority_decision('CHECKPUBLIC',
                'privacy-public-text/v1 approve synthetic-product-rule ' + self.rule_context,
                self.rule['source']['quote'])
            attest, publication = self.attest, self.publication
            self.attest = dict(attest, ruleContext=self.rule_context)
            self.publication = dict(publication, ruleContext=self.rule_context)
            self.prepare_public('check')
            self.rule, self.rule_context, self.attest = rule, context, attest
            self.publication = publication
        check.check_command = publish._policy_verify_command(self.local, self.repo,
            self.base, self.head, self.args.branch, check.policy)

    def execute_check(self):
        c = self.context('check')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(c.check_command[1:])
        self.assertEqual(err.getvalue(), '')
        return code, json.loads(out.getvalue())

    def pr_adapter(self):
        """Scoped injected readers and token consumer; never launches gh."""
        from contextlib import ExitStack
        stack = ExitStack()
        self.args.base = 'main'
        self.args.title_file, self.args.body_file = (p for _, p in self.text_files)
        self.prs, self.reads = [], []
        self.original_run = process.run
        stack.enter_context(patch.object(process, 'run', side_effect=self.read))
        stack.enter_context(patch.object(token_run, 'run_command', side_effect=self.create_pr))
        return stack

    row = OfflinePublishPR.row
    read = OfflinePublishPR.read
    pr_entry = OfflinePublishPR.entry

    def create_pr(self, argv, path, *, local_dir):
        self.assertEqual(Path.cwd(), self.repo)
        self.assertEqual(local_dir, self.local)
        self.assertEqual(argv[:9], ['gh', 'pr', 'create', '--repo',
            'fixture-org/publish-fixture', '--head', self.args.branch, '--base', 'main'])
        self.assertEqual(argv[9], '--title=' + self.title)
        self.assertEqual(argv[10], '--body-file')
        self.assertEqual(Path(argv[11]).read_text(), self.body)
        self.assertEqual(stat.S_IMODE(Path(argv[11]).stat().st_mode), 0o600)
        self.assertEqual(self.events()[-1]['kind'], 'op-intent')
        self.launches.append(argv)
        self.prs = [self.row()]
        result = token_run.RunResult(True, 0, None, 'ok', 0)
        jsonio.atomic_replace(path, result.record())
        return result

    def native(self, role, head):
        append = self.add
        def actual_parent(kind, data):
            if kind == 'result':
                data = dict(data, parentCommit=self.git('rev-parse', head + '^').strip())
            return append(kind, data)
        with patch.object(self, 'add', side_effect=actual_parent):
            return OfflinePublish.native(self, role, head)

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '--allow-empty', '-qm', message)
        return self.git('rev-parse', 'HEAD').strip()

    @staticmethod
    def digest(value):
        import hashlib
        raw = value if isinstance(value, bytes) else jsonio.canonical_json(value)
        return 'sha256:' + hashlib.sha256(raw).hexdigest()

    def write(self, name, value):
        raw = jsonio.canonical_json(value)
        (self.temp / name).write_bytes(raw)
        return self.digest(raw)

    def authority_work(self, work):
        loaded = records.load_work(self.local, REPO, 'WORK')['work']
        data = dict(loaded, workId=work)
        records.create_work(self.local, REPO, work, data, writer=WRITER)

    def authority_decision(self, work, question, answer):
        return records.append_event(self.local, REPO, work, 'decision', dict(
            question=question, answer=answer, date='2026-11-04', decidedBy='owner'), writer=WRITER)

    def prepare_artifact(self, *, prefix='', work='ARTIFACT'):
        self.authority_work(work)
        entries = [dict(path='asset.bin', mode='100644', size=len(self.opaque),
            blobOid=self.git('rev-parse', self.base + ':asset.bin').strip())]
        manifest = dict(schemaVersion=1, repository=REPO, objectFormat='sha1',
                        baseline=self.base, entries=entries)
        receipt = dict(schemaVersion=1, receiptId='synthetic-inventory', repository=REPO,
            baseline=self.base, manifestDigest=self.write(prefix + 'manifest', manifest),
            **{k: self.identifiers[k] for k in ('policyVersion', 'policyDigest', 'listDigest')},
            authorityWorkId=work, approvalSeq=1, sourceFile=prefix + 'source', sourceDigest='',
            verificationFile=prefix + 'verification', verificationDigest='')
        keys = ('receiptId', 'repository', 'baseline', 'manifestDigest', 'policyVersion',
                'policyDigest', 'listDigest', 'authorityWorkId')
        context = dict(schemaVersion=1, **{k: receipt[k] for k in keys})
        source = dict(schemaVersion=1, receiptId=receipt['receiptId'], contextDigest=self.digest(context),
            sourceRef='synthetic:owner-inventory', date='2026-11-04',
            quote='SYNTHETIC ONLY: approve this exact inventory.')
        verification = dict(schemaVersion=1, receiptId=receipt['receiptId'],
            contextDigest=self.digest(context), verifier='synthetic-independent-observer',
            sourceRef='synthetic:inventory-observation', method='git-object-inventory',
            baselineEvidence=dict(repository=REPO, commit=self.base, objectFormat='sha1',
                                  entriesDigest=self.digest(entries)),
            inventoryDigest=receipt['manifestDigest'], result='verified')
        receipt['sourceDigest'] = self.write(prefix + 'source', source)
        receipt['verificationDigest'] = self.write(prefix + 'verification', verification)
        context.update(sourceDigest=receipt['sourceDigest'], verificationDigest=receipt['verificationDigest'])
        artifact_context = self.digest(context)
        if prefix:
            self.check_artifact_context = artifact_context
        else:
            self.artifact_context = artifact_context
        self.authority_decision(work, 'privacy-artifact/v1 approve synthetic-inventory ' +
                                artifact_context, source['quote'])
        self.write(prefix + 'artifact-policy', dict(schemaVersion=1, policyVersion=receipt['policyVersion'],
            manifestFile=prefix + 'manifest', manifestDigest=receipt['manifestDigest'], receiptFile=prefix + 'receipt',
            receiptDigest=self.write(prefix + 'receipt', receipt), authorityWorkId=work))

    def prepare_rule(self):
        from t3compose import privacy_dispositions as pd
        self.authority_work('PUBLIC')
        self.rule = dict(schemaVersion=2, ruleId='synthetic-product-rule', repository=REPO,
            scope=pd.SCOPE, fields=['file', 'text'], policyVersion=pd.POLICY_VERSION,
            policyDigest=pd.digest(pd.DESCRIPTOR), authorityWorkId='PUBLIC', approvalSeq=1,
            source=dict(sourceRef='synthetic:owner-product-rule', date='2026-11-04',
                        quote='SYNTHETIC ONLY: republish public product text.'))
        self.rule_context = self.digest({k: v for k, v in self.rule.items() if k != 'approvalSeq'})
        self.authority_decision('PUBLIC', 'privacy-public-text/v1 approve synthetic-product-rule ' +
                                self.rule_context, self.rule['source']['quote'])
        attest = dict(schemaVersion=2, ruleId=self.rule['ruleId'], ruleContext=self.rule_context,
                      verifier='synthetic-independent-observer', result='verified')
        self.publication = dict(attest, sourceRef='synthetic:prior-publication',
                               method='prior-publication', repository=REPO, publicCommit=self.base)
        self.attest = attest
        self.dispositions = {}

    def prepare_public(self, operation):
        from t3compose import privacy
        c = self.contexts[operation]
        entries = []
        # Enumerate the known fixture places independently of evaluator output.
        places = [('file', sha, 'product.txt', None, self.product) for sha in c.commits]
        places += [('text', None, None, i, text) for i, (_, text) in enumerate(c.texts)]
        for field, commit, path, index, text in places:
            proof = privacy._literal_proofs(self.repo, self.rows, text, public_commit=self.base)
            self.assertIs(proof['complete'], True)
            entries.append(dict(field=field, context=self.digest(dict(version='finding-context/v1',
                field=field, commit=commit, index=index,
                pathDigest=None if path is None else self.digest(path.encode()))),
                inputDigest=self.digest(text.encode()),
                occurrences=[dict(o, scope='public-product-text') for o in proof['occurrences']]))
        selected = dict(version='selection-context/v1', repository=REPO, base=c.base, head=c.head,
            commits=c.commits, branchDigest=self.digest(c.branch.encode()), texts=[dict(
                labelDigest=self.digest(label.encode()), textDigest=self.digest(text.encode()))
                for label, text in c.texts])
        self.dispositions[operation] = dict(self.attest, sourceRef='synthetic:occurrence-review',
            method='occurrence-review', publicCommit=self.base, listDigest=self.digest(self.rows),
            selectionContext=self.digest(selected), entries=entries)
        self.pin_public(operation)

    def pin_public(self, operation):
        from t3compose import privacy_dispositions as pd
        policy = dict(schemaVersion=2, policyVersion=pd.POLICY_VERSION, authorityWorkId=self.rule['authorityWorkId'],
                      artifactPolicyFile=None, artifactPolicyDigest=None)
        for name, value in (('rule', self.rule), ('publication', self.publication),
                            ('dispositions', self.dispositions[operation])):
            filename = operation + '-' + name
            policy.update({name + 'File': filename, name + 'Digest': self.write(filename, value)})
        if self.mode == 'combined':
            name = 'check-artifact-policy' if operation == 'check' else 'artifact-policy'
            policy.update(artifactPolicyFile=name,
                artifactPolicyDigest=self.digest((self.temp / name).read_bytes()))
        self.write(operation + '-policy', policy)

    def context(self, operation):
        return self.contexts[operation]

    def evaluate(self, context, **overrides):
        from t3compose import privacy
        options = dict(base=context.base, head=context.head, branch=context.branch,
                       texts=context.texts, privacy_policy=context.policy)
        commits = overrides.pop('commits', context.commits)
        options.update(overrides)
        return privacy.evaluate_policy(context.repo, context.local, commits, **options)

    def observation(self, context, result):
        from datetime import datetime, timezone
        from t3compose import privacy
        identifiers = privacy.prepare_policy_identifiers(context.local)
        return publish._policy_observation(result, operation='pr' if context.texts else 'push',
            repository=context.identity, base=context.base, head=context.head, commits=context.commits,
            branch=context.branch, texts=context.texts, check_command=context.check_command,
            identifiers=identifiers, list_observed_at=datetime.now(timezone.utc).isoformat())


class OfflinePolicyLifecycle(OfflineTestCase):
    def fixture(self, mode='combined', operation='push'):
        from tempfile import mkdtemp
        case = self
        class Holder:
            temp = Path(mkdtemp(dir=case.temp))
            def __getattr__(self, name):
                return getattr(case, name)
        f = PublisherPolicyFixture(Holder(), mode=mode)
        c = f.context('check' if operation == 'push' else 'pr')
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(c.check_command[1:])
        self.assertEqual((code, err.getvalue()), (0, ''))
        self.assertEqual(json.loads(out.getvalue())['rawExit'], 1)
        f.policy_requirement(c.check_command)
        f.policy_pass(c.check_command)
        self.assertTrue(f.policy_join(c.check_command))
        f.args.privacy_policy = f.context(operation).policy
        f.args.privacy_check_policy = c.policy if operation == 'push' else None
        f.operation = operation
        f.target = ('https://' + REPO + '.git#refs/heads/' + f.args.branch if operation == 'push'
                    else 'https://' + REPO + '/pulls#' + f.args.branch)
        return f

    def prepare(self, f, **changes):
        c = f.context(f.operation)
        options = dict(operation=f.operation, target=f.target,
            refs=f.refs if f.operation == 'push' else None,
            texts=c.texts, text_files=c.text_files)
        options.update(changes)
        return publish._prepare_policy_gate(f.args, f.repo, REPO,
            records.load_work(f.local, REPO, 'WORK'), c.commits, f.args.branch, **options)

    def intent(self, f):
        import uuid
        return f.add('op-intent', dict(opId=uuid.uuid4().hex,
            operation='push' if f.operation == 'push' else 'pull-request',
            target=f.target, commit=f.head, expectedPriorHead='absent' if f.operation == 'push' else None))

    def private_parents(self, f):
        _, root = records.location(f.local, REPO, 'WORK')
        current = root
        while current != f.local.parent:
            current.chmod(0o700)
            current = current.parent
        return root / 'publication-policy'

    def test_six_direct_lifecycles_real_checks_evaluations_intents_and_storage(self):
        from t3compose import privacy
        for mode in ('artifact', 'public', 'combined'):
            for operation in ('push', 'pr'):
                with self.subTest(mode=mode, operation=operation):
                    f = self.fixture(mode, operation)
                    directory = self.private_parents(f)
                    order, results = [], []
                    actual_eval = privacy.evaluate_policy
                    actual_seal = publish._seal_policy_context
                    actual_write = publish._write_policy_observation
                    def evaluate(*args, **kw):
                        value = actual_eval(*args, **kw)
                        self.assertIs(value.policy_satisfied, True)
                        results.append(value)
                        order.append(('evaluate', str(kw['privacy_policy'])))
                        return value
                    def seal(*args, **kw):
                        order.append(('seal', str(kw['policy'])))
                        return actual_seal(*args, **kw)
                    def write(*args):
                        order.append(('write', args[3]))
                        return actual_write(*args)
                    with patch.object(privacy, 'evaluate_policy', side_effect=evaluate), \
                         patch.object(publish, '_seal_policy_context', side_effect=seal), \
                         patch.object(publish, '_write_policy_observation', side_effect=write):
                        state, stopped = self.prepare(f)
                        self.assertIsNone(stopped)
                        self.assertNotIn(str(f.temp), repr(state))
                        intent = self.intent(f)
                        before = f.events()
                        boundary = len(order)
                        self.assertIsNone(publish._finalize_policy_gate(state, intent=intent))
                    policies = [str(f.context('check').policy), str(f.context('push').policy)] if operation == 'push' else [str(f.context('pr').policy)]
                    self.assertEqual(order[:boundary], [('evaluate', p) for p in policies] + [('seal', p) for p in policies])
                    ids = [intent['data']['opId']]
                    if operation == 'push':
                        ids += [ids[0] + '-review']
                    self.assertEqual(order[boundary:], [('evaluate', p) for p in policies] +
                        [('seal', p) for p in policies] + [('write', i) for i in ids] + [('seal', p) for p in policies])
                    self.assertEqual(len({id(r) for r in results}), len(policies) * 2)
                    self.assertEqual(f.events(), before)
                    for name in ids:
                        path = directory / (name + '.json')
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                        self.assertLessEqual(path.stat().st_size, 16384)
                        value = json.loads(path.read_bytes())
                        self.assertEqual(len(value), 23)
                        self.assertEqual(value['rawExit'], 1)
                        self.assertTrue(value['policySatisfied'])
                        self.assertNotIn('SYNTHETIC_PRODUCT', path.read_text())
                        self.assertNotIn(str(f.temp), path.read_text())
                    if operation == 'push':
                        pub = json.loads((directory / (ids[0] + '.json')).read_bytes())
                        review = json.loads((directory / (ids[1] + '.json')).read_bytes())
                        self.assertEqual((pub['commitCount'], review['commitCount']), (1, 2))
                        self.assertEqual(pub['checkCommandDigest'], review['checkCommandDigest'])
                    self.assertEqual(f.launches, [])

    def invalid(self, function):
        with self.assertRaises(cli.CommandError) as caught:
            function()
        self.assertEqual(str(caught.exception), 'publication: invalid policy lifecycle')
        self.assertEqual(caught.exception.code, 2)

    def test_strict_pairing_context_identity_and_current_check(self):
        f = self.fixture()
        saved = f.args.privacy_policy
        del f.args.privacy_policy
        f.args.privacy_check_policy = None
        self.assertEqual(self.prepare(f), (None, None))
        f.args.privacy_check_policy = saved
        self.invalid(lambda: self.prepare(f))
        f.args.privacy_policy = saved
        f.args.privacy_check_policy = f.context('check').policy
        for changes in ({'target': 'https://invalid.invalid'}, {'texts': (('title', 'wrong'),)},
                        {'refs': {}}, {'operation': 'pr'}):
            with self.subTest(changes=changes):
                self.invalid(lambda: self.prepare(f, **changes))
        for command_changes in ({'outcome': 'fail'}, {'commit': f.middle},
                                {'command': ['different']}, {'source': 'worker'}):
            with self.subTest(change=command_changes):
                canonical = f.context('check').check_command
                history = f.events()
                # An unchanged requirement retains the earlier applicable pass.
                f.policy_requirement(canonical)
                f.policy_pass(canonical, **command_changes)
                state, stopped = self.prepare(f)
                if command_changes == {'outcome': 'fail'}:
                    self.assertIsNone(state)
                    self.assertEqual(stopped.code, 1)
                else:
                    self.assertIsNotNone(state)
                    self.assertIsNone(stopped)
                # Changing the definition and restoring it creates a real boundary.
                f.policy_requirement(['intervening-command'])
                f.policy_requirement(canonical)
                self.assertEqual(self.prepare(f)[1].code, 1)  # No post-boundary check.
                f.policy_pass(canonical, **command_changes)
                state, stopped = self.prepare(f)
                self.assertIsNone(state)
                self.assertEqual(stopped.code, 1)
                f.policy_pass(f.context('check').check_command)
                state, stopped = self.prepare(f)
                self.assertIsNone(stopped)
                self.assertIsNotNone(state)
                self.assertEqual(f.events()[:len(history)], history)
        f.git('config', 'remote.origin.url', 'https://github.com/other/repository.git')
        self.invalid(lambda: self.prepare(f))
        self.assertEqual(f.launches, [])

    def test_actual_failed_raw_migration_and_changed_requirement(self):
        f = self.fixture('public')
        canonical = f.context('check').check_command
        f.policy_pass(['raw-check'], outcome='fail')
        self.assertIsNone(self.prepare(f)[1])
        history = f.events()
        f.policy_requirement(['changed-command'])
        self.assertEqual(self.prepare(f)[1].code, 1)
        f.policy_requirement(canonical)
        self.assertEqual(self.prepare(f)[1].code, 1)
        f.policy_pass(canonical)
        self.assertIsNone(self.prepare(f)[1])
        self.assertEqual(f.events()[:len(history)], history)
        # A current pass cannot repair other whole-evidence gaps.
        decision = next(e['data']['decisionRef'] for e in reversed(f.events()) if e['kind'] == 'scope')
        f.add('scope', dict(decisionRef=decision, acceptance=[dict(id='OTHER', text='New criterion')]))
        self.assertEqual(self.prepare(f)[1].code, 1)

    def test_both_independent_captures_same_path_no_result_reuse(self):
        from t3compose import privacy
        f = self.fixture('artifact')
        f.args.privacy_policy = f.args.privacy_check_policy
        original = privacy._policy_snapshot
        reads = []
        def snapshot(path):
            reads.append(str(path))
            return original(path)
        with patch.object(privacy, '_policy_snapshot', side_effect=snapshot):
            state, stopped = self.prepare(f)
        self.assertIsNone(stopped)
        self.assertEqual(state.captures[0], state.captures[1])
        self.assertGreaterEqual(reads.count(state.captures[0][1]), 4)
        self.private_parents(f)
        intent = self.intent(f)
        f.args.privacy_policy = str(f.args.privacy_policy)
        self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
        self.assertEqual(f.launches, [])

    def test_initial_and_final_invalid_type_truth_and_wrong_selection(self):
        from t3compose import privacy
        f = self.fixture('public')
        for value in (True, {'policySatisfied': True}, SimpleNamespace(policy_satisfied=True),
                      privacy.PolicyEvaluation(dict(policySatisfied=1))):
            with self.subTest(value=type(value).__name__):
                with patch.object(privacy, 'evaluate_policy', return_value=value):
                    self.invalid(lambda: self.prepare(f))
        f.args.privacy_policy = f.context('check').policy
        self.assertEqual(self.prepare(f)[1].code, 1)
        f.args.privacy_policy = f.context('push').policy
        self.assertIsNone(self.prepare(f)[1])
        for stage in ('initial', 'final'):
            state, stopped = self.prepare(f)
            self.assertIsNone(stopped)
            with patch.object(privacy, 'evaluate_policy', return_value=privacy.PolicyEvaluation(dict(policySatisfied=False))):
                if stage == 'initial':
                    self.assertEqual(self.prepare(f)[1].code, 1)
                else:
                    self.assertEqual(publish._finalize_policy_gate(state, intent=self.intent(f)).code, 1)

    def test_final_captured_paths_metadata_head_and_real_intent(self):
        for operation, mutation in (('push', 'publication'), ('push', 'check'),
                ('push', 'raw'), ('push', 'head'), ('pr', 'metadata'), ('pr', 'unrelated')):
            with self.subTest(operation=operation, mutation=mutation):
                f = self.fixture('combined', operation)
                state, stopped = self.prepare(f)
                self.assertIsNone(stopped)
                directory = self.private_parents(f)
                intent = self.intent(f)
                if mutation == 'publication':
                    f.args.privacy_policy = str(f.args.privacy_policy) + '/.'
                elif mutation == 'check':
                    f.args.privacy_check_policy = f.temp / 'other'
                elif mutation == 'raw':
                    path = f.context('check').policy
                    path.write_bytes(path.read_bytes() + b' ')
                elif mutation == 'head':
                    f.git('checkout', '--detach', f.middle)
                elif mutation == 'metadata':
                    f.text_files[0][1].write_text('changed title')
                else:
                    f.add('decision', dict(question='unrelated', answer='current change',
                          date='2026-11-04', decidedBy='manager'))
                self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                self.assertFalse(directory.exists())
        for mutation in ('absent', 'target', 'head', 'operation', 'opId', 'prior'):
            with self.subTest(intent=mutation):
                f = self.fixture('artifact')
                state, stopped = self.prepare(f)
                directory = self.private_parents(f)
                if mutation == 'absent':
                    intent = dict(kind='op-intent', data={})
                else:
                    import uuid
                    data = dict(opId=uuid.uuid4().hex, operation='push', target=f.target,
                                commit=f.head, expectedPriorHead='absent')
                    key, value = {'target': ('target', 'wrong'), 'head': ('commit', f.middle),
                        'operation': ('operation', 'other'), 'opId': ('opId', 'non-uuid'),
                        'prior': ('expectedPriorHead', f.base)}[mutation]
                    data[key] = value
                    intent = f.add('op-intent', data)
                self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                self.assertFalse(directory.exists())

    def test_cross_second_actual_proof_and_storage_both_current_seals(self):
        from t3compose import privacy
        # Independent first authority/policy changes leave second actual result satisfied.
        for stage in ('proof', 'storage'):
            for mutation in ('unchanged', 'artifact', 'public', 'source', 'list', 'locale'):
                with self.subTest(stage=stage, mutation=mutation):
                    f = self.fixture()
                    state, stopped = self.prepare(f)
                    self.assertIsNone(stopped)
                    directory = self.private_parents(f)
                    intent = self.intent(f)
                    injected, evaluations, seals = [], [], []
                    def change():
                        if injected:
                            return
                        injected.append(mutation)
                        if mutation == 'artifact':
                            f.authority_decision('CHECKARTIFACT', 'privacy-artifact/v1 revoke synthetic-inventory ' + f.check_artifact_context, 'SYNTHETIC ONLY: revoked.')
                        elif mutation == 'public':
                            f.authority_decision('CHECKPUBLIC', 'privacy-public-text/v1 revoke synthetic-product-rule ' + f.check_rule_context, 'SYNTHETIC ONLY: revoked.')
                        elif mutation == 'source':
                            path = f.temp / 'check-source'
                            path.write_bytes(path.read_bytes() + b' ')
                        elif mutation == 'list':
                            (f.local / 'state/private-identifiers.txt').write_text('OTHER_SYNTHETIC\n')
                        elif mutation == 'locale':
                            os.environ['LC_ALL'] = 'C' if os.environ.get('LC_ALL') != 'C' else 'C.UTF-8'
                    actual_eval, actual_proof = privacy.evaluate_policy, privacy._literal_proofs
                    actual_seal, actual_write = publish._seal_policy_context, publish._write_policy_observation
                    def evaluate(*args, **kwargs):
                        evaluations.append(str(kwargs['privacy_policy']))
                        return actual_eval(*args, **kwargs)
                    def proof(*args, **kwargs):
                        value = actual_proof(*args, **kwargs)
                        if len(evaluations) == 2 and stage == 'proof':
                            change()
                        return value
                    def seal(*args, **kwargs):
                        seals.append(str(kwargs['policy']))
                        return actual_seal(*args, **kwargs)
                    def write(*args):
                        value = actual_write(*args)
                        if stage == 'storage' and args[3].endswith('-review'):
                            change()
                        return value
                    before = f.events()
                    with patch.object(privacy, 'evaluate_policy', side_effect=evaluate), \
                         patch.object(privacy, '_literal_proofs', side_effect=proof), \
                         patch.object(publish, '_seal_policy_context', side_effect=seal), \
                         patch.object(publish, '_write_policy_observation', side_effect=write):
                        if mutation == 'unchanged':
                            self.assertIsNone(publish._finalize_policy_gate(state, intent=intent))
                        else:
                            self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                    self.assertEqual(injected, [mutation])
                    self.assertEqual(len(evaluations), 2)
                    if mutation not in ('list', 'locale'):
                        self.assertTrue(seals)
                    self.assertEqual(f.events(), before)
                    self.assertEqual(len(list(directory.glob('*.json'))), 2 if stage == 'storage' or mutation == 'unchanged' else 0)
                    self.assertEqual(f.launches, [])

    def test_storage_failure_retention_no_reuse_and_fresh_intent(self):
        for failure in (1, 2, 0):
            with self.subTest(failure=failure):
                f = self.fixture('artifact')
                state, stopped = self.prepare(f)
                directory = self.private_parents(f)
                intent = self.intent(f)
                actual = publish._write_policy_observation
                writes = []
                def write(*args):
                    writes.append(args[3])
                    if len(writes) == failure:
                        raise OSError('synthetic private failure')
                    return actual(*args)
                with patch.object(publish, '_write_policy_observation', side_effect=write):
                    if failure:
                        self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                    else:
                        self.assertIsNone(publish._finalize_policy_gate(state, intent=intent))
                old = {p.name: p.read_bytes() for p in directory.glob('*.json')}
                self.assertEqual(len(old), {1: 0, 2: 1, 0: 2}[failure])
                self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                # No event is manufactured by either helper; caller records real refusal.
                self.assertEqual(f.events()[-1], intent)
                f.add('op-result', dict(intentRef=intent['seq'], **{k: intent['data'][k]
                    for k in ('opId', 'operation', 'target', 'commit')}, outcome='refused',
                    confirmation='Synthetic caller observed refusal'))
                fresh, stopped = self.prepare(f)
                self.assertIsNone(stopped)
                later = self.intent(f)
                self.assertIsNone(publish._finalize_policy_gate(fresh, intent=later))
                self.assertEqual({p.name: p.read_bytes() for p in directory.glob('*.json') if p.name in old}, old)
                self.assertNotEqual(later['data']['opId'], intent['data']['opId'])

    def test_second_context_post_storage_seal_old_files_and_final_types(self):
        from t3compose import privacy
        for mutation in ('public', 'artifact', 'old-file', 'final-type'):
            with self.subTest(mutation=mutation):
                f = self.fixture()
                state, stopped = self.prepare(f)
                self.assertIsNone(stopped)
                directory = self.private_parents(f)
                intent = self.intent(f)
                actual = publish._write_policy_observation
                writes, seals, decisions = [], [], []
                before = f.events()
                authority_before = {work: records.load_work(f.local, REPO, work)['events']
                                    for work in ('PUBLIC', 'ARTIFACT')}
                actual_seal = publish._seal_policy_context
                def seal(*args, **kwargs):
                    seals.append(str(kwargs['policy']))
                    return actual_seal(*args, **kwargs)
                def write(*args):
                    value = actual(*args)
                    writes.append(value)
                    if len(writes) == 2:
                        if mutation == 'public':
                            decisions.append(f.authority_decision('PUBLIC', 'privacy-public-text/v1 revoke synthetic-product-rule ' + f.rule_context, 'SYNTHETIC ONLY: revoked.'))
                        elif mutation == 'artifact':
                            decisions.append(f.authority_decision('ARTIFACT', 'privacy-artifact/v1 revoke synthetic-inventory ' + f.artifact_context, 'SYNTHETIC ONLY: revoked.'))
                    return value
                old = None
                if mutation == 'old-file':
                    directory.mkdir(mode=0o700)
                    old = directory / (intent['data']['opId'] + '.json')
                    old.write_bytes(b'old bytes never permission')
                    old.chmod(0o600)
                from contextlib import ExitStack
                with ExitStack() as stack:
                    stack.enter_context(patch.object(publish, '_write_policy_observation', side_effect=write))
                    stack.enter_context(patch.object(publish, '_seal_policy_context', side_effect=seal))
                    if mutation == 'final-type':
                        stack.enter_context(patch.object(privacy, 'evaluate_policy', return_value=True))
                    self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                if mutation in ('public', 'artifact'):
                    self.assertEqual(len(writes), 2)
                    self.assertEqual(seals, [str(f.context(name).policy) for name in ('check', 'push', 'check', 'push')])
                if old:
                    self.assertEqual(old.read_bytes(), b'old bytes never permission')
                self.assertEqual(f.events(), before)
                self.assertEqual(f.events()[-1], intent)
                for work, previous in authority_before.items():
                    expected = previous + decisions if mutation.upper() == work else previous
                    self.assertEqual(records.load_work(f.local, REPO, work)['events'], expected)
                self.assertEqual(len(decisions), int(mutation in ('public', 'artifact')))
                self.assertEqual(f.launches, [])

    def test_initial_policy_authority_and_pinned_evidence_fail_closed(self):
        for mutation in ('missing', 'malformed', 'wrong-place', 'revoked', 'prior-publication'):
            with self.subTest(mutation=mutation):
                f = self.fixture('public')
                self.assertIsNone(self.prepare(f)[1])
                if mutation == 'missing':
                    f.context('push').policy.unlink()
                elif mutation == 'malformed':
                    f.context('push').policy.write_text('{}')
                elif mutation == 'wrong-place':
                    entry = f.dispositions['push']['entries'][0]
                    entry['occurrences'][0]['byteOffset'] += 1
                    f.pin_public('push')
                elif mutation == 'revoked':
                    f.authority_decision('PUBLIC', 'privacy-public-text/v1 revoke synthetic-product-rule ' + f.rule_context, 'SYNTHETIC ONLY: revoked.')
                else:
                    f.publication['method'] = 'unattested'
                    f.pin_public('push')
                if mutation == 'wrong-place':
                    state, stopped = self.prepare(f)
                    self.assertIsNone(state)
                    self.assertEqual(stopped.code, 1)
                else:
                    self.invalid(lambda: self.prepare(f))
                self.assertEqual(f.launches, [])

    def test_equal_bytes_distinct_paths_remain_independent_and_stable_bindings(self):
        for mutation in ('check-bytes', 'publication-bytes', 'list', 'locale'):
            with self.subTest(mutation=mutation):
                f = self.fixture('artifact' if mutation.endswith('bytes') else 'public')
                if mutation.endswith('bytes'):
                    publication = f.context('push').policy
                    check = f.context('check').policy
                    check.write_bytes(publication.read_bytes())
                    # The exact canonical command still executes under the replacement policy.
                    code, summary = f.execute_check()
                    self.assertEqual((code, summary['rawExit']), (0, 1))
                state, stopped = self.prepare(f)
                self.assertIsNone(stopped)
                if mutation.endswith('bytes'):
                    self.assertNotEqual(state.captures[0][1], state.captures[1][1])
                    self.assertEqual(state.captures[0][2], state.captures[1][2])
                intent = self.intent(f)
                directory = self.private_parents(f)
                if mutation.endswith('bytes'):
                    path = f.context('check' if mutation == 'check-bytes' else 'push').policy
                    path.write_bytes(path.read_bytes() + b' ')
                elif mutation == 'list':
                    with (f.local / 'state/private-identifiers.txt').open('a') as stream:
                        stream.write('UNMATCHED_SYNTHETIC\n')
                else:
                    os.environ['LC_ALL'] = 'C' if os.environ.get('LC_ALL') != 'C' else 'C.UTF-8'
                self.invalid(lambda: publish._finalize_policy_gate(state, intent=intent))
                self.assertFalse(directory.exists())


class OfflinePublicationText(OfflineTestCase):
    def setUp(self):
        self.path = self.temp / 'metadata'
        self.path.write_bytes(b'Ordinary public text')

    def read(self):
        return publish._read_publication_text(self.path)

    def refused(self):
        with self.assertRaises(cli.CommandError) as raised:
            self.read()
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn(str(self.path), str(raised.exception))
        self.assertNotIn('PRIVATE_SENTINEL', str(raised.exception))

    def test_regular_empty_and_intermediate_alias(self):
        self.path.chmod(0o666)
        self.assertEqual(self.read(), 'Ordinary public text')
        alias = self.temp / 'alias'
        alias.symlink_to(self.temp, target_is_directory=True)
        self.assertEqual(publish._read_publication_text(alias / 'metadata'), self.read())
        self.path.write_bytes(b'')
        self.assertEqual(self.read(), '')

    def test_final_symlink_and_substitution_at_open(self):
        target = self.temp / 'target'
        target.write_bytes(self.path.read_bytes())
        self.path.unlink()
        self.path.symlink_to(target)
        self.refused()
        self.path.unlink()
        self.path.write_bytes(b'public')
        original = os.open
        def swapped(path, flags, *a, **kw):
            self.path.unlink()
            self.path.symlink_to(target)
            return original(path, flags, *a, **kw)
        with patch.object(os, 'open', side_effect=swapped):
            self.refused()

    def test_nonregular_fifo_nonblocking_and_directory(self):
        self.path.unlink()
        os.mkfifo(self.path)
        original = os.open
        def opened(path, flags, *a, **kw):
            self.assertTrue(flags & os.O_NONBLOCK)
            self.assertTrue(flags & os.O_NOFOLLOW)
            return original(path, flags, *a, **kw)
        with patch.object(os, 'open', side_effect=opened):
            self.refused()
        self.path.unlink()
        self.path.mkdir()
        self.refused()

    def test_bounds_utf8_nul_and_growth(self):
        for raw in (b'x' * (publish._LIMIT + 1), b'\xff', b'x\0y'):
            self.path.write_bytes(raw)
            self.refused()
        self.path.write_bytes(b'x' * publish._LIMIT)
        self.assertEqual(len(self.read()), publish._LIMIT)
        original = os.read
        self.path.write_bytes(b'public')
        def grew(fd, size):
            self.assertLessEqual(size, publish._LIMIT + 1)
            return b'x' * size
        with patch.object(os, 'read', side_effect=grew):
            self.refused()

    def test_named_replacement_and_inplace_mutation(self):
        original = os.read
        for replacement in (False, True):
            self.path.write_bytes(b'public')
            changed = []
            def mutate(fd, size):
                raw = original(fd, size)
                if not changed:
                    changed.append(True)
                    if replacement:
                        other = self.temp / 'other'
                        other.write_bytes(b'public')
                        other.replace(self.path)
                    else:
                        self.path.write_bytes(b'changed')
                return raw
            with patch.object(os, 'read', side_effect=mutate):
                self.refused()
            self.assertTrue(changed)

    def test_setup_read_validation_failures_close_once(self):
        original_open, original_close = os.open, os.close
        for stage in ('fstat', 'read', 'validation', 'stat'):
            opened, closed = [], []
            def opening(*a, **kw):
                fd = original_open(*a, **kw)
                opened.append(fd)
                return fd
            def closing(fd):
                closed.append(fd)
                return original_close(fd)
            target = {'fstat': (os, 'fstat'), 'read': (os, 'read'),
                      'validation': (publish.privacy, '_text'), 'stat': (os, 'stat')}[stage]
            error = cli.CommandError('PRIVATE_SENTINEL') if stage == 'validation' else OSError('PRIVATE_SENTINEL')
            with patch.object(os, 'open', side_effect=opening), patch.object(os, 'close', side_effect=closing):
                with patch.object(*target, side_effect=error):
                    self.refused()
            self.assertEqual(opened, closed)
            self.assertEqual(len(closed), 1)
        with patch.object(os, 'open', side_effect=OSError('PRIVATE_SENTINEL')), patch.object(os, 'close') as close:
            self.refused()
        close.assert_not_called()

    def test_reported_close_error_never_retries_recycled_descriptor(self):
        original_close, original_open = os.close, os.open
        closed, recycled = [], []
        def close(fd):
            closed.append(fd)
            original_close(fd)
            recycled.append(original_open(self.path, os.O_RDONLY))
            raise OSError('PRIVATE_SENTINEL')
        try:
            with patch.object(os, 'close', side_effect=close):
                self.refused()
            self.assertEqual(len(closed), 1)
            os.fstat(recycled[0])
        finally:
            for fd in recycled:
                original_close(fd)


class OfflineStrictInvocation(OfflineTestCase):
    # Fixture methods only: no inherited/repeated baseline test campaign.
    setUp = OfflinePublishPR.setUp
    git = OfflinePublish.git
    add = OfflinePublish.add
    create_work = OfflinePublish.create_work
    native = OfflinePublish.native
    cover = OfflinePublish.cover
    remote = OfflinePublish.remote
    run_child = OfflinePublish.run_child
    events = OfflinePublish.events
    ops = OfflinePublish.ops
    read = OfflinePublishPR.read
    row = OfflinePublishPR.row
    create = OfflinePublishPR.create

    def invoke(self, mode):
        cwd = Path.cwd()
        try:
            if mode == 'pr':
                os.chdir(self.repo)
            return (publish.pr if mode == 'pr' else publish.push)(self.args)
        finally:
            os.chdir(cwd)

    def hook(self, mode, action):
        name = '_discover' if mode == 'pr' else '_status_path'
        original = getattr(publish, name)
        reached = []
        def changed(*a, **kw):
            value = original(*a, **kw)
            if not reached:
                reached.append(True)
                action()
            return value
        self.reached = reached
        return patch.object(publish, name, side_effect=changed)

    def assert_refused(self, result):
        self.assertTrue(self.reached)
        self.assertFalse(self.launches)
        self.assertEqual(result.data['outcome'], 'refused')
        data = self.events()[-1]['data']
        self.assertEqual(self.ops()[-2:], ['op-intent', 'op-result'])
        self.assertEqual(data['outcome'], 'refused')
        self.assertNotIn('statusSummary', data)
        self.assertNotIn('PRIVATE_SENTINEL', str(result.data))
        self.assertNotIn(str(self.temp), str(result.data))

    def test_original_fields_equal_byte_paths_and_current_bytes(self):
        for mode in ('push', 'pr'):
            fields = ['local_dir', 'work', 'head', 'branch', 'repo_path'] if mode == 'push' else [
                'local_dir', 'work', 'head', 'branch', 'repo_path', 'base', 'title_file', 'body_file',
                'title_bytes', 'body_bytes']
            for field in fields:
                with self.subTest(mode=mode, field=field):
                    saved = vars(self.args).copy()
                    def mutate():
                        if field.endswith('_bytes'):
                            getattr(self.args, field.replace('_bytes', '_file')).write_text('Changed public text')
                        elif field.endswith('_file'):
                            alternate = self.temp / ('alternate-' + field)
                            alternate.write_bytes(getattr(self.args, field).read_bytes())
                            setattr(self.args, field, alternate)
                        else:
                            setattr(self.args, field, {'local_dir': self.temp / 'unused', 'work': 'OTHER',
                                'head': self.base, 'branch': 'other/topic', 'base': 'other',
                                'repo_path': str(self.temp)}[field])
                    with self.hook(mode, mutate):
                        result = self.invoke(mode)
                    vars(self.args).update(saved)
                    self.assert_refused(result)
                    self.args.title_file.write_text('Fixture title')
                    self.args.body_file.write_text('Fixture body\n')
                    self.cover(self.head)

    def test_current_complete_app_configuration_semantic_guard(self):
        path = self.local / 'github-app.json'
        initial = path.read_bytes()
        for mode in ('push', 'pr'):
            for section, key, value in (('app', 'appId', 2), ('app', 'slug', 'other-worker'),
                    ('app', 'keychain', {'service': 'other', 'account': 'other'}),
                    ('repository', 'permissions', {'contents': 'read'})):
                with self.subTest(mode=mode, key=key):
                    def mutate():
                        data = json.loads(initial)
                        data[section][key] = value
                        jsonio.atomic_replace(path, data)
                    with self.hook(mode, mutate):
                        result = self.invoke(mode)
                    self.assert_refused(result)
                    path.write_bytes(initial)
                    self.cover(self.head)

    def test_relative_paths_before_dirty_helper_and_repository_cwd(self):
        original = vars(self.args).copy()
        cwd = Path.cwd()
        for mode in ('push', 'pr'):
            self.args.local_dir = Path(os.path.relpath(self.local, self.repo if mode == 'pr' else cwd))
            self.args.repo_path = os.path.relpath(self.repo, cwd)
            self.args.title_file = Path(os.path.relpath(self.temp / 'title.txt', self.repo))
            self.args.body_file = Path(os.path.relpath(self.temp / 'body.txt', self.repo))
            self.git('config', 'user.name', 'Fixture')
            real_git = publish._git
            def git(repo, *a, **kw):
                if a == ('status', '--porcelain', '--untracked-files=no'):
                    return b''
                return real_git(repo, *a, **kw)
            with patch.object(cli, 'helper_info', return_value={'commit': 'e'*40, 'dirty': True, 'root': str(self.repo)}), patch.object(publish, '_git', side_effect=git):
                consumer = self.create if mode == 'pr' else self.run_child
                with patch.object(token_run, 'run_command', side_effect=consumer):
                    result = self.invoke(mode)
            self.assertEqual(result.code, 0)
            self.assertEqual(len(self.launches), 1)
            self.assertEqual(vars(self.args)['local_dir'], Path(os.path.relpath(self.local, self.repo if mode == 'pr' else cwd)))
            self.launches.clear()
            self.cover(self.head)
        vars(self.args).update(original)

    def test_pretoken_read_command_and_append_failures(self):
        config_path = self.local / 'github-app.json'
        config = config_path.read_bytes()
        for mode in ('push', 'pr'):
            for failure in ('read', 'command', 'config', 'append'):
                with self.subTest(mode=mode, failure=failure):
                    def mutate():
                        if failure == 'config':
                            config_path.write_bytes(b'invalid PRIVATE_SENTINEL')
                        if failure == 'read' and mode == 'pr':
                            self.args.body_file.unlink()
                    def fail(*a, **kw):
                        if failure == 'command':
                            raise cli.CommandError('PRIVATE_SENTINEL')
                        raise OSError('PRIVATE_SENTINEL')
                    append = publish._append
                    def writer(*a, **kw):
                        if a[3] == 'op-result':
                            raise records.RecordError('PRIVATE_SENTINEL')
                        return append(*a, **kw)
                    with self.hook(mode, mutate):
                        if failure in ('command', 'append') or failure == 'read' and mode == 'push':
                            with patch.object(publish, '_final_gate', side_effect=fail):
                                if failure == 'append':
                                    with patch.object(publish, '_append', side_effect=writer):
                                        with self.assertRaises(cli.CommandError) as error:
                                            self.invoke(mode)
                                    self.assertEqual(error.exception.code, 2)
                                    self.assertNotIn('PRIVATE_SENTINEL', str(error.exception))
                                    self.assertTrue(self.reached)
                                    self.assertEqual(self.events()[-1]['kind'], 'op-intent')
                                    self.assertTrue(records.uncertain_items(self.events()))
                                    self.assertFalse(self.launches)
                                    # Explicit synthetic reconciliation only after checking uncertainty.
                                    intent = self.events()[-1]
                                    publish._finish(self.local, REPO, 'WORK', intent, 'refused')
                                    result = None
                                else:
                                    result = self.invoke(mode)
                        else:
                            result = self.invoke(mode)
                    config_path.write_bytes(config)
                    self.args.body_file.write_text('Fixture body\n')
                    if result is not None:
                        self.assert_refused(result)
                        self.assertEqual(result.code, 2)
                    self.cover(self.head)

    def test_preintent_invalid_main_missing_metadata_and_symlink(self):
        for field in ('base', 'missing', 'symlink'):
            original = vars(self.args).copy()
            if field == 'base':
                self.args.base = 'other'
            elif field == 'missing':
                self.args.body_file = self.temp / 'missing'
            else:
                link = self.temp / 'link'
                link.symlink_to(self.args.title_file)
                self.args.title_file = link
            with self.assertRaises(cli.CommandError):
                self.invoke('pr')
            self.assertEqual(self.ops(), [])
            self.assertFalse(self.launches)
            vars(self.args).update(original)


    def test_capture_precedes_helper_inspection_and_metadata_read_order(self):
        original_info, original_read = cli.helper_info, publish._read_publication_text
        order = []
        def read(path):
            order.append(Path(path).name)
            return original_read(path)
        def info():
            order.append('helper')
            if order.count('helper') == 1:
                self.args.title_file.write_text('Changed during helper inspection')
            return original_info()
        with patch.object(publish, '_read_publication_text', side_effect=read), patch.object(cli, 'helper_info', side_effect=info):
            result = self.invoke('pr')
        self.assertEqual(order[:3], ['title.txt', 'body.txt', 'helper'])
        self.assertEqual(result.data['outcome'], 'refused')
        self.assertFalse(self.launches)
        self.assertNotIn('statusSummary', self.events()[-1]['data'])

    def test_late_final_symlink_and_read_failure_refuse_actual_pr(self):
        for failure in ('symlink', 'read'):
            with self.subTest(failure=failure):
                path = self.args.title_file
                def mutate():
                    if failure == 'symlink':
                        alternate = self.temp / 'late-target'
                        alternate.write_bytes(path.read_bytes())
                        path.unlink()
                        path.symlink_to(alternate)
                reader = publish._read_publication_text
                def read(value):
                    if failure == 'read' and self.reached:
                        with patch.object(os, 'read', side_effect=OSError('PRIVATE_SENTINEL')):
                            return reader(value)
                    return reader(value)
                with self.hook('pr', mutate), patch.object(publish, '_read_publication_text', side_effect=read):
                    result = self.invoke('pr')
                self.assert_refused(result)
                if path.is_symlink():
                    path.unlink()
                path.write_text('Fixture title')
                self.cover(self.head)

    def test_strict_payload_config_semantics_and_head_last_without_policy_calls(self):
        config_path = self.local / 'github-app.json'
        initial = config_path.read_bytes()
        for mode in ('push', 'pr'):
            order = []
            git, load, guard = publish._git, publish.github_app.load_config, publish._guard_invocation
            def inspect(repo, *a, **kw):
                order.append(tuple(a))
                return git(repo, *a, **kw)
            def config(path):
                order.append('config')
                return load(path)
            def guarded(state):
                order.append('guard')
                return guard(state)
            consumer = self.run_child if mode == 'push' else self.create
            def run(*a, **kw):
                self.assertEqual(order[-1], ('rev-parse', '--verify', 'HEAD'))
                self.assertEqual(order[-2], 'config')
                self.assertIn('guard', order)
                return consumer(*a, **kw)
            def reserialize():
                # Semantic equality is allowed; no raw JSON byte binding is claimed.
                config_path.write_text(json.dumps(json.loads(initial), indent=4) + '\n')
            with self.hook(mode, reserialize), patch.object(publish, '_git', side_effect=inspect), patch.object(publish.github_app, 'load_config', side_effect=config), patch.object(publish, '_guard_invocation', side_effect=guarded):
                with patch.object(publish, '_prepare_policy_gate', side_effect=AssertionError('policy activation')), patch.object(publish, '_finalize_policy_gate', side_effect=AssertionError('policy activation')), patch.object(publish.privacy, 'evaluate_policy', side_effect=AssertionError('policy activation')), patch.object(publish, '_write_policy_observation', side_effect=AssertionError('policy activation')):
                    with patch.object(token_run, 'run_command', side_effect=run):
                        result = self.invoke(mode)
            self.assertEqual(result.code, 0)
            self.assertEqual(len(self.launches), 1)
            if mode == 'push':
                self.assertEqual(self.launches[0], ['git', 'push', '--force-with-lease=' + REF + ':', URL, self.head + ':' + REF])
            self.launches.clear()
            self.cover(self.head)


    def test_removed_original_field_and_early_namespace_mutation_refuse(self):
        for mode in ('push', 'pr'):
            saved = self.args.work
            with self.hook(mode, lambda: delattr(self.args, 'work')):
                result = self.invoke(mode)
            self.args.work = saved
            self.assert_refused(result)
            self.cover(self.head)
        info = cli.helper_info
        def changed():
            self.args.work = 'OTHER'
            return info()
        with patch.object(cli, 'helper_info', side_effect=changed):
            result = self.invoke('push')
        self.args.work = saved
        self.assertEqual(result.data['outcome'], 'refused')
        self.assertFalse(self.launches)

    def test_actual_postintent_push_remote_read_error_records_refusal(self):
        reached = []
        remote = publish._remote
        def read(repo, url):
            if self.events()[-1]['kind'] == 'op-intent':
                reached.append(True)
                raise cli.CommandError('PRIVATE_SENTINEL backend read error')
            return remote(repo, url)
        self.reached = reached
        with patch.object(publish, '_remote', side_effect=read):
            result = self.invoke('push')
        self.assert_refused(result)
        self.assertEqual(result.code, 2)


    def test_containment_setup_failure_is_still_pretoken(self):
        for mode in ('push', 'pr'):
            with self.hook(mode, lambda: None):
                with patch.object(publish, '_quiet', side_effect=OSError('PRIVATE_SENTINEL')):
                    result = self.invoke(mode)
            self.assert_refused(result)
            self.assertEqual(result.code, 2)
            self.cover(self.head)
