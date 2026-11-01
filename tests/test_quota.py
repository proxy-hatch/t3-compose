"""Refusal memory with synthetic fixtures and guarded CLI children."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

from t3compose import cli, config, jsonio, process, quota
from tests.support import OfflineTestCase, ROOT, guarded_helper_command

ACCOUNTS = ['agy-synth-alpha', 'agy-synth-beta', 'agy-synth-gamma']
MODEL = 'gemini-3.8-flash-medium'
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def refusal(observed='2026-01-01T00:00:00Z', **changes):
    return {**dict(providerInstanceId=ACCOUNTS[0], model=MODEL, kind='usage-limit',
                   observedAt=observed, resetAt=None, evidence='synthetic evidence'), **changes}


def digest(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()


class SimulatedQuota(OfflineTestCase):
    def setUp(self):
        self.local = self.temp / 'synthetic-local'
        self.local.mkdir()
        self.cfg_path = self.local / 'agent-routing.json'
        self.state_path = self.local / 'agent-routing-state.json'
        self.lock_path = self.local / 'agent-routing-state.lock'
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.v3.example.json').read_text())
        self.state = {'activeProviderInstanceId': ACCOUNTS[0], 'note': 'prior note',
                      'quotaObservations': {ACCOUNTS[0]: [{'observedAt': 'legacy timestamp',
                          'status': 'exhausted', 'resetAt': None, 'evidence': 'legacy evidence'}]}}
        self.save()

    def save(self):
        self.cfg_path.write_bytes(jsonio.canonical_json(self.cfg))
        self.state_path.write_bytes(jsonio.canonical_json(self.state))

    def argv(self, leaf, **changes):
        values = dict(instance=ACCOUNTS[0], model=MODEL, kind='usage-limit',
                      observed_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                      evidence='synthetic evidence') if leaf == 'record' else dict(
                          instance=ACCOUNTS[1], reason=' explicit reason ') if leaf == 'activate' else {}
        values.update(changes)
        return ['--local-dir', str(self.local), 'quota', leaf,
                *[part for k, v in values.items() for part in ('--' + k.replace('_', '-'), v)]]

    def invoke(self, leaf, code=0, child=False, **changes):
        argv = self.argv(leaf, **changes)
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
            return
        self.assertEqual(err, '')
        result = json.loads(out)
        expected = {'state', 'liveRefusals', 'configDigest', 'stateDigest'}
        if leaf != 'show':
            expected.add('previousStateDigest')
        if leaf == 'record':
            expected.add('activeMove')
        self.assertEqual(set(result), expected)
        self.assertEqual(result['configDigest'], digest(self.cfg_path))
        self.assertEqual(result['stateDigest'], digest(self.state_path))
        self.assertEqual(result['state'], json.loads(self.state_path.read_bytes()))
        self.assertEqual(result['state']['quotaObservations'], self.state['quotaObservations'])
        return result

    def test_real_command_success_and_read_only_show(self):
        previous = digest(self.state_path)
        recorded = self.invoke('record', child=True)
        self.assertEqual(recorded['previousStateDigest'], previous)
        self.assertEqual(recorded['activeMove'], {'from': ACCOUNTS[0], 'to': ACCOUNTS[1]})
        entry = recorded['state']['refusals'][0]
        self.assertEqual(set(entry), set(refusal()))
        self.assertEqual(len(recorded['liveRefusals']), 1)
        expiry = quota.utc(entry['observedAt']) + timedelta(minutes=30)
        self.assertEqual(recorded['liveRefusals'][0]['expiresAt'], quota.utc_text(expiry))
        before = self.state_path.read_bytes(), self.state_path.stat().st_mtime_ns
        self.invoke('show', child=True)
        self.assertEqual((self.state_path.read_bytes(), self.state_path.stat().st_mtime_ns), before)
        activated = self.invoke('activate', child=True)
        self.assertEqual(activated['state']['refusals'], recorded['state']['refusals'])
        self.assertEqual(activated['state']['note'], ' explicit reason ')
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_pure_time_boundary_hints_duplicates_and_invalid_times(self):
        entries = [refusal(resetAt='2099-01-01T00:00:00Z'), refusal()]
        original = copy.deepcopy(entries)
        self.assertEqual(len(quota.live_refusals(entries, 30, NOW + timedelta(minutes=30,
                                                                              microseconds=-1))), 2)
        self.assertEqual(quota.live_refusals(entries, 30, NOW + timedelta(minutes=30)), [])
        self.assertEqual(entries, original)
        for value in ('', '2026-02-30T00:00:00Z', '2026-01-01T00:00:00+00:00',
                      '2026-01-01T00:00:00.1234567Z', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                quota.utc(value)
        for micros in (0, 1, 123456):
            moment = NOW.replace(microsecond=micros)
            self.assertEqual(quota.utc(quota.utc_text(moment)), moment)
        for entry in (refusal('invalid'), refusal('2026-01-02T00:00:00Z'),
                      refusal(resetAt='invalid'), refusal(resetAt='2025-01-01T00:00:00Z')):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                quota.live_refusals([entry], 30, NOW)

    def test_pruning_order_and_active_moves(self):
        now = datetime.now(timezone.utc)
        old = refusal(quota.utc_text(now - timedelta(minutes=31)))
        live = refusal(quota.utc_text(now - timedelta(minutes=1)))
        self.state['refusals'] = [old, live, live]
        self.save()
        self.assertEqual(len(self.invoke('show')['liveRefusals']), 2)
        result = self.invoke('record', instance=ACCOUNTS[2])
        self.assertIsNone(result['activeMove'])
        self.assertEqual(result['state']['refusals'][:2], [live, live])
        self.invoke('activate', instance=ACCOUNTS[2])
        result = self.invoke('record', instance=ACCOUNTS[2])
        self.assertEqual(result['activeMove'], {'from': ACCOUNTS[2], 'to': ACCOUNTS[0]})
        result = self.invoke('record', instance='codex-synth-primary', model='gpt-6.1-sol')
        self.assertIsNone(result['activeMove'])

    def test_invalid_updates_preserve_bytes(self):
        before = self.state_path.read_bytes()
        for changes in ({'instance': 'synthetic-secret'}, {'model': 'gpt-6.1-sol'},
                        {'kind': 'unknown'}, {'observed_at': '2999-01-01T00:00:00Z'},
                        {'observed_at': 'bad'}, {'reset_at': 'bad'},
                        {'reset_at': '1999-01-01T00:00:00Z'}, {'evidence': ' '},
                        {'evidence': 'x' * 513}, {'status': 'exhausted'}):
            with self.subTest(changes=changes):
                self.invoke('record', code=2, **changes)
                self.assertEqual(self.state_path.read_bytes(), before)
        for changes in ({'instance': 'synthetic-secret'}, {'reason': ' '}, {'reason': 'x' * 16384}):
            self.invoke('activate', code=2, **changes)
            self.assertEqual(self.state_path.read_bytes(), before)
        # Valid live entries must never be pruned merely to make room.
        entry = refusal(quota.utc_text(datetime.now(timezone.utc)), evidence='x' * 500)
        self.state['refusals'] = []
        while len(jsonio.canonical_json(self.state)) + len(jsonio.canonical_json(entry)) + 2 < 16384:
            self.state['refusals'].append(entry)
        self.save()
        self.assertLess(len(self.state_path.read_bytes()), 16384)
        before = self.state_path.read_bytes()
        self.invoke('record', evidence='x' * 512, code=2)
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_bad_state_and_version_two_fail_every_command(self):
        for changes in ({'observedAt': 'bad'}, {'observedAt': '2999-01-01T00:00:00Z'},
                        {'resetAt': '1999-01-01T00:00:00Z'}):
            self.state['refusals'] = [{**refusal('2000-01-01T00:00:00Z'), **changes}]
            self.save()
            before = self.state_path.read_bytes()
            for leaf in ('show', 'record', 'activate'):
                self.invoke(leaf, code=3)
                self.assertEqual(self.state_path.read_bytes(), before)
        self.state.pop('refusals')
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.example.json').read_text())
        self.save()
        before = self.state_path.read_bytes()
        for leaf in ('show', 'record', 'activate'):
            self.invoke(leaf, code=3)
            self.assertEqual(self.state_path.read_bytes(), before)
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.v3.example.json').read_text())
        self.save()
        self.state_path.unlink()
        for leaf in ('show', 'record', 'activate'):
            self.invoke(leaf, code=3)
            self.assertFalse(self.state_path.exists())

    def test_concurrent_writers_keep_all_six_entries(self):
        pairs = [(a, MODEL) for a in ACCOUNTS] + [
            ('codex-synth-primary', 'gpt-6.1-sol'),
            ('claude-synth-primary', 'claude-opus-5-5'),
            ('pi-synth-glm', 'openrouter/z-ai/glm-5.3')]
        children = []
        try:
            for instance, model in pairs:
                children.append(subprocess.Popen(guarded_helper_command(*self.argv(
                    'record', instance=instance, model=model)), stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True))
            for child in children:
                out, err = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, (out, err))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()
        state = json.loads(self.state_path.read_bytes())
        self.assertEqual({(r['providerInstanceId'], r['model']) for r in state['refusals']}, set(pairs))
        self.assertEqual(len(state['refusals']), 6)
        self.assertEqual(self.state_path.stat().st_mode & 0o777, 0o600)

    def test_held_lock_blocks_child_and_has_private_mode(self):
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        before = self.state_path.read_bytes()
        child = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            child = subprocess.Popen(guarded_helper_command(*self.argv('record')),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            time.sleep(2.05)
            self.assertIsNone(child.poll())
            self.assertEqual(self.state_path.read_bytes(), before)
        finally:
            os.close(fd)
        try:
            out, err = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, (out, err))
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate()
        self.assertEqual(len(json.loads(self.state_path.read_bytes())['refusals']), 1)

    def test_lock_order_and_publication_failures(self):
        events = []
        flock, close, load, publish = fcntl.flock, os.close, quota.load_quota_state, jsonio.atomic_replace
        def locked(fd, mode):
            flock(fd, mode)
            events.append('lock')
        def loaded(*args, **kwargs):
            events.append('read')
            return load(*args, **kwargs)
        def published(*args, **kwargs):
            events.append('publish')
            return publish(*args, **kwargs)
        def closed(fd):
            events.append('close')
            return close(fd)
        with (patch.object(fcntl, 'flock', side_effect=locked),
              patch.object(quota, 'load_quota_state', side_effect=loaded),
              patch.object(jsonio, 'atomic_replace', side_effect=published),
              patch.object(os, 'close', side_effect=closed)):
            self.invoke('record')
        self.assertLess(events.index('lock'), events.index('read'))
        self.assertLess(events.index('read'), events.index('publish'))
        self.assertEqual(events[-1], 'close')
        self.assertEqual(self.lock_path.stat().st_mode & 0o777, 0o600)
        for after in (False, True):
            self.save()
            before = self.state_path.read_bytes()
            replace = jsonio.os.replace
            def fail(src, dst, **kwargs):
                if after:
                    replace(src, dst, **kwargs)
                raise OSError('synthetic-secret')
            with patch.object(jsonio.os, 'replace', side_effect=fail):
                self.invoke('record', code=2)
            if after:
                self.assertEqual(len(json.loads(self.state_path.read_bytes())['refusals']), 1)
            else:
                self.assertEqual(self.state_path.read_bytes(), before)
            self.assertEqual(list(self.local.glob('.t3c-tmp-*')), [])

    def test_import_register_show_and_reload(self):
        with (patch.object(Path, 'open', side_effect=AssertionError('read')),
              patch.object(process, 'run', side_effect=AssertionError('process'))):
            importlib.reload(quota)
            self.assertTrue({('quota', leaf) for leaf in ('show', 'record', 'activate')}
                            <= set(cli.command_names()))
        with patch.object(jsonio, 'atomic_replace', side_effect=AssertionError('write')):
            self.invoke('show')
        self.assertFalse(self.lock_path.exists())
        self.cfg['providerPools']['agy-synth-pool']['providerInstanceIds'].append('agy-synth-new')
        self.save()
        self.invoke('activate', instance='agy-synth-new')
        self.state['note'] = 'changed on disk'
        self.save()
        self.assertEqual(self.invoke('show')['state']['note'], 'changed on disk')
