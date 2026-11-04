"""Synthetic public-text authority, attestation and occurrence-join checks.

No scanner, repository or private value is involved: every rule, publication,
review and proof here is explicitly synthetic.
"""
import ast
import copy
import importlib.util
import itertools
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT
from t3compose import privacy_dispositions as pd, process, records

REPO, COMMIT, BLOB = 'example.invalid/team/repo', 'a' * 40, 'b' * 40
QUOTE = 'SYNTHETIC ONLY: standing rule.'
UNRELATED = dict(question='unrelated scheduling question')


def sha(char):
    return 'sha256:' + char * 64


class OfflinePublicTextEvidence(OfflineTestCase):
    def setUp(self):
        self.local = self.temp / 'state'
        self.streams = itertools.count(1)
        self.occurrence = dict(patternLine=1, byteOffset=4, byteLength=5, literalDigest=sha('3'),
                               publicBlobOid=BLOB, publicContentDigest=sha('4'))
        self.build()

    def decision(self, work, action='approve', context=None, *, rule='synthetic-rule', owner='owner',
                 answer=QUOTE, date='2026-11-04', question=None):
        question = question or 'privacy-public-text/v1 %s %s %s' % (action, rule, context or self.context)
        return records.append_event(self.local, self.repo, work, 'decision', dict(
            question=question, answer=answer, date=date, decidedBy=owner), writer='manager@' + self.repo)

    def build(self, rule=None, event=None, before=(), repo=REPO):
        """Fresh consistent evidence in its own authority stream; each override breaks one thing."""
        self.work, self.repo = work, repo = 'AUTH%d' % next(self.streams), repo
        records.create_work(self.local, repo, work, dict(
            workId=work, linearIssue=None, repository=repo, targetBranch='main', reviewBase=COMMIT,
            scope=['x'], designRefs=[], ownedPaths=['x'], dependencies=[],
            acceptance=[dict(id='AC', text='Synthetic')],
            requiredChecks=[dict(id='CHK', command='synthetic', kind='fast')], writer='manager@' + repo),
            writer='manager@' + repo)
        rule = {**dict(schemaVersion=1, ruleId='synthetic-rule', repository=REPO, scope=pd.SCOPE,
                       fields=['file', 'message'], policyVersion=pd.POLICY_VERSION,
                       policyDigest=pd.digest(pd.DESCRIPTOR), authorityWorkId=work,
                       approvalSeq=len(before) + 1,
                       source=dict(sourceRef='synthetic:owner-message', date='2026-11-04', quote=QUOTE)),
                **(rule or {})}
        self.context = pd.digest({k: v for k, v in rule.items() if k != 'approvalSeq'})
        for item in before:
            self.decision(work, **item)
        if event is not False:
            self.decision(work, **(event or {}))
        attest = dict(schemaVersion=1, ruleId='synthetic-rule', ruleContext=self.context,
                      verifier='synthetic-independent-reviewer', result='verified')
        self.files = dict(
            rule=rule,
            publication=dict(attest, sourceRef='synthetic:publication-observation',
                             method='prior-publication', repository=REPO, publicCommit=COMMIT),
            dispositions=dict(attest, sourceRef='synthetic:occurrence-review', method='occurrence-review',
                              publicCommit=COMMIT, listDigest=sha('1'), entries=[dict(
                                  field='file', inputDigest=sha('2'),
                                  occurrences=[dict(self.occurrence, scope=pd.SCOPE)])]))
        self.policy = dict(schemaVersion=1, policyVersion=pd.POLICY_VERSION, authorityWorkId=work,
                           ruleFile='rule', ruleDigest=sha('a'), publicationFile='publication',
                           publicationDigest=sha('b'), dispositionsFile='dispositions',
                           dispositionsDigest=sha('c'), artifactPolicyFile=None, artifactPolicyDigest=None)
        self.calls = []

    def read(self, name, digest):
        self.calls.append((name, digest))
        return copy.deepcopy(self.files[name])

    def observe(self, **overrides):
        options = dict(local_dir=self.local, repository=REPO, list_digest=sha('1'))
        options.update(overrides)
        return pd.observe(copy.deepcopy(self.policy), self.read, **options)

    def refused(self, **overrides):
        with self.assertRaises(pd.Refusal) as error:
            self.observe(**overrides)
        for private in ('SYNTHETIC', 'synthetic', str(self.temp), sha('2'), BLOB, REPO):
            self.assertNotIn(private, str(error.exception))

    def proof(self, **changes):
        return {**dict(proofVersion='literal-proof/v1', publicCommit=COMMIT, listDigest=sha('1'),
                       inputDigest=sha('2'), localeDigest=sha('5'), complete=True, reason=None,
                       occurrences=[dict(self.occurrence)]), **changes}

    def resolve(self, proof=None, findings=None):
        calls = []

        def prove(digest):
            calls.append(digest)
            return proof or self.proof()
        findings = findings or [dict(field='file', lines=[1], inputDigest=sha('2'))]
        return pd.resolve(self.observe(), findings, prove, locale_digest=sha('5')), calls

    def test_valid_evidence_yields_exact_bindings_entries_and_authority_facts(self):
        observed = self.observe()
        self.assertEqual(self.calls, [('rule', sha('a')), ('publication', sha('b')), ('dispositions', sha('c'))])
        authority = observed['authority']
        self.assertEqual(observed['bindings'], dict(
            ruleId='synthetic-rule', ruleContext=self.context, repository=REPO, scope=pd.SCOPE,
            fields=('file', 'message'), policyVersion=pd.POLICY_VERSION,
            policyDigest=pd.digest(pd.DESCRIPTOR), authorityWorkId=self.work, approvalSeq=1,
            publicCommit=COMMIT, listDigest=sha('1'), ruleDigest=sha('a'), publicationDigest=sha('b'),
            dispositionsDigest=sha('c'), authorityWorkDigest=authority['authorityWorkDigest']))
        self.assertEqual(list(observed['entries']), [('file', sha('2'))])
        self.assertEqual(set(authority), set(pd.OBSERVED))
        self.assertEqual((authority['authorityEventCount'], authority['authorityCheckpoint']),
                         (1, records.snapshot(self.local, REPO, self.work)))
        self.assertLessEqual(authority['observedStart'], authority['observedEnd'])
        self.assertEqual(observed['artifact'], (None, None))
        self.policy.update(artifactPolicyFile='policy', artifactPolicyDigest=sha('d'))
        self.assertEqual(self.observe()['artifact'], ('policy', sha('d')))  # pinned here, read by the facade

    def test_schemas_are_closed_bounded_and_cross_bound(self):
        base = copy.deepcopy((self.files, self.policy))
        entry = lambda: self.files['dispositions']['entries'][0]
        occurrence = lambda: entry()['occurrences'][0]
        publication = lambda **change: self.files['publication'].update(change)
        dispositions = lambda **change: self.files['dispositions'].update(change)
        valid = dict(field='file', occurrences=[dict(self.occurrence, scope=pd.SCOPE)])
        rows = [('policy key', lambda: self.policy.update(extra=1)),
                ('policy missing', lambda: self.policy.pop('ruleFile')),
                ('policy kind', lambda: self.policy.update(policyVersion='artifact-provenance/v1')),
                ('policy bool version', lambda: self.policy.update(schemaVersion=True)),
                ('policy version 2', lambda: self.policy.update(schemaVersion=2)),
                ('policy pin', lambda: self.policy.update(ruleDigest='sha256:' + 'A' * 64)),
                ('policy stream', lambda: self.policy.update(authorityWorkId='other')),
                ('half artifact pair', lambda: self.policy.update(artifactPolicyFile='policy')),
                ('publication repository', lambda: publication(repository='example.invalid/other/repo')),
                ('publication commit', lambda: (publication(publicCommit='A' * 40), dispositions(publicCommit='A' * 40))),
                ('publication method', lambda: publication(method='label-only')),
                ('publication result', lambda: publication(result='pending')),
                ('publication verifier', lambda: publication(verifier=' ')),
                ('publication source', lambda: publication(sourceRef='')),
                ('publication rule', lambda: publication(ruleId='other-rule')),
                ('publication context', lambda: publication(ruleContext=sha('9'))),
                ('publication key', lambda: publication(extra=1)),
                ('dispositions method', lambda: dispositions(method='prior-publication')),
                ('dispositions snapshot', lambda: dispositions(publicCommit='c' * 40)),
                ('dispositions list', lambda: dispositions(listDigest=sha('9'))),
                ('dispositions context', lambda: dispositions(ruleContext=sha('9'))),
                ('dispositions verifier', lambda: dispositions(verifier='')),
                ('entry count', lambda: dispositions(entries=[dict(valid, inputDigest='sha256:' + format(i, '064x'))
                                                              for i in range(pd.MAX_ENTRIES + 1)])),
                ('duplicate entry', lambda: dispositions(entries=[dict(valid, inputDigest=sha('2'))] * 2)),
                ('occurrence count', lambda: entry().update(occurrences=[
                    dict(self.occurrence, byteOffset=i, scope=pd.SCOPE) for i in range(pd.MAX_OCCURRENCES + 1)])),
                ('entry field', lambda: entry().update(field='url')),
                ('entry digest', lambda: entry().update(inputDigest='sha256:abc')),
                ('entry key', lambda: entry().update(extra=1)),
                ('no occurrences', lambda: entry().update(occurrences=[])),
                ('scope class', lambda: occurrence().update(scope='public')),
                ('bool row', lambda: occurrence().update(patternLine=True)),
                ('zero row', lambda: occurrence().update(patternLine=0)),
                ('negative offset', lambda: occurrence().update(byteOffset=-1)),
                ('empty length', lambda: occurrence().update(byteLength=0)),
                ('literal digest', lambda: occurrence().update(literalDigest='sha256:' + 'g' * 64)),
                ('witness oid', lambda: occurrence().update(publicBlobOid='b' * 39)),
                ('witness digest', lambda: occurrence().update(publicContentDigest=None)),
                ('occurrence key', lambda: occurrence().update(extra=1))]
        self.observe()  # positive control: each row below changes exactly one thing
        for name, mutate in rows:
            with self.subTest(name):
                self.files, self.policy = copy.deepcopy(base)
                mutate()
                self.refused()

    def test_rule_scope_fields_and_source_are_validated(self):
        source = lambda **change: dict(dict(sourceRef='synthetic:owner-message', date='2026-11-04', quote=QUOTE), **change)
        rows = [('scope', dict(scope='public-text')), ('empty fields', dict(fields=[])),
                ('unsorted fields', dict(fields=['message', 'file'])), ('duplicate fields', dict(fields=['file', 'file'])),
                ('unknown field', dict(fields=['file', 'secret'])), ('text fields', dict(fields='file')),
                ('policy version', dict(policyVersion='public-text/v2')), ('policy digest', dict(policyDigest=sha('9'))),
                ('rule key', dict(extra=1)), ('blank locator', dict(source=source(sourceRef=' '))),
                ('source key', dict(source=source(extra=1)))]
        self.observe()  # positive control
        for name, rule in rows:
            with self.subTest(name):
                self.build(rule=rule)
                self.refused()
        other = 'example.invalid/other/repo'
        self.build(rule=dict(repository=other), repo=other)  # a valid stream, but for another repository
        self.refused()

    def test_authority_is_an_exact_current_unrevoked_owner_decision(self):
        other = sha('9')
        rows = [('manager decision', dict(event=dict(owner='manager'))),
                ('other words', dict(event=dict(answer='SYNTHETIC ONLY: other words.'))),
                ('other date', dict(event=dict(date='2026-11-03'))),
                ('other context', dict(event=dict(context=other))),
                ('other rule', dict(event=dict(rule='other-rule'))),
                ('generic question', dict(event=dict(question='approve everything'))),
                ('opaque inventory authority', dict(event=dict(question='privacy-artifact/v1 approve synthetic-rule ' + other))),
                ('no decision', dict(event=False)),
                ('sequence past the stream', dict(rule=dict(approvalSeq=9))),
                ('sequence names another event', dict(before=[UNRELATED], rule=dict(approvalSeq=1)))]
        self.build(before=[UNRELATED])
        self.observe()  # positive control: an unrelated earlier decision only shifts the sequence
        for name, options in rows:
            with self.subTest(name):
                self.build(**options)
                self.refused()
        later = [('terminal revocation', ('revoke', None, {})), ('revocation of another context', ('revoke', other, {})),
                 ('approval of another context', ('approve', other, {})),
                 ('malformed namespace question', ('approve', None, dict(question='privacy-public-text/v1 maybe x'))),
                 ('nonowner namespace decision', ('approve', other, dict(rule='other-rule', owner='manager'))),
                 ('unresolved namespace decision', ('approve', other, dict(
                     rule='other-rule', answer={'unknown': 'pending'}, date={'unknown': 'pending'})))]
        for name, (action, context, options) in later:
            with self.subTest(name):
                self.build()
                self.decision(self.work, action, context, **options)
                self.refused()
        self.build()
        self.decision(self.work, 'approve', other, rule='other-rule')  # another rule's owner decision
        self.decision(self.work, question='privacy-artifact/v1 revoke some-receipt ' + other)  # opaque authority
        self.decision(self.work, **UNRELATED)
        self.assertEqual(self.observe()['authority']['authorityEventCount'], 4)
        self.refused(local_dir=self.temp / 'absent')  # no stream, no authority

    def test_unrelated_appends_change_authority_facts_but_not_bindings(self):
        start = self.observe()
        self.decision(self.work, **UNRELATED)  # the documented window: only approval data and revocation matter
        end = self.observe()
        self.assertEqual(start['bindings'], end['bindings'])
        self.assertNotEqual(start['authority']['authorityCheckpoint'], end['authority']['authorityCheckpoint'])
        self.assertEqual((start['authority']['authorityEventCount'], end['authority']['authorityEventCount']), (1, 2))

    def test_join_resolves_only_complete_exact_attested_in_scope_occurrences(self):
        self.assertEqual(self.resolve(), ([dict(inputDigest=sha('2'), resolved=True, reason=None, occurrences=1)],
                                          [sha('2')]))
        one = lambda **change: [dict(self.occurrence, **change)]
        rows = [('incomplete', dict(complete=False, reason='missing-witness'), 'proof'),
                ('incomplete flag', dict(complete=False), 'proof'),
                ('refused', dict(reason='unsupported-grammar'), 'proof'),
                ('version', dict(proofVersion='literal-proof/v2'), 'proof'),
                ('other snapshot', dict(publicCommit='c' * 40), 'proof'),
                ('other list', dict(listDigest=sha('9')), 'proof'),
                ('other input', dict(inputDigest=sha('9')), 'proof'),
                ('other locale', dict(localeDigest=sha('9')), 'proof'),
                ('empty', dict(occurrences=[]), 'proof'),
                ('no witness', dict(occurrences=one(publicBlobOid=None, publicContentDigest=None)), 'proof'),
                ('other row', dict(occurrences=one(patternLine=2)), 'proof'),
                ('malformed', dict(occurrences=[{}]), 'proof'),
                ('uncovered repeat', dict(occurrences=one() + one(byteOffset=20)), 'mismatch'),
                ('moved', dict(occurrences=one(byteOffset=5)), 'mismatch'),
                ('resized', dict(occurrences=one(byteLength=6)), 'mismatch'),
                ('other literal', dict(occurrences=one(literalDigest=sha('9'))), 'mismatch'),
                ('other witness blob', dict(occurrences=one(publicBlobOid='c' * 40)), 'mismatch'),
                ('other witness bytes', dict(occurrences=one(publicContentDigest=sha('9'))), 'mismatch')]
        for name, change, reason in rows:
            with self.subTest(name):
                [verdict], calls = self.resolve(self.proof(**change))
                self.assertEqual((verdict['resolved'], verdict['reason'], verdict['occurrences']), (False, reason, 0))
                self.assertEqual(calls, [sha('2')])
        entry = self.files['dispositions']['entries'][0]
        for scope in pd.CLASSES[1:]:  # verified exclusions: the same bytes stay unresolved
            with self.subTest(scope):
                entry['occurrences'][0]['scope'] = scope
                self.assertEqual(self.resolve()[0][0]['reason'], 'excluded')
        repeat = self.proof(occurrences=one() + one(byteOffset=20))
        for second, expected in ((pd.SCOPE, (True, None, 2)), ('credential', (False, 'excluded', 0))):
            with self.subTest(second):  # every occurrence of an input needs scope, never only the first
                entry['occurrences'] = [dict(self.occurrence, scope=pd.SCOPE), dict(self.occurrence, byteOffset=20, scope=second)]
                [verdict], _ = self.resolve(repeat)
                self.assertEqual((verdict['resolved'], verdict['reason'], verdict['occurrences']), expected)
        for attested in (entry['occurrences'], [dict(self.occurrence, scope=pd.SCOPE)] * 2):
            entry['occurrences'] = attested  # an attested occurrence the proof lacks; a duplicated one is no repeat
            self.assertEqual(self.resolve()[0][0]['reason'], 'mismatch')

    def test_unscoped_or_unattested_findings_never_request_a_proof(self):
        hit = dict(field='file', lines=[1], inputDigest=sha('2'))
        rows = [('field outside the rule', dict(hit, field='path'), 'out-of-scope'),
                ('other input', dict(hit, inputDigest=sha('9')), 'unattested'),
                ('stale field', dict(hit, field='message'), 'unattested')]
        for name, finding, reason in rows:
            with self.subTest(name):
                verdicts, calls = self.resolve(findings=[finding])
                self.assertEqual(([v['resolved'] for v in verdicts], [v['reason'] for v in verdicts], calls),
                                 ([False], [reason], []))
        verdicts, calls = self.resolve(findings=[rows[0][1], hit, rows[1][1]])
        self.assertEqual(([v['reason'] for v in verdicts], calls), (['out-of-scope', None, 'unattested'], [sha('2')]))

    def test_module_imports_no_facade_and_performs_no_io(self):
        tree = ast.parse((ROOT / 't3compose/privacy_dispositions.py').read_text())
        names = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                 for alias in node.names}
        self.assertEqual(names & {'privacy', 'cli', 'process', 'identifier_list', 'subprocess', 'os'}, set())
        spec = importlib.util.spec_from_file_location('t3compose.privacy_dispositions_probe',
                                                      ROOT / 't3compose/privacy_dispositions.py')
        probe = importlib.util.module_from_spec(spec)
        with patch.object(process, 'run', side_effect=AssertionError('process')), \
                patch('builtins.open', side_effect=AssertionError('read')), \
                patch('os.open', side_effect=AssertionError('read')):
            spec.loader.exec_module(probe)
        self.assertFalse(hasattr(probe, 'register'))
