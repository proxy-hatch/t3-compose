"""Offline route input, saved-catalog and matrix-selection tests, with one guarded real-entry-point run."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from t3compose import cli, jsonio, process, records, routing, config, schema
from tests.support import OfflineTestCase, ROOT, guarded_helper_command
from tests.test_records import _valid_work, _routing_task, _valid_event_payloads, _valid_v3_routing_payloads

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
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.v3.example.json').read_text())
        self.state = {'activeProviderInstanceId': ACCOUNTS[0],
                      'quotaObservations': {}, 'note': 'synthetic'}
        self.catalog = {'providers': []}
        cells = [self.cfg['manager'], *self.cfg['helpers'].values()]
        cells += [c for lanes in self.cfg['matrix'].values() for stages in lanes.values() for c in stages.values()]
        selections = [s for c in cells for s in [c['primary'], *c['quotaFallback'],
                      *([c['prePass']] if 'prePass' in c else [])]]
        for instance, driver in [(MASTER, 'claudeAgent'), (DIRECT, 'codex'),
                                 ('pi-synth-glm', 'pi'), *[(a, 'antigravity') for a in ACCOUNTS]]:
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
        self.metadata = patch.object(cli, 'helper_info', return_value={'commit': 'e' * 40})
        self.metadata.start()
        self.addCleanup(self.metadata.stop)
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
        if code in (0, 4, 5):
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
    def judge(self, *, lane='BACKEND_LOGIC', criticality='NON_CRITICAL', scope='STANDARD',
              child=False, code=0, **changes):
        self.current = self.input()
        values = dict(input_ref=self.current['inputRef'], policy_digest=self.current['policyDigest'],
                      lane=lane, criticality=criticality, scope=scope, reason='Synthetic judgment')
        values.update(changes)
        return self.invoke('judge', *[p for k, v in values.items() for p in ('--' + k.replace('_', '-'), v)],
                           child=child, code=code)

    def select(self, role='worker', stage=1, code=0, child=False, **changes):
        values = dict(role=role, stage=stage, input_ref=self.current['inputRef'],
                      policy_digest=self.current['policyDigest'], catalog=self.catalog_path,
                      catalog_read_at=stamp(datetime.now(timezone.utc)))
        values.update(changes)
        return self.invoke('select', *[p for k, v in values.items() for p in ('--' + k.replace('_', '-'), v)],
                           child=child, code=code)

    def test_real_cli_three_successes_and_refusal(self):
        self.current = self.input(child=True)
        self.judge(child=True)
        self.select(child=True)
        cell = self.cfg['matrix']['worker']['backend-logic']['1']
        for target in [cell['primary'], *cell['quotaFallback']]:
            for instance in ACCOUNTS if 'providerPool' in target else [target['providerInstanceId']]:
                self.refuse(instance, target['model'])
        self.save()
        self.assertIsNotNone(self.select(child=True, code=5)['retryAfter'])

    def test_matrix_primaries_every_role_lane_stage(self):
        for role, lanes in self.cfg['matrix'].items():
            for lane, stages in lanes.items():
                for stage, cell in stages.items():
                    with self.subTest(role=role, lane=lane, stage=stage):
                        self.judge(lane='UI_FRONTEND' if lane == 'ui-frontend' else 'BACKEND_LOGIC',
                                   criticality='CORE' if lane == 'core' else 'NON_CRITICAL')
                        result = self.select(role, stage)
                        self.assertEqual(result['selection'], cell['primary'])
                        self.assertEqual((result['chosenIndex'], result['lane'], result['stage'],
                                          result['pass'], result['round']),
                                         (0, lane, int(stage), 'record' if role == 'reviewer' else None, 'first'))
                        for key, path in [('configDigest', self.cfg_path), ('catalogDigest', self.catalog_path),
                                          ('quotaStateDigest', self.state_path)]:
                            self.assertEqual(result[key], digest(path))
                        event = self.events()[-1]
                        schema.validate_event_data('routing-selection', event['data'])
                        self.assertEqual(result['selectionRef'], event['seq'])
                        self.assertEqual(result['targetsTried'][-1]['reason'], 'selected')
                        self.assertEqual('billing' in result, result['providerInstanceId'] == 'pi-synth-glm')

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
        self.judge()
        self.assertEqual(self.events()[-1]['writer'], new)
        self.select()
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

    def refuse(self, instance, model, minutes=1):
        entry = dict(providerInstanceId=instance, model=model, kind='usage-limit',
                     observedAt=stamp(datetime.now(timezone.utc) - timedelta(minutes=minutes)),
                     resetAt=None, evidence='Synthetic refusal')
        self.state.setdefault('refusals', []).append(entry)
        return entry

    def test_refusal_skip_expiry_and_latest_expiry(self):
        self.judge()
        cell = self.cfg['matrix']['worker']['backend-logic']['2']
        primary = cell['primary']
        self.refuse(primary['providerInstanceId'], primary['model'], 20)
        newer = self.refuse(primary['providerInstanceId'], primary['model'])
        self.save()
        result = self.select(stage=2)
        self.assertEqual(result['chosenIndex'], 1)
        self.assertEqual(result['selection'], cell['quotaFallback'][0])
        expiry = stamp(routing.utc(newer['observedAt']) + timedelta(minutes=30))
        self.assertEqual(result['targetsTried'][0]['reason'], 'live quota refusal until ' + expiry)
        for entry in self.state['refusals']:
            entry['observedAt'] = stamp(datetime.now(timezone.utc) - timedelta(minutes=30))
        self.save()
        self.assertEqual(self.select(stage=2)['chosenIndex'], 0)

    def test_fresh_refusal_published_during_state_read_is_accepted(self):
        self.judge()
        primary = self.cfg['matrix']['worker']['backend-logic']['2']['primary']
        load = routing.load_quota_state
        def publish_then_load(*args, **kwargs):
            self.refuse(primary['providerInstanceId'], primary['model'], minutes=0)
            self.save()
            return load(*args, **kwargs)
        with patch.object(routing, 'load_quota_state', side_effect=publish_then_load):
            result = self.select(stage=2)
        self.assertEqual(result['chosenIndex'], 1)
        self.assertTrue(result['targetsTried'][0]['reason'].startswith('live quota refusal until '))

    def test_pool_order_model_refusal_wrap_and_outside_pointer(self):
        self.judge()
        self.state['activeProviderInstanceId'] = ACCOUNTS[2]
        provider = next(p for p in self.catalog['providers'] if p['providerInstanceId'] == ACCOUNTS[2])
        provider['models'] = []
        model = self.cfg['matrix']['worker']['backend-logic']['1']['primary']['model']
        self.refuse(ACCOUNTS[0], model)
        self.save()
        result = self.select()
        self.assertEqual([t['account'] for t in result['targetsTried']], [ACCOUNTS[2], *ACCOUNTS[:2]])
        self.assertEqual(result['chosenIndex'], 0)
        self.assertEqual(result['targetsTried'][0]['reason'], 'model not offered: ' + model)
        self.assertTrue(result['targetsTried'][1]['reason'].startswith('live quota refusal until '))
        # A valid pointer in another pool starts this pool at its first account.
        self.cfg['providerPools']['other-synth-pool'] = {
            'selection': 'exhaust-in-order', 'providerInstanceIds': ['agy-synth-other']}
        self.state.update(activeProviderInstanceId='agy-synth-other', refusals=[])
        self.save()
        self.assertEqual(self.select()['chosenPoolAccount'], ACCOUNTS[0])

    def test_quota_record_moves_next_selection_start(self):
        self.judge()
        first = self.select()
        argv = ['--local-dir', str(self.local), 'quota', 'record', '--instance', ACCOUNTS[0],
                '--model', first['model'], '--kind', 'usage-limit', '--observed-at',
                stamp(datetime.now(timezone.utc)), '--evidence', 'Synthetic refusal']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(argv), 0)
        before = self.state_path.read_bytes()
        result = self.select()
        self.assertEqual(result['targetsTried'][0]['account'], ACCOUNTS[1])
        self.assertEqual(self.state_path.read_bytes(), before)

    def test_billing_and_role_blocks(self):
        self.judge()
        self.assertEqual(self.select('feature-lead')['billing'], 'per-token')
        self.assertEqual(self.events()[-1]['data']['billing'], 'per-token')
        self.cfg['roleBlocks'].append(dict(driverKind='pi', role='feature-lead',
                                          reason='Synthetic capability failure', evidence='Synthetic probe'))
        self.save()
        result = self.select('feature-lead')
        self.assertNotIn('billing', result)
        self.assertNotIn('billing', self.events()[-1]['data'])
        self.assertEqual(result['targetsTried'][0]['reason'], 'role block: Synthetic capability failure')
        self.judge(lane='UI_FRONTEND')
        self.assertEqual(self.select()['chosenIndex'], 0)
        self.assertEqual(self.select()['billing'], 'per-token')

    def test_version_two_refused_on_every_command(self):
        self.judge()
        self.cfg = json.loads((ROOT / 'docs/examples/agent-routing.example.json').read_text())
        self.save()
        self.input(code=3)
        self.invoke('judge', '--input-ref', 1, '--policy-digest', self.current['policyDigest'],
                    '--lane', 'BACKEND_LOGIC', '--criticality', 'CORE', '--scope', 'STANDARD',
                    '--reason', 'Synthetic', code=3)
        self.select(code=3)

    def test_mixed_history_and_classification_decisions(self):
        writer = 'lead:WORK-1@' + REPO
        old = records.record_routing_input(self.local, REPO, WORK, task=self.task, context=None,
                                          policy_digest='sha256:' + 'b' * 64, writer=writer)
        payloads = _valid_event_payloads()
        for kind in ('routing-judgment', 'routing-selection'):
            data = copy.deepcopy(payloads[kind])
            data.update(old)
            if kind == 'routing-selection':
                data['classificationRef'] = 2
            records.append_event(self.local, REPO, WORK, kind, data, writer=writer)
        judgment = self.judge()
        newer_old = copy.deepcopy(payloads['routing-judgment'])
        newer_old.update(self.current)
        records.append_event(self.local, REPO, WORK, 'routing-judgment', newer_old, writer=writer)
        self.assertEqual(self.select()['classificationRef'], judgment['seq'])
        classification = _valid_v3_routing_payloads()['routing-classification']
        classification.update(self.current, riskFacts=[], outcome='accepted')
        classification['answers']['scope']['choice'] = 'STANDARD'
        classification['answers']['scope']['probabilities'] = {
            'STANDARD': 1, 'COMPLEX': 0, 'INSUFFICIENT_CONTEXT': 0}
        classification['answers']['lane']['choice'] = 'MIXED'
        classification['answers']['lane']['probabilities'] = {'BACKEND_LOGIC': 0, 'UI_FRONTEND': 0, 'MIXED': 1}
        classification['criteriaDigest'] = 'sha256:' + hashlib.sha256(
            jsonio.canonical_json(classification['criteria'])).hexdigest()
        def append():
            return records.append_event(self.local, REPO, WORK, 'routing-classification',
                                        classification, writer=writer)
        accepted = append()
        self.assertEqual(self.select()['classificationRef'], accepted['seq'])
        self.assertEqual(self.select()['lane'], 'backend-logic')
        classification['answers']['lane']['confidence'] = 0.59
        append()
        self.assertIn('record a judgment', self.select(code=4)['reason'])
        for answer in classification['answers'].values():
            answer['confidence'] = 1.0
        for outcome in ('defaulted', 'retry-with-context'):
            with self.subTest(outcome=outcome):
                classification['outcome'] = outcome
                append()
                before = self.events()
                self.select(code=4)
                self.assertEqual(self.events(), before)

    def test_only_version_two_decision_is_ignored(self):
        self.current = self.input()
        decision = copy.deepcopy(_valid_event_payloads()['routing-judgment'])
        decision.update(self.current)
        records.append_event(self.local, REPO, WORK, 'routing-judgment', decision,
                             writer='lead:WORK-1@' + REPO)
        before = self.events()
        self.select(code=4)
        self.assertEqual(self.events(), before)

    def test_dry_cell_retry_full_walk_and_no_mutation(self):
        self.judge()
        cell = self.cfg['matrix']['worker']['backend-logic']['1']
        entries = []
        for selection in [cell['primary'], *cell['quotaFallback']]:
            for instance in ACCOUNTS if 'providerPool' in selection else [selection['providerInstanceId']]:
                entries.append(self.refuse(instance, selection['model'], len(entries) + 1))
        self.save()
        before, events = self.state_path.read_bytes(), self.events()
        result = self.select(code=5)
        self.assertEqual(len(result['targetsTried']), 5)
        self.assertEqual([t['index'] for t in result['targetsTried']], [0, 0, 0, 1, 2])
        expected = min(routing.utc(e['observedAt']) + timedelta(minutes=30) for e in entries)
        self.assertEqual(result['retryAfter'], stamp(expected))
        self.assertEqual(self.events(), events)
        self.assertEqual(self.state_path.read_bytes(), before)
        self.catalog['providers'] = []
        self.save()
        self.assertIsNone(self.select(code=5)['retryAfter'])

    def test_floors_stages_and_unknown_fact(self):
        self.task['riskFacts'] = ['document-change', 'credentials-or-guardrail']
        self.judge()
        result = self.select(code=4)
        self.assertEqual(result['startingStage'], 2)
        result = self.select('reviewer', stage=2)
        self.assertEqual((result['lane'], result['criticality'], result['scope'], result['floorsApplied']),
                         ('core', 'CORE', 'COMPLEX', ['credentials-or-guardrail']))
        self.task['riskFacts'] = ['shared-interface', 'document-change']
        self.judge(scope='COMPLEX')
        self.assertEqual(self.select('reviewer', stage=3)['lane'], 'core')
        self.task['riskFacts'] = ['document-change']
        self.judge()
        self.assertEqual(self.select()['floorsApplied'], [])
        self.task['riskFacts'] = ['unknown-synth-fact']
        self.judge()
        self.select(code=4)

    def test_catalog_failures_and_ordered_unavailability_checks(self):
        self.judge()
        for changes in ({'catalog_read_at': stamp(datetime.now(timezone.utc) - timedelta(minutes=16))},
                        {'catalog_read_at': '2999-01-01T00:00:00Z'}, {'catalog': self.temp / 'missing'}):
            self.select(code=4, **changes)
        self.catalog_path.write_text('{')
        self.select(code=4)
        self.save()
        primary = next(p for p in self.catalog['providers'] if p['providerInstanceId'] == DIRECT)
        original = copy.deepcopy(primary)
        for changes, reason in [
                ({'canRunChildTask': False}, 'canRunChildTask is false'),
                ({'canRunCrossProviderChildTask': False}, 'canRunCrossProviderChildTask is false'),
                ({'constraints': ['x\n' + 'y' * 600]}, 'constraints: x ' + 'y' * 600),
                ({'models': []}, 'model not offered: gpt-6.1-sol')]:
            primary.update(changes)
            self.save()
            result = self.select(stage=2)
            self.assertEqual(result['targetsTried'][0]['reason'], reason[:512])
            primary.clear()
            primary.update(copy.deepcopy(original))
        model = next(m for m in primary['models'] if m['id'] == 'gpt-6.1-sol')
        model['options'] = [{'id': 'reasoningEffort', 'values': [True]}]
        self.save()
        self.assertEqual(self.select(stage=2)['targetsTried'][0]['reason'],
                         'option not offered: reasoningEffort')
        pi = next(p for p in self.catalog['providers'] if p['driverKind'] == 'pi')
        pi['driverKind'] = 'unknown-synth-driver'
        self.save()
        self.judge(lane='UI_FRONTEND')
        self.assertEqual(self.select()['targetsTried'][0]['reason'],
                         'driver kind not allowed: unknown-synth-driver')
        pi['driverKind'] = 'pi'
        # Both manager primary and fallback must be subscription providers when present.
        for instance in (MASTER, DIRECT):
            provider = next(p for p in self.catalog['providers'] if p['providerInstanceId'] == instance)
            provider['driverKind'] = 'pi'
            self.save()
            self.select(code=3)
            provider['driverKind'] = 'claudeAgent' if instance == MASTER else 'codex'

    def test_rounds_current_input_append_failure_and_state_errors(self):
        self.judge()
        for role, round_ in [('reviewer', 'corrections'), ('worker', 're-review'),
                             ('feature-lead', 'corrections'), ('feature-lead', 're-review')]:
            self.select(role, round=round_, code=2)
        for role, round_ in [('worker', 'corrections'), ('reviewer', 're-review')]:
            self.assertEqual(self.select(role, round=round_)['round'], round_)
        self.select(input_ref=0, code=2)
        self.select(policy_digest='bad', code=2)
        self.select(input_ref=999, code=4)
        self.select(policy_digest='sha256:' + 'a' * 64, code=4)
        before = self.events()
        with patch.object(records, 'append_event', side_effect=records.RecordError('synthetic-secret')):
            self.select(code=2)
        self.assertEqual(self.events(), before)
        self.state['refusals'] = [dict(providerInstanceId=DIRECT, model='gpt-6.1-sol',
            kind='usage-limit', observedAt='2999-01-01T00:00:00Z', resetAt=None, evidence='Synthetic')]
        self.save()
        self.select(code=3)
        self.state_path.write_text('{')
        self.select(code=3)
        self.save()
        # A new input invalidates the supplied older reference, even with identical content.
        self.input()
        self.select(code=4)

    def test_judgment_freshness_arguments_and_missing_decision(self):
        self.current = self.input()
        self.select(code=4)
        values = ['--lane', 'BACKEND_LOGIC', '--criticality', 'CORE',
                  '--scope', 'STANDARD', '--reason', 'Synthetic']
        for ref, policy, code in [(0, self.current['policyDigest'], 2),
                                  (1, 'bad', 2), (999, self.current['policyDigest'], 4),
                                  (1, 'sha256:' + 'a' * 64, 4)]:
            self.invoke('judge', '--input-ref', ref, '--policy-digest', policy, *values, code=code)
        self.cfg['classification']['ruleVersion'] = 'synthetic-new-policy'
        self.save()
        self.invoke('judge', '--input-ref', 1, '--policy-digest', self.current['policyDigest'],
                    *values, code=4)
        self.select(code=4)

    def test_deeply_nested_config_and_state_are_exit_three(self):
        self.judge()
        for path in (self.cfg_path, self.state_path):
            path.write_bytes(b'[' * 2000 + b'0' + b']' * 2000)
            self.select(code=3)
            self.save()

    def test_import_and_register_are_effect_free(self):
        import t3compose.routing as routing
        with (patch.object(Path, 'open', side_effect=AssertionError('read')),
              patch.object(process, 'run', side_effect=AssertionError('process'))):
            importlib.reload(routing)
            names = cli.command_names()
            self.assertIn(('route', 'input'), names)
            self.assertIn(('route', 'judge'), names)
            self.assertIn(('route', 'select'), names)
