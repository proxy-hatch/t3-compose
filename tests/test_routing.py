"""Offline route input, saved-catalog and quota-evaluation tests, with one guarded real-entry-point run."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from t3compose import cli, jsonio, process, records
from tests.support import OfflineTestCase, ROOT, guarded_helper_command
from tests.test_records import _valid_work, _routing_task

REPO, WORK = 'synthetic/routing', 'WORK-1'
ACCOUNTS = ['agy-synth-alpha', 'agy-synth-beta', 'agy-synth-gamma']
DIRECT = 'codex-synth-primary'
MASTER = 'claude-synth-primary'
NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)


def stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def digest(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()


class OfflineRoutingFixture(OfflineTestCase):
    def setUp(self):
        self.local = self.temp / 'synthetic-local'
        self.local.mkdir()
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.example.json').read_text())
        self.state = {'activeProviderInstanceId': ACCOUNTS[0],
                      'quotaObservations': {}, 'note': 'synthetic'}
        self.catalog = {'providers': []}
        selections = [self.cfg['master'], self.cfg['easyWorker'], self.cfg['textHelper']]
        selections += [s for domain in self.cfg['modelPreferences'].values() for s in domain.values()]
        selections += [s for c in self.cfg['availabilityFallback']['chains'] for s in [c['from'], *c['to']]]
        for instance, driver in [(MASTER, 'claudeAgent'), (DIRECT, 'codex'),
                                 ('pi-synth-openrouter', 'pi'), *[(a, 'antigravity') for a in ACCOUNTS]]:
            models = {}
            for selection in selections:
                if selection.get('providerInstanceId') == instance or (
                        'providerPool' in selection and instance in ACCOUNTS):
                    model = models.setdefault(selection['model'], {'id': selection['model'], 'options': []})
                    for key, value in selection['options'].items():
                        option = next((o for o in model['options'] if o['id'] == key), None)
                        if option is None:
                            option = {'id': key, 'values': []}
                            model['options'].append(option)
                        if value not in option['values']:
                            option['values'].append(value)
            self.catalog['providers'].append({'providerInstanceId': instance, 'driverKind': driver,
                'models': list(models.values()), 'canRunChildTask': True,
                'canRunCrossProviderChildTask': True, 'constraints': []})
        self.cfg_path = self.local / 'agent-routing.json'
        self.state_path = self.local / 'agent-routing-state.json'
        self.catalog_path = self.temp / 'catalog.json'
        self.task_path = self.temp / 'task.json'
        self.save()
        work = _valid_work()
        work.update(repository=REPO, writer='lead:WORK-1@' + REPO)
        records.create_work(self.local, REPO, WORK, work, writer=work['writer'])
        self.task = _routing_task()
        self.task['riskFacts'] = []

    def write(self, path, value):
        path.write_bytes(jsonio.canonical_json(value))

    def save(self):
        for path, value in [(self.cfg_path, self.cfg), (self.state_path, self.state),
                            (self.catalog_path, self.catalog)]:
            self.write(path, value)

    def invoke(self, leaf, *options, child=False, code=0):
        argv = ['--local-dir', str(self.local), 'route', leaf, '--repo', REPO, '--work', WORK,
                *map(str, options)]
        if child:
            result = process.run(guarded_helper_command(*argv), capture_output=True, text=True)
            actual, out, err = result.returncode, result.stdout, result.stderr
        else:
            out_stream, err_stream = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_stream), contextlib.redirect_stderr(err_stream):
                actual = cli.main(argv)
            out, err = out_stream.getvalue(), err_stream.getvalue()
        self.assertEqual(actual, code, (out, err))
        if code == 0:
            self.assertEqual(err, '')
            return json.loads(out)
        self.assertEqual(out, '')
        self.assertTrue(err)
        self.assertNotIn(str(self.temp), err)
        return None

    def input(self, child=False, code=0):
        self.write(self.task_path, self.task)
        return self.invoke('input', '--input', self.task_path, child=child, code=code)

    def events(self):
        return records.load_work(self.local, REPO, WORK)['events']


class OfflineRouting(OfflineRoutingFixture):
    def test_real_cli_input_records_canonical_task_and_retired_leaves_are_unknown(self):
        output = self.input(child=True)
        (event,) = self.events()
        self.assertEqual(event['kind'], 'routing-input')
        self.assertEqual(event['writer'], 'lead:WORK-1@' + REPO)
        self.assertEqual(event['data']['task'], self.task)
        self.assertIsNone(event['data']['context'])
        self.assertEqual(output, {'inputRef': event['seq'], 'inputDigest': event['data']['inputDigest'],
                                  'policyDigest': event['data']['policyDigest']})
        for leaf in ('judge', 'select'):
            self.invoke(leaf, code=2)
        self.assertEqual(len(self.events()), 1)

    def test_input_validation_and_config_failure_codes(self):
        for task in [{}, {**self.task, 'intent': 'x' * 16384}, {**self.task, 'extra': True}]:
            self.write(self.task_path, task)
            self.invoke('input', '--input', self.task_path, code=2)
        self.assertEqual(self.events(), [])
        self.cfg['version'] = 1
        self.save()
        self.input(code=3)
        self.assertEqual(self.events(), [])

    def test_damaged_records_and_failed_append_are_input_errors(self):
        with patch.object(records, 'append_event', side_effect=records.RecordError('synthetic failure')):
            self.input(code=2)
        self.assertEqual(self.events(), [])
        self.input()
        _, directory = records.location(self.local, REPO, WORK)
        next((directory / 'events').iterdir()).write_text('{')
        self.input(code=2)

    def test_current_writer_after_transfer_uses_effective_record(self):
        old, new = 'lead:WORK-1@' + REPO, 'manager@' + REPO
        proof = records.append_event(self.local, REPO, WORK, 'reconciliation', {
            'item': {'kind': 'takeover', 'ref': old}, 'outcome': 'stopped-and-preserved',
            't3Facts': {'writer': old, 'state': 'stopped', 'childrenReconciled': True,
                        'evidence': 'synthetic observation'},
            'gitFacts': {'commitsPreserved': True, 'uncommittedPreserved': True,
                         'evidence': 'synthetic archive', 'worktreePath': '/synthetic/tree'}}, writer=old)
        records.append_event(self.local, REPO, WORK, 'writer-transfer', {
            'from': old, 'to': new, 'reconciliationRef': proof['seq'],
            'checkpoint': records.snapshot(self.local, REPO, WORK)}, writer=old)
        self.input()
        self.assertEqual(self.events()[-1]['writer'], new)

    def test_catalog_shape_fails_closed_but_unavailable_entries_parse(self):
        from t3compose.routing import CatalogError, parse_catalog
        valid = copy.deepcopy(self.catalog)
        valid['providers'].append({'providerInstanceId': 'synthetic-disabled', 'driverKind': 'unsupported',
            'models': [], 'canRunChildTask': False, 'canRunCrossProviderChildTask': False,
            'constraints': ['disabled']})
        self.assertEqual(len(parse_catalog(valid)), 7)
        for field, value in [('canRunChildTask', 1), ('canRunCrossProviderChildTask', 'true'),
                ('constraints', ''), ('constraints', [False]), ('models', {}), ('driverKind', None)]:
            with self.subTest(field=field, value=value):
                invalid = copy.deepcopy(valid)
                invalid['providers'][0][field] = value
                with self.assertRaises(CatalogError):
                    parse_catalog(invalid)
        for field in ('models', 'driverKind', 'constraints', 'canRunChildTask', 'canRunCrossProviderChildTask'):
            invalid = copy.deepcopy(valid)
            del invalid['providers'][0][field]
            with self.assertRaises(CatalogError):
                parse_catalog(invalid)
        for options in ({}, [{'id': 'x', 'values': [1]}], [{'id': 'x'}]):
            invalid = copy.deepcopy(valid)
            invalid['providers'][0]['models'][0]['options'] = options
            with self.assertRaises(CatalogError):
                parse_catalog(invalid)

    def test_catalog_read_failure_and_freshness_boundary(self):
        from t3compose.routing import CatalogError, load_catalog
        for age, valid in [(900, True), (901, False), (-1, False)]:
            if valid:
                self.assertEqual(load_catalog(self.catalog_path, stamp(NOW - timedelta(seconds=age)), NOW)[1],
                                 digest(self.catalog_path))
            else:
                with self.assertRaises(CatalogError):
                    load_catalog(self.catalog_path, stamp(NOW - timedelta(seconds=age)), NOW)
        raw = jsonio.canonical_json(self.catalog)
        deep = raw[:-1] + b',"metadata":' + b'[' * 100000 + b'0' + b']' * 100000 + b'}'
        self.assertLess(len(deep), 1024 * 1024)
        for content in (b'{', b'{}', b'{"providers":[],"providers":[]}', b'[' * 10000, deep):
            self.catalog_path.write_bytes(content)
            with self.assertRaises(CatalogError):
                load_catalog(self.catalog_path, stamp(NOW), NOW)
        self.catalog_path.unlink()
        with self.assertRaises(CatalogError):
            load_catalog(self.catalog_path, stamp(NOW), NOW)

    def test_duplicate_catalog_identifiers_fail_closed(self):
        from t3compose.routing import CatalogError, parse_catalog
        duplicate = copy.deepcopy(self.catalog)
        duplicate['providers'].append(copy.deepcopy(duplicate['providers'][0]))
        with self.assertRaises(CatalogError):
            parse_catalog(duplicate)
        duplicate = copy.deepcopy(self.catalog)
        options = duplicate['providers'][0]['models'][0]['options']
        options.append({'id': 'synthetic', 'values': [False]})
        options.append({'id': 'synthetic', 'values': [True]})
        with self.assertRaises(CatalogError):
            parse_catalog(duplicate)

    def test_evaluate_quota_chronology_reset_and_invalid_times(self):
        from t3compose.routing import evaluate_quota
        old = {'observedAt': stamp(NOW - timedelta(hours=2)), 'status': 'exhausted',
               'resetAt': None, 'evidence': 'synthetic'}
        available = {**old, 'status': 'available', 'observedAt': stamp(NOW - timedelta(hours=1))}
        for observations, expected in [([], 'unknown'), ([old], 'exhausted'),
                ([old, available], 'available'), ([available, old], 'available'),
                ([{**old, 'resetAt': stamp(NOW - timedelta(seconds=1))}], 'unknown'),
                ([{**old, 'resetAt': stamp(NOW)}], 'exhausted'),
                ([{**old, 'resetAt': stamp(NOW + timedelta(hours=1))}], 'exhausted')]:
            with self.subTest(observations=observations):
                self.assertEqual(evaluate_quota(observations, NOW), expected)
        for invalid in ({**old, 'observedAt': 'invalid'}, {**old, 'observedAt': stamp(NOW + timedelta(seconds=1))},
                        {**old, 'resetAt': stamp(NOW - timedelta(hours=3))}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                evaluate_quota([invalid], NOW)

    def test_import_and_register_are_effect_free(self):
        import t3compose.routing as routing
        with (patch.object(Path, 'open', side_effect=AssertionError('read')),
              patch.object(process, 'run', side_effect=AssertionError('process'))):
            importlib.reload(routing)
            names = cli.command_names()
            self.assertIn(('route', 'input'), names)
            self.assertNotIn(('route', 'judge'), names)
            self.assertNotIn(('route', 'select'), names)
