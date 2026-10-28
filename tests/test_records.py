import copy
import hashlib
import importlib
from pathlib import Path
from unittest.mock import patch

from t3compose.jsonio import canonical_json
from tests.support import OfflineTestCase

SHA_A, SHA_B, SHA_C = "1" * 40, "2" * 40, "3" * 40
DIGEST_A, DIGEST_B, DIGEST_C = "sha256:" + ("a" * 64), "sha256:" + ("b" * 64), "sha256:" + ("c" * 64)
REPO_ID, SYNTH_REPO = "github.com/example-org/example-repo", "synthetic/router-pilot"
MANAGER_WRITER, LEAD_WRITER = "manager@" + REPO_ID, "lead:WORK-1@" + REPO_ID


def _routing_task():
    return {
        "intent": "Implement pure schema validation", "acceptance": ["All 21 event kinds validate"],
        "interfaces": ["t3compose.schema"], "scope": ["t3compose/schema.py", "tests/test_records.py", "docs/records.md"],
        "riskFacts": ["shared-interface"], "dependencies": ["HS-1", "DOC-1"],
    }


def _routing_input_data(context=None):
    task = _routing_task()
    digest = "sha256:" + hashlib.sha256(canonical_json({"task": task, "context": context})).hexdigest()
    return {"task": task, "context": context, "inputDigest": digest, "policyDigest": DIGEST_A}


def _valid_work():
    return {
        "workId": "WORK-1", "linearIssue": "EX-101", "repository": REPO_ID, "targetBranch": "main", "reviewBase": SHA_A,
        "scope": ["t3compose/schema.py", "tests/test_records.py"],
        "designRefs": ["docs/superpowers/specs/2026-10-27-t3-compose-design.md#sources-of-truth-and-local-records"],
        "ownedPaths": ["t3compose/schema.py", "tests/test_records.py", "docs/records.md"], "dependencies": ["HS-1"],
        "acceptance": [{"id": "AC-1", "text": "Each event kind validates"}, {"id": "AC-2", "text": "Unknown keys fail"}],
        "requiredChecks": [
            {"id": "CHK-FAST", "command": ["python3", "-m", "unittest"], "kind": "fast"},
            {"id": "CHK-HEAVY", "command": "make verify", "kind": "heavy"},
        ],
        "writer": MANAGER_WRITER,
    }


def _valid_event_payloads():
    return {
        "routing-input": _routing_input_data(),
        "routing-classification": {
            "inputRef": 1, "inputDigest": DIGEST_A, "policyDigest": DIGEST_B, "ruleVersion": "v2",
            "criteria": {
                "domain": {"SOFTWARE_ENGINEERING": "Code and docs", "OTHER": "Non-eng", "MIXED_OR_UNCLEAR": "Unclear"},
                "difficulty": {"EASY": "Mechanical", "MEDIUM": "Bounded", "CHALLENGING": "Hard", "INSUFFICIENT_CONTEXT": "Missing"},
            },
            "criteriaDigest": DIGEST_C, "riskFacts": ["shared-interface"],
            "configuredModel": "~typesafe/jev-latest", "snapshotModel": "typesafe/jev-1.14-20261014", "responseId": "dec-synth-1",
            "answers": {
                "domain": {"type": "choice", "choice": "SOFTWARE_ENGINEERING", "confidence": 0.85, "probabilities": {"SOFTWARE_ENGINEERING": 0.9, "OTHER": 0.06, "MIXED_OR_UNCLEAR": 0.04}},
                "difficulty": {"type": "choice", "choice": "MEDIUM", "confidence": 0.78, "probabilities": {"EASY": 0.1, "MEDIUM": 0.8, "CHALLENGING": 0.05, "INSUFFICIENT_CONTEXT": 0.05}},
            },
            "outcome": "accepted", "reason": "Confidence above threshold",
        },
        "routing-judgment": {"inputRef": 1, "inputDigest": DIGEST_A, "policyDigest": DIGEST_B, "domain": "SOFTWARE_ENGINEERING", "difficulty": "MEDIUM", "reason": "Bounded schema validator", "basis": "manual"},
        "routing-selection": {
            "inputRef": 1, "inputDigest": DIGEST_A, "policyDigest": DIGEST_B, "classificationRef": 2,
            "role": "worker", "round": "first", "reviewScope": None, "tier": "medium", "floorsApplied": ["shared-interface"], "escalation": False,
            "selection": {"providerPool": "pool-synth", "model": "model-argon-synth", "options": {"reasoningEffort": "high", "fastMode": False}, "roles": ["worker"]},
            "chosenPoolAccount": "acct-synth-1", "skippedPoolAccounts": [{"providerInstanceId": "acct-synth-0", "reason": "model not offered"}],
            "availabilityReason": "Offering account available", "fallbackReason": None,
            "configDigest": DIGEST_A, "catalogReadAt": "2026-10-28T08:00:00Z", "catalogDigest": DIGEST_B, "quotaStateDigest": DIGEST_C,
        },
        "dispatch-intent": {
            "role": "worker", "round": 1, "clientRequestId": "t3c-WORK-1-worker-r1", "briefPath": ".local/state/briefs/WORK-1-r1.md", "briefDigest": DIGEST_A,
            "selection": {"providerInstanceId": "prov-synth-1", "model": "model-sonnet-synth", "options": {"thinking": True, "syntheticEmpty": "", "syntheticSpace": "   "}, "roles": ["worker", "reviewer"]},
            "provisionedCommit": SHA_A, "startCommit": SHA_A, "reviewBase": SHA_A, "dispatchingCheckout": "/tmp/synthetic-checkout", "isolation": "worktree",
        },
        "dispatch-confirmed": {"clientRequestId": "t3c-WORK-1-worker-r1", "taskId": "task:synthetic%3Aworker%3A1", "childThread": "thread-synth-101", "worktreePath": "/tmp/synthetic-worktrees/wt-101"},
        "result": {
            "taskId": "task:synthetic%3Aworker%3A1", "t3Status": {"state": "completed", "summary": "Worker finished cleanly"},
            "worktreePath": "/tmp/synthetic-worktrees/wt-101", "resultCommit": SHA_B, "parentCommit": SHA_A,
            "branchObserved": "t3/synthetic-record-task", "workerClaims": {"testsRan": 47, "allPassed": True, "checks": {"unknown": "not reported"}},
            "verifiedFacts": {"headMatches": True, "cleanTree": True}, "questions": [],
        },
        "check": {"checkId": "CHK-FAST", "command": ["python3", "-I", "-S", "-B", "-m", "unittest"], "commit": SHA_B, "outcome": "pass", "source": "feature-lead", "evidencePath": ".local/state/logs/chk-fast.log"},
        "criterion-evidence": {"criterionId": "AC-1", "commit": SHA_B, "source": "feature-lead", "evidence": {"test": "tests.test_records.OfflineRecordSchema"}},
        "lead-assignment": {"thread": "thread-lead-synth-1", "assignedBy": MANAGER_WRITER, "date": "2026-10-28", "reason": "Multi-part records store implementation"},
        "review": {
            "reviewer": {"kind": "task", "taskId": "task:synthetic%3Areviewer%3A2"}, "base": SHA_A, "head": SHA_B, "verdict": "changes-required", "evidence": None,
            "findings": [{"id": "F-1", "severity": "important", "summary": "Reject bool seq", "location": "t3compose/schema.py:42"}],
        },
        "finding-disposition": {"reviewRef": 11, "findingId": "F-1", "response": "Added explicit bool check", "disposition": "fixed", "by": "worker:task:synthetic%3Aworker%3A1", "evidence": "tests/test_records.py", "commit": SHA_C, "reason": None},
        "op-intent": {"opId": "op-push-1", "operation": "push", "target": "refs/heads/t3/synthetic-record-task", "commit": SHA_C, "expectedPriorHead": "absent"},
        "op-result": {
            "intentRef": 13, "opId": "op-push-1", "operation": "push", "target": "refs/heads/t3/synthetic-record-task", "commit": SHA_C,
            "outcome": "succeeded", "confirmation": "git ls-remote confirmed exact SHA on branch", "statusSummary": {"childStarted": True, "childStatus": 0, "revocation": "ok"},
        },
        "decision": {"question": "Split RC-1 into three sequential PRs?", "answer": "Three PRs in sequence within 1,200 lines each", "decidedBy": "owner", "date": "2026-10-28"},
        "scope": {
            "decisionRef": 15, "scope": ["t3compose/schema.py", "tests/test_records.py", "docs/records.md"],
            "acceptance": [{"id": "AC-1", "text": "Updated acceptance criterion"}],
            "requiredChecks": [{"id": "CHK-FAST", "command": ["python3", "-m", "unittest"], "kind": "fast"}],
        },
        "next-action": {"action": "Proceed to RC-1b after RC-1a merges"},
        "reconciliation": {"item": {"kind": "dispatch", "ref": 5}, "t3Facts": {"status": "completed", "taskId": "task:synthetic%3Aworker%3A1"}, "gitFacts": {"head": SHA_B, "dirty": False}, "outcome": "completed", "notes": "Verified clean completion"},
        "restart": {"phase": "before", "checkpoint": {"lastSeq": 18, "digest": DIGEST_A}},
        "handoff": {
            "pointers": {"skill": "t3-compose-manager", "repository": REPO_ID, "recordsPath": ".local/state/records", "routingPolicyPath": ".local/agent-routing.json", "takeoverDumpPath": ".local/state/takeover.json"},
            "holdEndCondition": "All Tasks through PH-1 merged and L1-L7 passed", "pilotCriteria": ["Single writer verified", "In-flight restart verified"],
            "nextAction": "Reconcile takeover state before first pilot dispatch", "checkpoint": {"lastSeq": 19, "digest": DIGEST_B},
        },
        "writer-transfer": {"from": MANAGER_WRITER, "to": LEAD_WRITER, "reconciliationRef": 18, "checkpoint": {"lastSeq": 20, "digest": DIGEST_C}},
    }


OPTIONAL_FIXTURE_KEYS = {
    "review": {"evidence"}, "finding-disposition": {"commit", "reason"},
    "op-result": {"statusSummary"}, "scope": {"scope", "acceptance", "requiredChecks"},
    "reconciliation": {"notes"},
}


class OfflineRecordSchema(OfflineTestCase):
    def test_import_purity_and_constants(self):
        from t3compose import cli, process, schema
        before = cli.command_names()
        with patch("builtins.open", side_effect=AssertionError("application file read")), \
                patch.object(Path, "open", side_effect=AssertionError("application path read")), \
                patch.object(process, "run", side_effect=AssertionError("application process")):
            importlib.reload(schema)
        self.assertEqual(cli.command_names(), before)
        self.assertEqual((schema.SCHEMA_VERSION, schema.MAX_ROUTING_INPUT_BYTES, len(schema.EVENT_KINDS)), (1, 16384, 21))
        self.assertEqual(set(schema.EVENT_KINDS), set(_valid_event_payloads()))
        self.assertEqual(set(schema.EVENT_SCHEMAS), set(_valid_event_payloads()))
        self.assertTrue(issubclass(schema.SchemaError, ValueError))

    def test_work_valid_and_field_validation(self):
        from t3compose.schema import SchemaError, validate_work
        work = _valid_work()
        snapshot = copy.deepcopy(work)
        self.assertIsNone(validate_work(work))
        self.assertEqual(work, snapshot)
        self.assertIsNone(validate_work({
            **work, "linearIssue": None, "repository": SYNTH_REPO, "scope": "Single scope description",
            "designRefs": [], "dependencies": [], "writer": "lead:WORK-1@" + SYNTH_REPO,
        }))

        for key in tuple(work):
            missing = copy.deepcopy(work)
            del missing[key]
            with self.subTest(missing=key), self.assertRaises(SchemaError) as ctx:
                validate_work(missing)
            self.assertIn(key, ctx.exception.path)

        with self.assertRaises(SchemaError) as ctx:
            validate_work({**copy.deepcopy(work), "unexpectedField": "secret-value-not-echoed"})
        self.assertNotIn("secret-value-not-echoed", str(ctx.exception))

        bad_cases = [
            ("workId", "../bad"), ("workId", ""), ("linearIssue", ""), ("linearIssue", 123),
            ("repository", "not-a-valid-repo"), ("repository", "github.com/owner"),
            ("repository", "synthetic/a/b"), ("repository", "host/owner/name"),
            ("targetBranch", "bad branch"), ("targetBranch", "refs/../main"), ("targetBranch", "/main"),
            ("workId", "WORK-1\n"), ("repository", "synthetic/name\n"), ("reviewBase", SHA_A + "\n"),
            ("targetBranch", "synthetic:branch"), ("targetBranch", "synthetic@{1}"), ("targetBranch", "-synthetic"),
            ("targetBranch", "synthetic/.hidden"), ("targetBranch", "synthetic/a.lock/child"), ("targetBranch", "synthetic\\branch"), ("targetBranch", "synthetic."),
            ("reviewBase", "1234"), ("reviewBase", "G" * 40), ("reviewBase", "A" * 40),
            ("scope", []), ("scope", [""]), ("designRefs", [""]), ("ownedPaths", []),
            ("ownedPaths", ["../outside.py"]), ("ownedPaths", ["/abs/path.py"]),
            ("dependencies", [""]), ("acceptance", []),
            ("acceptance", [{"id": "AC-1", "text": "one"}, {"id": "AC-1", "text": "dup"}]),
            ("acceptance", [{"id": "AC-1", "text": "one", "extra": 1}]),
            ("requiredChecks", []), ("requiredChecks", [{"id": "C1", "command": [], "kind": "fast"}]),
            ("requiredChecks", [{"id": "C1", "command": "ok", "kind": "medium"}]),
            ("requiredChecks", [{"id": "C1", "command": "ok", "kind": "fast"}, {"id": "C1", "command": "ok2", "kind": "heavy"}]),
            ("writer", "manager@other.org/o/r"), ("writer", "lead:OTHER-WORK@" + REPO_ID), ("writer", "worker@" + REPO_ID),
        ]
        for field, bad_val in bad_cases:
            with self.subTest(field=field, bad_val=bad_val), self.assertRaises(SchemaError):
                validate_work({**copy.deepcopy(work), field: bad_val})

    def test_event_envelope_validation(self):
        from t3compose.schema import SchemaError, validate_event
        payloads = _valid_event_payloads()
        base_event = {
            "schemaVersion": 1, "seq": 25, "kind": "next-action", "at": "2026-10-28T16:50:00Z",
            "writer": MANAGER_WRITER, "helperCommit": SHA_A, "data": payloads["next-action"],
        }
        snap = copy.deepcopy(base_event)
        self.assertIsNone(validate_event(base_event))
        self.assertEqual(base_event, snap)
        self.assertIsNone(validate_event({**base_event, "at": "2026-10-28T16:50:00.123456Z", "seq": 10 ** 4300 - 1}))

        for key in tuple(base_event):
            missing = copy.deepcopy(base_event)
            del missing[key]
            with self.subTest(missing=key), self.assertRaises(SchemaError):
                validate_event(missing)

        bad_envelopes = [
            ("at", "2026-10-28T00:00:00Z\n"), ("at", "２０２６-１０-２８T00:00:00Z"), ("seq", 10 ** 4300),
            ("schemaVersion", 2), ("schemaVersion", True), ("seq", 0), ("seq", -1), ("seq", True), ("seq", "1"),
            ("kind", "unknown-kind"), ("at", "2026-10-28 16:50:00Z"), ("at", "2026-10-28T16:50:00+00:00"),
            ("at", "2026-02-30T10:00:00Z"), ("at", "2026-10-28T24:00:00Z"), ("at", "2026-10-28T16:50:00.1234567Z"),
            ("writer", "invalid-writer"), ("helperCommit", "abc"), ("data", []),
        ]
        for field, bad_val in bad_envelopes:
            with self.subTest(field=field, bad_val=bad_val), self.assertRaises(SchemaError):
                validate_event({**copy.deepcopy(base_event), field: bad_val})

        restart_after = {
            "schemaVersion": 1, "seq": 20, "kind": "restart", "at": "2026-10-28T16:50:00Z", "writer": MANAGER_WRITER,
            "helperCommit": SHA_A, "data": {"phase": "after", "checkpoint": {"lastSeq": 18, "digest": DIGEST_A}, "beforeRef": 19},
        }
        self.assertIsNone(validate_event(restart_after))
        with self.assertRaises(SchemaError):
            validate_event({**restart_after, "seq": 19})
        bad_ckpt = copy.deepcopy(restart_after)
        bad_ckpt["data"]["checkpoint"]["lastSeq"] = 20
        with self.assertRaises(SchemaError):
            validate_event(bad_ckpt)

    def test_all_21_event_kinds_required_and_unknown_fields(self):
        from t3compose.schema import SchemaError, validate_event, validate_event_data
        payloads = _valid_event_payloads()
        self.assertEqual(len(payloads), 21)

        for kind, valid_data in payloads.items():
            original = copy.deepcopy(valid_data)
            with self.subTest(kind=kind, check="valid"):
                self.assertIsNone(validate_event_data(kind, valid_data))
                self.assertEqual(valid_data, original)
                self.assertIsNone(validate_event({
                    "schemaVersion": 1, "seq": 30, "kind": kind, "at": "2026-10-28T12:00:00Z",
                    "writer": MANAGER_WRITER, "helperCommit": SHA_A, "data": valid_data,
                }))
            with self.subTest(kind=kind, check="unknown-key"):
                with self.assertRaises(SchemaError) as ctx:
                    validate_event_data(kind, {**copy.deepcopy(valid_data), "SYNTHETIC_PRIVATE_KEY": "sensitive-token-xyz"})
                for marker in ("sensitive-token-xyz", "SYNTHETIC_PRIVATE_KEY"):
                    self.assertNotIn(marker, str(ctx.exception) + ctx.exception.path + ctx.exception.rule)
            for req_key in valid_data.keys() - OPTIONAL_FIXTURE_KEYS.get(kind, set()):
                with self.subTest(kind=kind, missing_key=req_key):
                    missing = copy.deepcopy(valid_data)
                    del missing[req_key]
                    with self.assertRaises(SchemaError) as ctx:
                        validate_event_data(kind, missing)
                    self.assertIn(req_key, ctx.exception.path)
                with self.subTest(kind=kind, wrong_type_key=req_key):
                    with self.assertRaises(SchemaError):
                        validate_event_data(kind, {**copy.deepcopy(valid_data), req_key: object()})

    def test_routing_input_cap_digest_and_context(self):
        from t3compose.schema import MAX_ROUTING_INPUT_BYTES, SchemaError, validate_event_data, validate_routing_input
        task = _routing_task()
        raw = {"task": task, "context": None}
        digest = validate_routing_input(raw)
        self.assertEqual(digest, "sha256:" + hashlib.sha256(canonical_json(raw)).hexdigest())

        with_ctx = {"task": task, "context": {"reason": "low-confidence", "details": "Clarify", "priorClassificationRef": 2}}
        ctx_digest = validate_routing_input(with_ctx)
        event_data = {"task": task, "context": with_ctx["context"], "inputDigest": ctx_digest, "policyDigest": DIGEST_A}
        self.assertIsNone(validate_event_data("routing-input", event_data))
        with self.assertRaises(SchemaError):
            validate_event_data("routing-input", {**event_data, "inputDigest": DIGEST_B})
        with self.assertRaises(SchemaError):
            validate_routing_input({"task": {**task, "boilerplate": "forbidden"}, "context": None})

        base_bytes = len(canonical_json({"task": {**task, "intent": ""}, "context": None}))
        exact_intent = "x" * (MAX_ROUTING_INPUT_BYTES - base_bytes)
        exact_obj = {"task": {**task, "intent": exact_intent}, "context": None}
        self.assertEqual(len(canonical_json(exact_obj)), MAX_ROUTING_INPUT_BYTES)
        self.assertTrue(validate_routing_input(exact_obj).startswith("sha256:"))
        over_obj = {"task": {**task, "intent": exact_intent + "y"}, "context": None}
        self.assertEqual(len(canonical_json(over_obj)), MAX_ROUTING_INPUT_BYTES + 1)
        with self.assertRaises(SchemaError):
            validate_routing_input(over_obj)

    def test_routing_classification_judgment_and_selection_rules(self):
        from t3compose.schema import SchemaError, validate_event_data
        payloads = _valid_event_payloads()
        failed_cls = {
            **copy.deepcopy(payloads["routing-classification"]), "outcome": "manager-judgment",
            "snapshotModel": {"unknown": "HTTP 503"}, "responseId": {"unknown": "HTTP 503"}, "answers": {"unknown": "HTTP 503"},
        }
        self.assertIsNone(validate_event_data("routing-classification", failed_cls))
        for field in ("snapshotModel", "responseId", "answers"):
            with self.subTest(field=field), self.assertRaises(SchemaError):
                validate_event_data("routing-classification", {**copy.deepcopy(payloads["routing-classification"]), field: {"unknown": "missing"}})
        for field, choice in (("domain", "MIXED_OR_UNCLEAR"), ("difficulty", "INSUFFICIENT_CONTEXT")):
            bad = copy.deepcopy(payloads["routing-classification"])
            bad["answers"][field]["choice"] = choice
            with self.assertRaises(SchemaError):
                validate_event_data("routing-classification", bad)

        for number in (10 ** 400, float("nan"), float("inf")):
            bad_number = copy.deepcopy(payloads["routing-classification"])
            bad_number["answers"]["domain"]["confidence"] = number
            with self.subTest(number=number), self.assertRaises(SchemaError):
                validate_event_data("routing-classification", bad_number)
        bad_sum = copy.deepcopy(payloads["routing-classification"])
        bad_sum["answers"]["domain"]["probabilities"]["SOFTWARE_ENGINEERING"] = 0.5
        with self.assertRaises(SchemaError):
            validate_event_data("routing-classification", bad_sum)
        bad_bool_prob = copy.deepcopy(payloads["routing-classification"])
        bad_bool_prob["answers"]["domain"]["confidence"] = True
        with self.assertRaises(SchemaError):
            validate_event_data("routing-classification", bad_bool_prob)

        rev_sel = {
            **copy.deepcopy(payloads["routing-selection"]), "role": "reviewer", "reviewScope": "code", "tier": "medium",
            "selection": {"providerInstanceId": "prov-sol-synth", "model": "sol-high", "options": {}},
            "chosenPoolAccount": None, "skippedPoolAccounts": [],
        }
        self.assertIsNone(validate_event_data("routing-selection", rev_sel))
        for mutation in ({"reviewScope": None}, {"tier": "easy"}, {"chosenPoolAccount": "acct-1"}):
            with self.subTest(mutation=mutation), self.assertRaises(SchemaError):
                validate_event_data("routing-selection", {**copy.deepcopy(rev_sel), **mutation})
        th_sel = {
            **copy.deepcopy(payloads["routing-selection"]), "role": "text-helper", "classificationRef": None,
            "selection": {"providerPool": "pool-synth", "model": "helper-flash", "options": {}},
        }
        self.assertIsNone(validate_event_data("routing-selection", th_sel))
        with self.assertRaises(SchemaError):
            validate_event_data("routing-selection", {**copy.deepcopy(payloads["routing-selection"]), "classificationRef": None})

    def test_review_lead_assignment_and_disposition_cd4_rules(self):
        from t3compose.schema import SchemaError, validate_event_data
        payloads = _valid_event_payloads()
        lead_rev = {
            **copy.deepcopy(payloads["review"]), "reviewer": {"kind": "lead", "thread": "thread-lead-synth-1"},
            "verdict": "approve", "findings": [], "evidence": {"path": ".local/state/reviews/WORK-1-final.md", "digest": DIGEST_A},
        }
        self.assertIsNone(validate_event_data("review", lead_rev))
        with self.assertRaises(SchemaError):
            validate_event_data("review", {**copy.deepcopy(lead_rev), "evidence": None})
        with self.assertRaises(SchemaError):
            validate_event_data("review", {**copy.deepcopy(lead_rev), "reviewer": {"kind": "lead", "thread": "thread-lead-synth-1", "taskId": "task-fabricated"}})
        self.assertIsNone(validate_event_data("review", {**copy.deepcopy(payloads["review"]), "evidence": {"path": ".local/state/reviews/WORK-1-r1.md", "digest": DIGEST_B}}))

        dup_finding = copy.deepcopy(payloads["review"])
        dup_finding["findings"].append({"id": "F-1", "severity": "minor", "summary": "Duplicate ID"})
        with self.assertRaises(SchemaError):
            validate_event_data("review", dup_finding)
        for bad_date in ("2026-10-28\n", "２０２６-１０-２８"):
            with self.subTest(date=bad_date), self.assertRaises(SchemaError):
                validate_event_data("lead-assignment", {**payloads["lead-assignment"], "date": bad_date})
        with self.assertRaises(SchemaError):
            validate_event_data("lead-assignment", {**payloads["lead-assignment"], "assignedBy": LEAD_WRITER})
        for bad_ref in ("task:synthetic%3Areviewer%3A2", 0, -1, True):
            with self.subTest(bad_ref=bad_ref), self.assertRaises(SchemaError):
                validate_event_data("finding-disposition", {**payloads["finding-disposition"], "reviewRef": bad_ref})
        with self.assertRaises(SchemaError):
            validate_event_data("finding-disposition", {**payloads["finding-disposition"], "commit": None})
        declined_ok = {**copy.deepcopy(payloads["finding-disposition"]), "disposition": "declined", "commit": None, "reason": "Stylistic preference"}
        self.assertIsNone(validate_event_data("finding-disposition", declined_ok))
        with self.assertRaises(SchemaError):
            validate_event_data("finding-disposition", {**declined_ok, "reason": None})

    def test_unknown_handling_and_conditional_event_variants(self):
        from t3compose.schema import SchemaError, validate_event_data
        payloads = _valid_event_payloads()
        self.assertIsNone(validate_event_data("dispatch-confirmed", {**payloads["dispatch-confirmed"], "worktreePath": {"unknown": "Not yet returned"}}))
        self.assertIsNone(validate_event_data("result", {**payloads["result"], "branchObserved": {"unknown": "Detached HEAD"}}))
        self.assertIsNone(validate_event_data("decision", {
            **payloads["decision"], "answer": {"unknown": "Awaiting owner"},
            "decidedBy": {"unknown": "Awaiting owner"}, "date": {"unknown": "Awaiting owner"},
        }))
        with self.assertRaises(SchemaError):
            validate_event_data("decision", {**payloads["decision"], "decidedBy": {"unknown": "Missing"}})
        self.assertIsNone(validate_event_data("next-action", {"action": {"unknown": "Blocked on owner"}}))
        self.assertIsNone(validate_event_data("reconciliation", {
            **payloads["reconciliation"], "t3Facts": {"unknown": "Timed out"},
            "gitFacts": {"unknown": "Worktree missing"}, "outcome": "unknown-liveness",
        }))
        self.assertIsNone(validate_event_data("handoff", {**payloads["handoff"], "nextAction": {"unknown": "Pending pilot"}}))

        for bad_unknown in ({"unknown": ""}, {"unknown": "   "}, {"unknown": "ok", "extra": 1}, {}):
            with self.subTest(bad_unknown=bad_unknown), self.assertRaises(SchemaError):
                validate_event_data("next-action", {"action": bad_unknown})

        for kind, bad_payload in (
            ("dispatch-intent", {**payloads["dispatch-intent"], "briefDigest": DIGEST_A + "\n"}),
            ("result", {**payloads["result"], "t3Status": {"state": "stopped", "summary": "synthetic"}}),
            ("check", {**payloads["check"], "commit": {"unknown": "Not allowed"}}),
            ("scope", {"decisionRef": 15}),
            ("restart", {"phase": "before", "checkpoint": {"lastSeq": 5, "digest": DIGEST_A}, "beforeRef": 4}),
            ("restart", {"phase": "after", "checkpoint": {"lastSeq": 5, "digest": DIGEST_A}}),
            ("restart", {"phase": "after", "checkpoint": {"lastSeq": 5, "digest": DIGEST_A}, "beforeRef": 5}),
            ("writer-transfer", {**payloads["writer-transfer"], "to": MANAGER_WRITER}),
            ("writer-transfer", {**payloads["writer-transfer"], "to": "lead:WORK-1@github.com/other/repo"}),
            ("writer-transfer", {**payloads["writer-transfer"], "reconciliationRef": 25}),
            ("op-intent", {**payloads["op-intent"], "expectedPriorHead": None}),
        ):
            with self.subTest(kind=kind), self.assertRaises(SchemaError):
                validate_event_data(kind, bad_payload)

        self.assertIsNone(validate_event_data("result", {**payloads["result"], "t3Status": {"state": "interrupted", "summary": "synthetic"}}))
        op_res_refused = {
            **copy.deepcopy(payloads["op-result"]), "outcome": "refused",
            "statusSummary": {"childStarted": False, "childStatus": None, "refusal": "extraheader"},
        }
        self.assertIsNone(validate_event_data("op-result", op_res_refused))
        with self.assertRaises(SchemaError):
            validate_event_data("op-result", {**op_res_refused, "statusSummary": {"childStarted": False, "childStatus": 0}})

    def test_open_values_and_mapping_diagnostics(self):
        from t3compose.schema import SchemaError, validate_event_data
        payloads, secret = _valid_event_payloads(), "SYNTHETIC_PRIVATE_KEY"
        for bad in (10 ** 4300, float("nan"), {"unknown": ""}, {"unknown": 1}, "\ud800"):
            with self.subTest(value_type=type(bad).__name__), self.assertRaises(SchemaError) as caught:
                validate_event_data("result", {**payloads["result"], "workerClaims": {secret: bad}})
            self.assertNotIn(secret, str(caught.exception) + caught.exception.path + caught.exception.rule)
        for invalid in (None, 1, [], {}):
            selection = {**payloads["dispatch-intent"]["selection"], "options": {secret: invalid}}
            with self.subTest(value_type=type(invalid).__name__), self.assertRaises(SchemaError) as caught:
                validate_event_data("dispatch-intent", {**payloads["dispatch-intent"], "selection": selection})
            self.assertNotIn(secret, str(caught.exception) + caught.exception.path + caught.exception.rule)
        self.assertIsNone(validate_event_data("result", {**payloads["result"], "workerClaims": {"n": 10 ** 4300 - 1}}))
        selection = {**payloads["dispatch-intent"]["selection"], secret: True}
        with self.assertRaises(SchemaError) as caught:
            validate_event_data("dispatch-intent", {**payloads["dispatch-intent"], "selection": selection})
        self.assertNotIn(secret, str(caught.exception) + caught.exception.path + caught.exception.rule)
