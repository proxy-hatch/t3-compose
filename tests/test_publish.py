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
        self.assertEqual(order, ['check', 'op-intent', 'op-result'])

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
        with self.assertRaises(cli.CommandError):
            self.invoke()
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
                 token_run.RunResult(True, None, None, None, 1),
                 token_run.RunResult(True, 0, None, 'failed', 0)]
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
            if counter == 3:
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
