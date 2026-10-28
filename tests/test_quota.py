"""Synthetic quota evidence only; every CLI child uses the offline bootstrap."""
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from t3compose import cli, config, jsonio, process
from tests.support import OfflineTestCase, ROOT, guarded_helper_command

ACCOUNTS = ['agy-synth-alpha', 'agy-synth-beta', 'agy-synth-gamma']
OBSERVED = '2000-01-01T00:00:00Z'


def observation(status='exhausted', observed=OBSERVED, reset=None, evidence='synthetic quota evidence'):
    return {'observedAt': observed, 'status': status, 'resetAt': reset, 'evidence': evidence}


def digest(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()


class SimulatedQuota(OfflineTestCase):
    def setUp(self):
        self.local = self.temp / 'synthetic-local'
        self.local.mkdir()
        self.cfg_path = self.local / 'agent-routing.json'
        self.state_path = self.local / 'agent-routing-state.json'
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.example.json').read_text())
        self.state = {'activeProviderInstanceId': ACCOUNTS[0], 'note': 'synthetic prior note',
                      'quotaObservations': {ACCOUNTS[0]: [observation()],
                                            ACCOUNTS[1]: [observation('available')]}}
        self.save()

    def save(self):
        self.cfg_path.write_bytes(jsonio.canonical_json(self.cfg))
        self.state_path.write_bytes(jsonio.canonical_json(self.state))

    def invoke(self, leaf, *options, code=0, child=True):
        argv = ['--local-dir', str(self.local), 'quota', leaf, *options]
        if child:
            result = process.run(guarded_helper_command(*argv), capture_output=True, text=True)
            actual, out, err = result.returncode, result.stdout, result.stderr
        else:
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                actual = cli.main(argv)
            out, err = stdout.getvalue(), stderr.getvalue()
        self.assertEqual(actual, code, (out, err))
        if code:
            self.assertEqual(out, '')
            self.assertTrue(err)
            self.assertNotIn(str(self.temp), err)
            self.assertNotIn('synthetic-secret', err)
            return None
        self.assertEqual(err, '')
        return json.loads(out)

    def record(self, instance=ACCOUNTS[0], status='available', observed=OBSERVED,
               reset=None, evidence='synthetic availability evidence', **kwargs):
        options = ['--instance', instance, '--status', status, '--observed-at', observed,
                   '--evidence', evidence]
        if reset is not None:
            options += ['--reset-at', reset]
        return self.invoke('record', *options, **kwargs)

    def activate(self, instance=ACCOUNTS[1], reason='synthetic explicit activation', **kwargs):
        return self.invoke('activate', '--instance', instance, '--reason', reason, **kwargs)

    def assert_result(self, result, expected):
        self.assertEqual(result['state'], expected)
        self.assertEqual(result['configDigest'], digest(self.cfg_path))
        self.assertEqual(result['stateDigest'], digest(self.state_path))
        self.assertEqual(json.loads(self.state_path.read_bytes()), expected)

    def test_show_is_read_only_with_exact_raw_digests_and_unknown_members(self):
        self.state_path.write_text(json.dumps(self.state, indent=2) + '\n')
        before = self.state_path.read_bytes(), self.state_path.stat().st_mtime_ns
        result = self.invoke('show')
        self.assert_result(result, self.state)
        self.assertEqual(result['quota'], dict(zip(ACCOUNTS, ['exhausted', 'available', 'unknown'])))
        self.assertNotIn('previousStateDigest', result)
        self.assertEqual((self.state_path.read_bytes(), self.state_path.stat().st_mtime_ns), before)
        self.assertEqual(sorted(p.name for p in self.local.iterdir()),
                         ['agent-routing-state.json', 'agent-routing.json'])

    def test_real_leaves_append_all_history_and_activate_separately(self):
        expected = copy.deepcopy(self.state)
        previous = digest(self.state_path)
        result = self.record()
        expected['quotaObservations'][ACCOUNTS[0]].append(observation('available',
                                                    evidence='synthetic availability evidence'))
        self.assert_result(result, expected)
        self.assertEqual(result['previousStateDigest'], previous)
        self.assertEqual(result['quota'][ACCOUNTS[0]], 'available')
        result = self.record(instance=ACCOUNTS[2], status='exhausted', reset='2099-01-01T00:00:00Z')
        expected['quotaObservations'][ACCOUNTS[2]] = [observation(reset='2099-01-01T00:00:00Z',
                                                    evidence='synthetic availability evidence')]
        self.assert_result(result, expected)
        previous = digest(self.state_path)
        result = self.activate(instance=ACCOUNTS[2])
        expected.update(activeProviderInstanceId=ACCOUNTS[2], note='synthetic explicit activation')
        self.assert_result(result, expected)
        self.assertEqual(result['previousStateDigest'], previous)
        self.assertEqual(result['quota'][ACCOUNTS[2]], 'exhausted')
        self.assert_result(self.invoke('show'), expected)
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_out_of_order_observations_append_without_rewriting_or_inference(self):
        self.record(observed='2002-01-01T00:00:00Z')
        result = self.record(status='exhausted', observed='2001-01-01T00:00:00Z')
        self.assertEqual(result['quota'][ACCOUNTS[0]], 'available')
        self.assertEqual([o['observedAt'] for o in result['state']['quotaObservations'][ACCOUNTS[0]]],
                         [OBSERVED, '2002-01-01T00:00:00Z', '2001-01-01T00:00:00Z'])
        result = self.record(status='exhausted', observed='2002-01-01T00:00:00Z')
        self.assertEqual(result['quota'][ACCOUNTS[0]], 'exhausted')
        self.assertIsNone(result['state']['quotaObservations'][ACCOUNTS[0]][-1]['resetAt'])

    def test_reset_boundary_and_available_match_routing_evaluation(self):
        import t3compose.quota as quota
        now = datetime(2020, 1, 1, tzinfo=timezone.utc)
        for reset, expected in [('2019-12-31T23:59:59Z', 'unknown'),
                                ('2020-01-01T00:00:00Z', 'exhausted'),
                                ('2020-01-01T00:00:01Z', 'exhausted')]:
            with self.subTest(reset=reset), patch.object(quota, 'datetime') as clock:
                clock.now.return_value = now
                result = self.record(status='exhausted', reset=reset, child=False)
                self.assertEqual(result['quota'][ACCOUNTS[0]], expected)
        self.assertEqual(self.record()['quota'][ACCOUNTS[0]], 'available')

    def test_invalid_requests_preserve_bytes(self):
        before = self.state_path.read_bytes()
        for changes in ({'instance': 'synthetic-secret-unknown'}, {'status': 'error'},
                {'evidence': ''}, {'evidence': '   '}, {'observed': ''},
                {'observed': 'synthetic-secret'}, {'observed': '2020-02-30T00:00:00Z'},
                {'observed': '2000-01-01T00:00:00+00:00'},
                {'observed': '2999-01-01T00:00:00Z'}, {'reset': 'invalid'},
                {'reset': '1999-12-31T23:59:59Z'}, {'reset': ''}):
            with self.subTest(changes=changes):
                self.record(code=2, **changes)
                self.assertEqual(self.state_path.read_bytes(), before)
        for changes in ({'instance': 'synthetic-secret-unknown'}, {'reason': ''}, {'reason': ' \t '}):
            with self.subTest(changes=changes):
                self.activate(code=2, **changes)
                self.assertEqual(self.state_path.read_bytes(), before)
        for leaf, options in [('record', []), ('activate', []), ('show', ['--extra', 'synthetic-secret'])]:
            self.invoke(leaf, *options, code=2)
            self.assertEqual(self.state_path.read_bytes(), before)

    def test_invalid_current_state_is_not_overwritten_by_any_leaf(self):
        variants = [b'{', b'{}', b'{"note":1,"note":2}', b'[' * 2000,
                    jsonio.canonical_json({**self.state, 'extra': 'synthetic-secret'}),
                    jsonio.canonical_json({**self.state, 'activeProviderInstanceId': 'synthetic-secret'}),
                    jsonio.canonical_json({**self.state, 'quotaObservations': {'synthetic-secret': []}})]
        for change in ({'status': 'error'}, {'evidence': ''}, {'observedAt': 'invalid'},
                       {'observedAt': '2999-01-01T00:00:00Z'}, {'resetAt': '1999-01-01T00:00:00Z'},
                       {'extra': True}):
            invalid = copy.deepcopy(self.state)
            invalid['quotaObservations'][ACCOUNTS[0]][0].update(change)
            variants.append(jsonio.canonical_json(invalid))
        for raw in variants:
            with self.subTest(raw=raw[:60]):
                self.state_path.write_bytes(raw)
                for call in (lambda: self.invoke('show', code=3),
                             lambda: self.record(code=3), lambda: self.activate(code=3)):
                    call()
                    self.assertEqual(self.state_path.read_bytes(), raw)

    def test_missing_state_and_invalid_config_refuse_without_initialization(self):
        self.state_path.unlink()
        for call in (lambda: self.invoke('show', code=3), lambda: self.record(code=3),
                     lambda: self.activate(code=3)):
            call()
            self.assertFalse(self.state_path.exists())
        self.save()
        before = self.state_path.read_bytes()
        for raw in (b'{}', b'{', b'[' * 2000):
            self.cfg_path.write_bytes(raw)
            self.invoke('show', code=3)
            self.record(code=3)
            self.activate(code=3)
            self.assertEqual(self.state_path.read_bytes(), before)
        self.cfg_path.unlink()
        self.invoke('show', code=3)

    def test_each_call_reloads_config_and_state(self):
        first = self.invoke('show', child=False)
        self.cfg['providerPools']['agy-synth-pool']['providerInstanceIds'].append('synthetic-new-account')
        self.save()
        second = self.activate('synthetic-new-account', child=False)
        self.assertNotEqual(first['configDigest'], second['configDigest'])
        self.assertEqual(second['quota']['synthetic-new-account'], 'unknown')
        self.state['quotaObservations'][ACCOUNTS[2]] = [observation()]
        self.save()
        self.assertEqual(self.invoke('show', child=False)['quota'][ACCOUNTS[2]], 'exhausted')
        self.cfg['providerPools']['agy-synth-pool']['providerInstanceIds'].remove(ACCOUNTS[2])
        self.cfg_path.write_bytes(jsonio.canonical_json(self.cfg))
        self.invoke('show', code=3, child=False)
        self.record(code=3, child=False)
        self.activate(code=3, child=False)

    def test_candidate_size_limit_preserves_readable_history(self):
        before = self.state_path.read_bytes()
        self.record(evidence='x' * 16384, code=2)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.activate(reason='x' * 16384, code=2)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.record(evidence='synthetic unicode evidence 雪')
        self.assertEqual(config.load_quota_state(local=self.local).data['quotaObservations'][ACCOUNTS[0]][-1]
                         ['evidence'], 'synthetic unicode evidence 雪')

    def test_shared_atomic_publication_preserves_old_or_complete_new_state(self):
        before = self.state_path.read_bytes()
        replace = jsonio.atomic_replace
        with patch.object(jsonio, 'atomic_replace', wraps=replace) as publish:
            result = self.record(child=False)
        publish.assert_called_once_with(self.state_path, result['state'])
        self.assertNotEqual(self.state_path.read_bytes(), before)
        for fail_after in (False, True):
            self.save()
            def failing_replace(src, dst, **kwargs):
                self.assertEqual(self.state_path.read_bytes(), before)
                if fail_after:
                    original_replace(src, dst, **kwargs)
                raise OSError('synthetic-secret publication failure')
            original_replace = jsonio.os.replace
            with patch.object(jsonio.os, 'replace', side_effect=failing_replace):
                self.record(code=2, child=False)
            actual = json.loads(self.state_path.read_bytes())
            expected = copy.deepcopy(self.state)
            if fail_after:
                expected['quotaObservations'][ACCOUNTS[0]].append(observation('available',
                                                       evidence='synthetic availability evidence'))
            self.assertEqual(actual, expected)
            self.assertEqual(list(self.local.glob('.t3c-tmp-*')), [])

    def test_import_register_and_show_have_no_publication_side_effect(self):
        import t3compose.quota as quota
        with (patch.object(Path, 'open', side_effect=AssertionError('read')),
              patch.object(process, 'run', side_effect=AssertionError('process'))):
            importlib.reload(quota)
            self.assertTrue({('quota', leaf) for leaf in ('show', 'record', 'activate')}
                            <= set(cli.command_names()))
        with patch.object(jsonio, 'atomic_replace', side_effect=AssertionError('write')):
            self.invoke('show', child=False)
