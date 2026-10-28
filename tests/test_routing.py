"""Offline routing acceptance, with guarded real-entry-point coverage."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
from unittest.mock import patch

from t3compose import cli, config, jsonio, process, records
from tests.support import OfflineTestCase, ROOT, guarded_helper_command
from tests.test_records import _valid_work, _routing_task, _valid_event_payloads

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
        self.source = None

    def write(self, path, value):
        path.write_bytes(jsonio.canonical_json(value))

    def save(self):
        for path, value in [(self.cfg_path, self.cfg), (self.state_path, self.state),
                            (self.catalog_path, self.catalog)]:
            self.write(path, value)

    def provider(self, instance):
        return next(p for p in self.catalog['providers'] if p['providerInstanceId'] == instance)

    def invoke(self, leaf, *options, child=True, code=0):
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
            data = json.loads(out)
            if code:
                self.assertTrue(data['reason'])
            return data
        self.assertNotIn(str(self.temp), err)
        return json.loads(out) if out else None

    def input(self, child=False):
        self.write(self.task_path, self.task)
        self.source = self.invoke('input', '--input', self.task_path, child=child)
        return self.source

    def judge(self, difficulty='MEDIUM', domain='SOFTWARE_ENGINEERING', **kwargs):
        if self.source is None:
            self.input()
        return self.invoke('judge', '--input-ref', self.source['inputRef'], '--policy-digest',
                           self.source['policyDigest'], '--domain', domain, '--difficulty', difficulty,
                           '--reason', 'synthetic manager judgment', **kwargs)

    def classify(self, **changes):
        if self.source is None:
            self.input()
        data = _valid_event_payloads()['routing-classification']
        data.update(self.source, criteria={k: self.cfg['classification'][k]['criteria']
                    for k in ('domain', 'difficulty')}, riskFacts=self.task['riskFacts'], **changes)
        data['criteriaDigest'] = 'sha256:' + hashlib.sha256(jsonio.canonical_json(data['criteria'])).hexdigest()
        return records.append_event(self.local, REPO, WORK, 'routing-classification', data,
                                    writer='lead:WORK-1@' + REPO)

    def select(self, role='worker', round='first', scope='code', escalate=False, **kwargs):
        opts = ['--role', role, '--round', round, '--review-scope', scope,
                '--input-ref', self.source['inputRef'], '--policy-digest', self.source['policyDigest'],
                '--catalog', self.catalog_path, '--catalog-read-at', stamp(datetime.now(timezone.utc))]
        if escalate:
            opts.append('--escalate')
        return self.invoke('select', *opts, **kwargs)

    def events(self):
        return records.load_work(self.local, REPO, WORK)['events']

    def selected(self, **kwargs):
        before = len(self.events())
        state = self.state_path.read_bytes()
        output = self.select(**kwargs)
        events = self.events()
        self.assertEqual(len(events), before + 1)
        self.assertEqual(events[-1]['kind'], 'routing-selection')
        data = events[-1]['data']
        self.assertEqual(output['selectionRef'], events[-1]['seq'])
        self.assertEqual(output['providerInstanceId'], data['chosenPoolAccount'] or data['selection']['providerInstanceId'])
        self.assertEqual(output['options'], data['selection']['options'])
        self.assertEqual(output['model'], data['selection']['model'])
        self.assertEqual(self.state_path.read_bytes(), state)
        self.assertEqual(data['configDigest'], digest(self.cfg_path))
        self.assertEqual(data['catalogDigest'], digest(self.catalog_path))
        self.assertEqual(data['quotaStateDigest'], digest(self.state_path))
        self.assertEqual(data['policyDigest'], self.source['policyDigest'])
        return data

    def refused(self, code=4, **kwargs):
        before, state = self.events(), self.state_path.read_bytes()
        output = self.select(code=code, **kwargs)
        self.assertEqual(self.events(), before)
        self.assertEqual(self.state_path.read_bytes(), state)
        return output


class OfflineRouting(OfflineRoutingFixture):
    def test_three_real_cli_leaves_and_full_provenance(self):
        self.input(child=True)
        self.judge(child=True)
        data = self.selected(child=True)
        self.assertEqual([e['kind'] for e in self.events()],
                         ['routing-input', 'routing-judgment', 'routing-selection'])
        first, judgment, selection = self.events()
        self.assertIsNone(first['data']['context'])
        self.assertEqual(judgment['data']['basis'], 'manual')
        self.assertEqual(selection['writer'], 'lead:WORK-1@' + REPO)
        self.assertEqual(data['classificationRef'], judgment['seq'])
        self.assertEqual(data['inputDigest'], first['data']['inputDigest'])
        self.assertEqual(data['chosenPoolAccount'], ACCOUNTS[0])
        self.assertEqual(data['selection'], self.cfg['modelPreferences']['software-engineering']['medium'])
        self.assertEqual((data['tier'], data['round'], data['role']), ('medium', 'first', 'worker'))
        self.assertEqual(data['floorsApplied'], [])
        self.assertFalse(data['escalation'])
        self.assertIsNone(data['reviewScope'])
        self.assertIsNone(data['fallbackReason'])
        self.assertTrue(data['availabilityReason'])
        self.assertNotIn('billing', data)

    def test_input_validation_and_config_failure_codes(self):
        for task in [{}, {**self.task, 'intent': 'x' * 16384}, {**self.task, 'extra': True}]:
            self.write(self.task_path, task)
            self.invoke('input', '--input', self.task_path, code=2)
        self.assertEqual(self.events(), [])
        self.cfg['version'] = 1
        self.save()
        self.write(self.task_path, self.task)
        self.invoke('input', '--input', self.task_path, code=3)
        self.assertEqual(self.events(), [])

    def test_latest_input_and_explicit_policy_for_both_decision_types(self):
        for decision in (self.judge, self.classify):
            with self.subTest(decision=decision.__name__):
                self.input()
                decision()
                old = self.source.copy()
                self.task['intent'] += ' changed'
                self.input()
                self.refused()
                current = self.source
                self.source = old
                self.refused()
                self.judge(code=4)
                self.source = current
                decision()
                self.selected()
                self.source = {**current, 'policyDigest': 'sha256:' + '0' * 64}
                self.refused()
                self.judge(code=4)

    def test_policy_changes_invalidate_judgment_and_classification(self):
        for decision in (self.judge, self.classify):
            for field in ('criteria', 'instructions', 'threshold', 'floor'):
                with self.subTest(decision=decision.__name__, field=field):
                    self.input()
                    decision()
                    if field == 'criteria':
                        self.cfg['classification']['domain']['criteria']['OTHER'] += ' changed'
                    elif field == 'instructions':
                        self.cfg['classification']['difficulty']['instructions'] += ' changed'
                    elif field == 'threshold':
                        self.cfg['jev']['minConfidence'] += 0.01
                    else:
                        facts = self.cfg['riskFacts']['shared-interface']
                        facts['floor'] = 'challenging' if facts['floor'] == 'medium' else 'medium'
                    self.save()
                    self.refused()
                    self.judge(code=4)
                    self.input()
                    decision()
                    self.selected()

    def test_fixed_helper_and_unaccepted_or_low_confidence_classification(self):
        self.input()
        data = self.selected(role='text-helper')
        self.assertIsNone(data['classificationRef'])
        self.assertEqual(data['selection'], self.cfg['textHelper'])
        self.refused()
        self.classify(outcome='manager-judgment')
        self.refused()
        event = _valid_event_payloads()['routing-classification']
        event['answers']['difficulty']['confidence'] = 0.1
        self.classify(answers=event['answers'])
        self.refused()
        self.judge()
        self.selected()

    def test_floors_and_difficulty_maximum(self):
        for facts, difficulty, expected in [([], 'EASY', 'easy'), ([], 'CHALLENGING', 'challenging'),
                (['shared-interface'], 'EASY', 'medium'),
                (['shared-interface', 'credentials-or-guardrails'], 'EASY', 'challenging')]:
            with self.subTest(facts=facts, difficulty=difficulty):
                self.task['riskFacts'] = facts
                self.input()
                self.judge(difficulty)
                data = self.selected()
                self.assertEqual(data['tier'], expected)
                self.assertEqual(data['floorsApplied'], facts)
        self.task['riskFacts'] = ['unknown-fact']
        self.input()
        self.judge()
        self.refused()

    def test_corrections_escalation_highest_same_round_and_base_max(self):
        self.task['riskFacts'] = ['credentials-or-guardrails']
        self.judge('EASY')
        self.assertEqual(self.selected()['tier'], 'challenging')
        self.refused(round='corrections', escalate=True)
        self.assertEqual(self.selected(round='corrections')['tier'], 'easy')
        raised = self.selected(round='corrections', escalate=True)
        self.assertEqual((raised['tier'], raised['floorsApplied']), ('medium', []))
        self.selected(round='corrections')  # A later easy choice cannot lower the history maximum.
        self.assertEqual(self.selected(round='corrections', escalate=True)['tier'], 'challenging')
        self.refused(round='corrections', escalate=True)
        self.refused(escalate=True)

    def test_configured_round_tiers_reload_and_escalation_base_max(self):
        self.judge('EASY')
        self.refused(escalate=True)
        self.selected(round='corrections')
        self.cfg['roundTiers']['corrections'] = 'challenging'
        self.cfg['roundTiers']['correctionsReview'] = 'challenging'
        self.cfg['roundTiers']['documentReview'] = 'challenging'
        self.save()
        self.assertEqual(self.selected(round='corrections', escalate=True)['tier'], 'challenging')
        for scope in ('document', 'corrections-delta'):
            self.assertEqual(self.selected(role='reviewer', scope=scope)['tier'], 'challenging')

    def test_lead_and_reviewer_follow_task_escalation_across_inputs(self):
        self.judge('MEDIUM')
        self.assertEqual(self.selected(role='feature-lead')['tier'], 'medium')
        self.selected()
        self.selected(escalate=True)
        self.assertEqual(self.selected(role='feature-lead')['tier'], 'challenging')
        self.assertEqual(self.selected(role='reviewer')['tier'], 'challenging')
        for scope in ('document', 'corrections-delta'):
            data = self.selected(role='reviewer', scope=scope)
            self.assertEqual((data['tier'], data['reviewScope']), ('medium', scope))
        self.input()
        self.judge('EASY')
        self.assertEqual(self.selected(role='feature-lead')['tier'], 'challenging')
        self.invoke('select', '--role', 'worker', code=2)
        self.refused(role='reviewer', escalate=True, code=2)

    def test_feature_lead_easy_task_still_requires_medium(self):
        self.judge('EASY')
        self.assertEqual(self.selected(role='feature-lead')['tier'], 'medium')

    def test_pool_role_fallback_and_nonprimary_target(self):
        self.judge()
        for role in ('reviewer', 'feature-lead'):
            data = self.selected(role=role)
            self.assertEqual(data['selection']['providerInstanceId'], DIRECT)
            self.assertIn('role', data['fallbackReason'])
            self.assertEqual(data['skippedPoolAccounts'], [])
        self.cfg['modelPreferences']['software-engineering']['medium'] = copy.deepcopy(
            self.cfg['modelPreferences']['non-engineering']['medium'])
        self.save()
        self.assertEqual(self.selected()['selection']['providerInstanceId'], MASTER)

    def test_pool_target_role_limit_and_never_follow_targets_chain(self):
        pool = self.cfg['modelPreferences']['software-engineering']['medium']
        direct = self.cfg['availabilityFallback']['chains'][0]['to'][0]
        self.cfg['modelPreferences']['software-engineering']['medium'] = direct
        self.cfg['availabilityFallback']['chains'] = [
            {'tier': 'medium', 'from': direct, 'to': [pool]},
            {'tier': 'medium', 'from': pool, 'to': [self.cfg['modelPreferences']['non-engineering']['medium']]}]
        self.provider(DIRECT)['canRunChildTask'] = False
        self.save()
        self.judge()
        self.assertEqual(self.selected()['chosenPoolAccount'], ACCOUNTS[0])
        self.refused(role='reviewer', code=5)
        self.refused(role='feature-lead', code=5)
        self.provider(ACCOUNTS[0])['canRunChildTask'] = False
        self.save()
        stopped = self.refused(code=4)
        self.assertIn('fallbackReason', stopped)
        self.assertIn('canRunChildTask', stopped['fallbackReason'])
        self.provider(ACCOUNTS[0])['canRunChildTask'] = True
        for account in ACCOUNTS:
            self.provider(account)['models'] = []
        self.save()
        output = self.refused(code=5)
        self.assertEqual(len(output['skippedPoolAccounts']), 3)
        self.cfg['availabilityFallback']['chains'][0]['tier'] = 'challenging'
        self.save()
        self.refused(code=3)

    def test_direct_target_roles_wrong_tier_and_no_second_fallback(self):
        medium = self.cfg['modelPreferences']['non-engineering']['medium']
        target = self.cfg['availabilityFallback']['chains'][0]['to'][0]
        third = {**medium, 'model': 'synthetic-third'}
        self.cfg['availabilityFallback']['chains'] = [
            {'tier': 'medium', 'from': medium, 'to': [{**target, 'roles': ['worker']}]},
            {'tier': 'medium', 'from': target, 'to': [third]}]
        self.provider(MASTER)['canRunChildTask'] = False
        self.save()
        self.judge(domain='OTHER')
        self.selected()
        self.refused(role='reviewer', code=5)
        self.provider(DIRECT)['canRunCrossProviderChildTask'] = False
        self.save()
        self.refused(code=5)
        self.cfg['availabilityFallback']['chains'][0]['tier'] = 'challenging'
        self.save()
        self.refused(code=5)

    def test_subscription_driver_authorization_and_master_refusal(self):
        self.judge(domain='OTHER')
        for driver in ('codex', 'claudeAgent', 'antigravity'):
            self.provider(MASTER)['driverKind'] = driver
            self.save()
            self.selected()
        self.provider(MASTER)['driverKind'] = 'claudeAgent'
        self.cfg['modelPreferences']['software-engineering']['medium'] = self.cfg['availabilityFallback']['chains'][0]['to'][0]
        self.input()
        self.judge()
        for driver in ('pi', 'unknown-driver'):
            self.provider(DIRECT)['driverKind'] = driver
            self.save()
            for role in ('worker', 'reviewer', 'feature-lead'):
                if role == 'worker':
                    self.assertIsNotNone(self.selected(role=role)['fallbackReason'])
                else:
                    self.refused(role=role, code=5)
            self.provider(MASTER)['driverKind'] = driver
            self.save()
            self.refused(code=3)
            self.provider(MASTER)['driverKind'] = 'claudeAgent'

    def test_active_preflight_stops_before_walk(self):
        self.judge()
        original = copy.deepcopy(self.catalog)
        for issue in ('missing', 'canRunChildTask', 'canRunCrossProviderChildTask', 'constraints'):
            self.catalog = copy.deepcopy(original)
            active = self.provider(ACCOUNTS[0])
            if issue == 'missing':
                self.catalog['providers'].remove(active)
            else:
                active[issue] = ['synthetic capacity refusal'] if issue == 'constraints' else False
            self.save()
            output = self.refused()
            self.assertEqual(output['skippedPoolAccounts'], [])
            self.assertIn(issue if issue != 'missing' else 'missing provider', output['reason'])

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

    def test_catalog_read_failure_freshness_boundary_and_bounded_reason(self):
        from t3compose.routing import CatalogError, load_catalog
        for age, valid in [(900, True), (901, False), (-1, False)]:
            if valid:
                self.assertEqual(load_catalog(self.catalog_path, stamp(NOW - timedelta(seconds=age)), NOW)[1],
                                 digest(self.catalog_path))
            else:
                with self.assertRaises(CatalogError):
                    load_catalog(self.catalog_path, stamp(NOW - timedelta(seconds=age)), NOW)
        self.judge()
        for content in (b'{', b'{}', b'{"providers":[],"providers":[]}', b'[' * 10000):
            self.catalog_path.write_bytes(content)
            self.refused()
        self.catalog_path.unlink()
        self.refused()
        self.provider(ACCOUNTS[0])['constraints'] = ['x' * 2000]
        self.save()
        self.assertLessEqual(len(self.refused()['reason']), 512)

    def test_complete_deep_catalog_returns_reason_without_mutation(self):
        self.judge()
        raw = jsonio.canonical_json(self.catalog)
        content = raw[:-1] + b',"metadata":' + b'[' * 100000 + b'0' + b']' * 100000 + b'}'
        self.assertLess(len(content), 1024 * 1024)
        self.catalog_path.write_bytes(content)
        result = self.refused()
        self.assertLessEqual(len(result['reason']), 512)

    def test_missing_model_options_and_unavailable_later_account_are_skips(self):
        self.judge()
        self.provider(ACCOUNTS[0])['models'] = []
        self.provider(ACCOUNTS[1])['canRunCrossProviderChildTask'] = False
        self.save()
        data = self.selected()
        self.assertEqual(data['chosenPoolAccount'], ACCOUNTS[2])
        self.assertIn('model not offered', data['skippedPoolAccounts'][0]['reason'])
        self.assertIn('canRunCrossProviderChildTask', data['skippedPoolAccounts'][1]['reason'])
        self.cfg['modelPreferences']['software-engineering']['medium']['options'] = {'effort': 'high'}
        self.cfg['availabilityFallback']['chains'][0]['from']['options'] = {'effort': 'high'}
        self.save()
        data = self.selected()
        self.assertIsNone(data['chosenPoolAccount'])
        self.assertEqual(len(data['skippedPoolAccounts']), 3)
        self.assertIn('option', data['skippedPoolAccounts'][2]['reason'])

    def test_malformed_references_and_damaged_records_are_input_errors(self):
        self.judge()
        original = self.source.copy()
        for key, value in [('inputRef', 0), ('inputRef', -1), ('policyDigest', 'invalid')]:
            self.source = {**original, key: value}
            self.refused(code=2)
            self.judge(code=2)
        self.source = original
        _, directory = records.location(self.local, REPO, WORK)
        event = next((directory / 'events').iterdir())
        event.write_text('{')
        self.select(code=2)

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
        self.input(child=True)
        self.judge()
        self.selected()
        self.assertEqual([e['writer'] for e in self.events()[-3:]], [new, new, new])

    def test_no_subscription_fallback_can_authorize_pool_or_direct_other_drivers(self):
        self.judge()
        for account in ACCOUNTS:
            self.provider(account)['driverKind'] = 'pi'
        self.provider(DIRECT)['driverKind'] = 'unrecognized'
        self.save()
        for role in ('worker', 'reviewer', 'feature-lead', 'text-helper'):
            output = self.refused(role=role, code=5)
            self.assertTrue(output['reason'])
        self.provider(DIRECT)['driverKind'] = 'codex'
        self.save()
        self.assertEqual(self.selected()['selection']['providerInstanceId'], DIRECT)
        self.cfg['modelPreferences']['software-engineering']['medium'] = self.cfg['availabilityFallback']['chains'][0]['to'][0]
        self.provider(DIRECT)['driverKind'] = 'antigravity'
        self.save()
        self.refused(role='reviewer', code=5)

    def test_exact_option_types_and_duplicate_catalog_identifiers(self):
        from t3compose.routing import CatalogError, parse_catalog
        self.judge(domain='OTHER')
        preference = self.cfg['modelPreferences']['non-engineering']['medium']
        preference['options'] = {'fastMode': False}
        model = next(m for m in self.provider(MASTER)['models'] if m['id'] == preference['model'])
        model['options'] = [{'id': 'fastMode', 'values': ['false']}]
        self.save()
        self.refused(code=5)
        model['options'][0]['values'] = [False, True]
        self.save()
        self.selected()
        self.catalog['providers'].append(copy.deepcopy(self.catalog['providers'][0]))
        with self.assertRaises(CatalogError):
            parse_catalog(self.catalog)
        self.catalog['providers'].pop()
        model['options'].append(copy.deepcopy(model['options'][0]))
        self.save()
        self.refused()

    def test_import_and_register_are_effect_free(self):
        import t3compose.routing as routing
        with (patch.object(Path, 'open', side_effect=AssertionError('read')),
              patch.object(process, 'run', side_effect=AssertionError('process'))):
            importlib.reload(routing)
            self.assertIn(('route', 'select'), cli.command_names())


class SimulatedRoutingQuota(OfflineRoutingFixture):
    def test_account_walk_wraps_retains_skips_and_direct_fallback(self):
        self.state['activeProviderInstanceId'] = ACCOUNTS[2]
        self.state['quotaObservations'] = {ACCOUNTS[0]: [
            {'observedAt': '2000-01-01T00:00:00Z', 'status': 'exhausted', 'resetAt': None,
             'evidence': 'synthetic quota refusal'}]}
        self.provider(ACCOUNTS[2])['models'] = []
        self.save()
        for difficulty in ('MEDIUM', 'EASY'):
            self.judge(difficulty)
            data = self.selected()
            self.assertEqual(data['chosenPoolAccount'], ACCOUNTS[1])
            self.assertEqual([s['providerInstanceId'] for s in data['skippedPoolAccounts']],
                             [ACCOUNTS[2], ACCOUNTS[0]])
            self.assertIn('exhausted', data['skippedPoolAccounts'][1]['reason'])
        self.state['quotaObservations'][ACCOUNTS[1]] = self.state['quotaObservations'][ACCOUNTS[0]]
        self.save()
        self.judge()
        data = self.selected()
        self.assertEqual(data['selection']['providerInstanceId'], DIRECT)
        self.assertIsNone(data['chosenPoolAccount'])
        self.assertEqual(len(data['skippedPoolAccounts']), 3)
        self.judge('EASY')
        self.refused(code=5)

    def test_quota_unknown_reset_later_available_and_chronology(self):
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
        self.judge()
        self.state['quotaObservations'][ACCOUNTS[0]] = [old]
        self.save()
        self.assertEqual(self.selected()['chosenPoolAccount'], ACCOUNTS[1])
        self.state['quotaObservations'][ACCOUNTS[0]].append(available)
        self.save()
        self.assertEqual(self.selected()['chosenPoolAccount'], ACCOUNTS[0])
        self.state['quotaObservations'][ACCOUNTS[0]] = [{**old, 'observedAt': 'invalid'}]
        self.save()
        self.refused(code=3)

    def test_exhausted_pool_target_cannot_follow_its_chain_and_reset_restores_account(self):
        pool = self.cfg['modelPreferences']['software-engineering']['medium']
        direct = self.cfg['availabilityFallback']['chains'][0]['to'][0]
        self.cfg['modelPreferences']['software-engineering']['medium'] = direct
        self.provider(DIRECT)['canRunChildTask'] = False
        observation = {'observedAt': '2000-01-01T00:00:00Z', 'status': 'exhausted',
                       'resetAt': None, 'evidence': 'synthetic quota refusal'}
        self.state['quotaObservations'] = {a: [observation.copy()] for a in ACCOUNTS}
        self.save()
        self.judge()
        output = self.refused(code=5)
        self.assertEqual(len(output['skippedPoolAccounts']), 3)
        self.assertTrue(all('exhausted' in s['reason'] for s in output['skippedPoolAccounts']))
        self.state['quotaObservations'][ACCOUNTS[1]][0]['resetAt'] = '2001-01-01T00:00:00Z'
        self.save()
        data = self.selected()
        self.assertEqual(data['selection'], pool)
        self.assertEqual(data['chosenPoolAccount'], ACCOUNTS[1])
        self.assertIsNotNone(data['fallbackReason'])
        self.assertEqual(len(data['skippedPoolAccounts']), 1)
