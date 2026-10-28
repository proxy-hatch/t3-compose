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


class OfflineRecordStore(OfflineTestCase):
    def setUp(self):
        import importlib.util
        self.assertIsNotNone(importlib.util.find_spec("t3compose.records"), "durable store missing")
        from t3compose import records, cli
        self.r = records
        self.enterContext(patch.object(cli, "helper_info", return_value={"commit": SHA_C}))
        self.local = self.temp / "synthetic-local"
        self.work = _valid_work()
        self.payloads = _valid_event_payloads()
        self.r.create_work(self.local, REPO_ID, "WORK-1", self.work, writer=MANAGER_WRITER)
        self.directory = self.local / "state/records" / REPO_ID / "work/WORK-1"

    def add(self, kind, data=None, **kwargs):
        import subprocess
        from t3compose import process
        from tests.support import FakeRunner
        fake = FakeRunner({"git": lambda argv, **kw: subprocess.CompletedProcess(argv, 0, SHA_A + "\n", "")})
        with patch.object(process, "run", fake):
            return self.r.append_event(self.local, REPO_ID, "WORK-1", kind,
                                       self.payloads[kind] if data is None else data,
                                       writer=kwargs.pop("writer", MANAGER_WRITER), **kwargs)

    def load(self):
        return self.r.load_work(self.local, REPO_ID, "WORK-1")

    def command(self, *args):
        from t3compose import process
        from tests.support import guarded_helper_command
        return process.run(guarded_helper_command("--local-dir", str(self.local), *args),
                           capture_output=True, text=True, timeout=15)

    def test_identity_immutable_work_and_unsafe_paths(self):
        import os
        for origin in (REPO_ID, "https://" + REPO_ID + ".git", "git@github.com:example-org/example-repo.git",
                       "ssh://git@github.com/example-org/example-repo.git"):
            self.assertEqual(self.r.normalize_repository(origin), REPO_ID)
        self.assertEqual(self.r.normalize_repository(SYNTH_REPO), SYNTH_REPO)
        before = (self.directory / "work.json").read_bytes()
        for repo, work_id, writer in ((REPO_ID, "../escape", MANAGER_WRITER),
                                     (REPO_ID, "WORK-1", LEAD_WRITER),
                                     (SYNTH_REPO, "WORK-1", MANAGER_WRITER),
                                     (REPO_ID, "WORK-1", MANAGER_WRITER)):
            with self.subTest(repo=repo, work=work_id), self.assertRaises(ValueError):
                self.r.create_work(self.local, repo, work_id, self.work, writer=writer)
        for repo in ("../escape", "host/a/b", "https://host.test/a/../escape", "https://host.test/a/b?secret=x"):
            with self.subTest(repo=repo), self.assertRaises(ValueError):
                self.r.normalize_repository(repo)
        self.assertEqual((self.directory / "work.json").read_bytes(), before)
        for relative in ("state", "state/records", "state/records/" + REPO_ID + "/work/WORK-1/events"):
            path = self.local / relative
            saved = path.with_name(path.name + "-saved")
            path.rename(saved)
            path.symlink_to(saved, target_is_directory=True)
            with self.assertRaises(ValueError):
                self.load()
            path.unlink()
            saved.rename(path)
        event = self.add("next-action")
        file = self.directory / "events/1.json"
        file.unlink()
        file.symlink_to(self.directory / "work.json")
        with self.assertRaises(ValueError):
            self.load()
        file.unlink()
        file.write_bytes(canonical_json(event))
        os.chmod(file, 0o600)
        self.assertEqual(self.load()["events"], [event])
        with self.assertRaises(ValueError):
            self.add("next-action", writer=LEAD_WRITER)

    def test_failed_ancestor_sync_is_not_skipped_on_retry(self):
        import os
        local, parent = self.temp / "synthetic-retry-local", self.temp.stat()
        original, attempts = os.fsync, []
        def sync(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
                attempts.append(fd)
                raise OSError("synthetic parent sync failure")
            return original(fd)
        with patch.object(os, "fsync", sync):
            for count in (1, 2):
                with self.assertRaises(ValueError):
                    self.r.create_work(local, REPO_ID, "WORK-1", self.work, writer=MANAGER_WRITER)
                self.assertEqual(len(attempts), count)
        self.assertEqual(self.r.create_work(local, REPO_ID, "WORK-1", self.work, writer=MANAGER_WRITER)["work"], self.work)

    def test_canonical_git_suffix_identities_round_trip(self):
        import json
        for repo in ("https://example.invalid/synthetic-owner/synthetic-repo.git.git", "synthetic/name.git"):
            identity = self.r.normalize_repository(repo)
            Path(identity).mkdir(parents=True)
            self.assertEqual(self.r.normalize_repository(identity), identity)
            writer = "manager@" + identity
            work = {**self.work, "repository": identity, "writer": writer}
            self.r.create_work(self.local, repo, "WORK-1", work, writer=writer)
            self.assertEqual(self.r.load_work(self.local, identity, "WORK-1")["work"], work)
            self.assertEqual(self.r.list_work(self.local, repo), {"works": [work], "damaged": []})
            for action, extra in (("show", ("--work", "WORK-1")), ("list", ())):
                result = self.command("work", action, "--repo", identity, *extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(work, [json.loads(result.stdout)["work"]] if action == "show" else json.loads(result.stdout)["works"])

    def test_corruption_isolation_full_history_and_partial_creation(self):
        import json
        event = self.add("next-action")
        file = self.directory / "events/1.json"
        for mutation in ({**event, "seq": 2}, {**event, "writer": LEAD_WRITER},
                         {**event, "data": {}}, {**event, "helperCommit": "short"}):
            file.write_bytes(canonical_json(mutation))
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.load()
            with self.assertRaises(ValueError):
                self.add("decision")
        file.write_bytes(canonical_json(event))
        for name in ("2.json", "01.json", "unexpected.json"):
            file.rename(file.with_name(name))
            with self.assertRaises(ValueError):
                self.load()
            file.with_name(name).rename(file)
        good = {**self.work, "workId": "WORK-2"}
        self.r.create_work(self.local, REPO_ID, "WORK-2", good, writer=MANAGER_WRITER)
        file.write_text("{broken")
        listed = self.r.list_work(self.local, REPO_ID)
        self.assertEqual([w["workId"] for w in listed["works"]], ["WORK-2"])
        self.assertEqual(listed["damaged"][0]["workId"], "WORK-1")
        result = self.command("work", "list", "--repo", REPO_ID)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout), listed)
        partial = {**self.work, "workId": "WORK-3"}
        with patch.object(self.r.jsonio, "atomic_create", side_effect=OSError("synthetic")), self.assertRaises(ValueError):
            self.r.create_work(self.local, REPO_ID, "WORK-3", partial, writer=MANAGER_WRITER)
        with self.assertRaises(ValueError):
            self.r.load_work(self.local, REPO_ID, "WORK-3")
        with self.assertRaises(ValueError):
            self.r.create_work(self.local, REPO_ID, "WORK-3", partial, writer=MANAGER_WRITER)
        self.assertEqual(self.r.list_work(self.local, SYNTH_REPO), {"works": [], "damaged": []})

    def test_duplicate_dispatch_rounds_and_completed_native_provenance(self):
        intent = self.add("dispatch-intent")
        self.assertEqual(self.add("dispatch-intent"), intent)
        for mutation in ({"briefDigest": DIGEST_B}, {"startCommit": SHA_B}, {"clientRequestId": "synthetic-second", "round": 2}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.add("dispatch-intent", {**self.payloads["dispatch-intent"], **mutation})
        for kind in ("result", "dispatch-confirmed"):
            with self.assertRaises(ValueError):
                self.add(kind, {**self.payloads[kind], "taskId": "synthetic-wrong", "clientRequestId": "synthetic-missing"})
        self.add("dispatch-confirmed")
        second = {**self.payloads["dispatch-intent"], "clientRequestId": "synthetic-second", "round": 2, "startCommit": SHA_B}
        with self.assertRaises(ValueError):
            self.add("dispatch-intent", second)
        for mutation in ({"taskId": "synthetic-wrong"}, {"worktreePath": "/tmp/synthetic-wrong"},
                         *({"t3Status": {"state": state, "summary": "synthetic"}} for state in ("failed", "cancelled", "interrupted"))):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.add("result", {**self.payloads["result"], **mutation})
        self.add("result")
        for mutation in ({"startCommit": SHA_A}, {"reviewBase": SHA_B}, {"round": 3}, {"provisionedCommit": SHA_B}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.add("dispatch-intent", {**second, **mutation})
        self.add("dispatch-intent", second)
        with self.assertRaises(ValueError):
            self.add("result")
        with self.assertRaises(ValueError):
            self.add("dispatch-confirmed", {**self.payloads["dispatch-confirmed"], "clientRequestId": "synthetic-second"})
        self.assertEqual(len(self.load()["events"]), 4)

    def test_reconciliation_only_proven_preservation_clears(self):
        self.add("dispatch-intent")
        reconciliation = {
            "item": {"kind": "dispatch", "ref": 1}, "outcome": "stopped-and-preserved",
            "t3Facts": {"clientRequestId": self.payloads["dispatch-intent"]["clientRequestId"],
                        "state": "stopped", "childrenReconciled": True, "evidence": "synthetic T3 status"},
            "gitFacts": {"commitsPreserved": True, "uncommittedPreserved": True,
                         "evidence": "synthetic preservation record", "worktreePath": "/tmp/synthetic-preserved"},
        }
        second = {**self.payloads["dispatch-intent"], "clientRequestId": "synthetic-second", "round": 2}
        for outcome in ("unknown-liveness", "in-flight", "not-started", "completed"):
            self.add("reconciliation", {**reconciliation, "outcome": outcome, "t3Facts": {"unknown": "synthetic unknown"}})
            with self.assertRaises(ValueError):
                self.add("dispatch-intent", second)
        for group, key, value in (("t3Facts", "childrenReconciled", False), ("t3Facts", "state", "running"),
                                  ("gitFacts", "commitsPreserved", False), ("gitFacts", "uncommittedPreserved", False),
                                  ("t3Facts", "clientRequestId", "synthetic-wrong"), ("gitFacts", "evidence", "")):
            bad = copy.deepcopy(reconciliation)
            bad[group][key] = value
            with self.subTest(group=group, key=key), self.assertRaises(ValueError):
                self.add("reconciliation", bad)
        self.add("reconciliation", reconciliation)
        self.add("dispatch-intent", second)
        confirmed = {**self.payloads["dispatch-confirmed"], "clientRequestId": "synthetic-second"}
        self.add("dispatch-confirmed", confirmed)
        reconciliation["item"]["ref"] = "synthetic-second"
        reconciliation["t3Facts"]["clientRequestId"] = "synthetic-second"
        with self.assertRaises(ValueError):
            self.add("reconciliation", reconciliation)
        reconciliation["t3Facts"]["taskId"] = confirmed["taskId"]
        reconciliation["gitFacts"]["worktreePath"] = confirmed["worktreePath"]
        self.add("reconciliation", reconciliation)
        self.add("dispatch-intent", {**second, "clientRequestId": "synthetic-third", "round": 3})

    def test_invalid_transition_histories_refused_by_store_entrypoint_and_load(self):
        import os
        for kind in ("review", "finding-disposition", "lead-assignment", "restart", "handoff", "writer-transfer"):
            with self.subTest(kind=kind):
                if kind == "lead-assignment":
                    self.payloads[kind]["assignedBy"] = "manager@synthetic/wrong"
                with self.assertRaises(ValueError):
                    self.add(kind)
                data = self.temp / "synthetic-data.json"
                data.write_bytes(canonical_json(self.payloads[kind]))
                result = self.command("event", "add", "--repo", REPO_ID, "--work", "WORK-1", "--kind", kind,
                                      "--file", str(data), "--writer", MANAGER_WRITER)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                payload = copy.deepcopy(self.payloads[kind])
                if "checkpoint" in payload:
                    payload["checkpoint"]["lastSeq"] = 0
                if kind == "writer-transfer":
                    payload["checkpoint"]["lastSeq"] = 1
                event = {"schemaVersion": 1, "seq": 1, "kind": kind, "data": payload,
                         "at": "2026-10-28T00:00:00Z", "writer": MANAGER_WRITER, "helperCommit": SHA_C}
                file = self.directory / "events/1.json"
                file.write_bytes(canonical_json(event))
                os.chmod(file, 0o600)
                with self.assertRaises(ValueError):
                    self.load()
                file.unlink()

    def test_routing_input_recorder_and_reference_mismatches(self):
        refs = self.r.record_routing_input(self.local, REPO_ID, "WORK-1", task=_routing_task(), context=None,
                                           policy_digest=DIGEST_A, writer=MANAGER_WRITER)
        self.assertEqual(refs, {"inputRef": 1, "inputDigest": _routing_input_data()["inputDigest"], "policyDigest": DIGEST_A})
        for kind in ("routing-classification", "routing-judgment"):
            data = {**self.payloads[kind], **refs}
            if kind == "routing-classification":
                data["criteriaDigest"] = "sha256:" + hashlib.sha256(canonical_json(data["criteria"])).hexdigest()
            for key, value in (("inputRef", 99), ("inputDigest", DIGEST_B), ("policyDigest", DIGEST_B)):
                with self.subTest(kind=kind, key=key), self.assertRaises(ValueError):
                    self.add(kind, {**data, key: value})
            self.add(kind, data)
        selection = {**self.payloads["routing-selection"], **refs}
        self.add("routing-selection", selection)
        with self.assertRaises(ValueError):
            self.add("routing-selection", {**selection, "classificationRef": 1})
        for task, context in (({**_routing_task(), "extra": True}, None),
                              ({**_routing_task(), "intent": "x" * 16385}, None),
                              (_routing_task(), {"priorClassificationRef": 99})):
            with self.subTest(context=context), self.assertRaises(ValueError):
                self.r.record_routing_input(self.local, REPO_ID, "WORK-1", task=task, context=context,
                                             policy_digest=DIGEST_A, writer=MANAGER_WRITER)
        self.assertEqual(len(self.load()["events"]), 4)

    def git_fixture(self):
        from t3compose import process
        checkout = self.temp / "synthetic-checkout"
        checkout.mkdir()
        def git(*args):
            return process.run(["git", "-C", str(checkout), *args], check=True, capture_output=True, text=True).stdout.strip()
        git("init", "-b", "synthetic-main")
        git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-m", "synthetic base")
        base = git("rev-parse", "HEAD")
        git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid", "commit", "--allow-empty", "-m", "synthetic head")
        head = git("rev-parse", "HEAD")
        git("remote", "add", "origin", "git@github.com:example-org/example-repo.git")
        return checkout, base, head

    def test_checkout_head_and_start_object_with_real_git(self):
        checkout, base, head = self.git_fixture()
        self.assertEqual(self.r.normalize_repository(str(checkout)), REPO_ID)
        reviewer = {**self.payloads["dispatch-intent"], "role": "reviewer", "provisionedCommit": head,
                    "startCommit": base, "dispatchingCheckout": str(checkout)}
        for mutation, override in (({"provisionedCommit": base}, checkout), ({"startCommit": SHA_C}, checkout), ({}, self.temp)):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.r.append_event(self.local, REPO_ID, "WORK-1", "dispatch-intent", {**reviewer, **mutation},
                                    writer=MANAGER_WRITER, checkout=override)
        self.r.append_event(self.local, REPO_ID, "WORK-1", "dispatch-intent", reviewer,
                            writer=MANAGER_WRITER, checkout=checkout)
        self.assertEqual(self.load()["events"][0]["data"]["startCommit"], base)

    def test_all_supported_kinds_through_store_and_actual_commands(self):
        import json
        checkout, base, head = self.git_fixture()
        payloads = self.payloads
        refs = {"inputRef": 1, "inputDigest": payloads["routing-input"]["inputDigest"], "policyDigest": DIGEST_A}
        for kind in ("routing-classification", "routing-judgment", "routing-selection"):
            payloads[kind].update(refs)
        payloads["routing-classification"]["criteriaDigest"] = "sha256:" + hashlib.sha256(canonical_json(payloads["routing-classification"]["criteria"])).hexdigest()
        payloads["dispatch-intent"].update(dispatchingCheckout=str(checkout), provisionedCommit=head)
        payloads["op-result"]["intentRef"] = 10
        payloads["scope"]["decisionRef"] = 12
        kinds = [k for k in payloads if k not in {"review", "finding-disposition", "lead-assignment", "restart", "handoff", "writer-transfer"}]
        for via_cli in (False, True):
            work_id = "WORK-cli" if via_cli else "WORK-api"
            work = {**self.work, "workId": work_id}
            data_file = self.temp / "synthetic-input.json"
            data_file.write_bytes(canonical_json(work))
            args = ("--repo", REPO_ID, "--work", work_id)
            if via_cli:
                result = self.command("work", "new", *args, "--file", str(data_file), "--writer", MANAGER_WRITER)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["work"], work)
                self.assertEqual(self.command("work", "new", *args, "--file", str(data_file), "--writer", MANAGER_WRITER).returncode, 2)
            else:
                self.r.create_work(self.local, REPO_ID, work_id, work, writer=MANAGER_WRITER)
            for seq, kind in enumerate(kinds, 1):
                for valid in (False, True):
                    data = payloads[kind] if valid else {**payloads[kind], "syntheticSecret": "do-not-echo"}
                    if via_cli:
                        data_file.write_bytes(canonical_json(data))
                        result = self.command("event", "add", *args, "--file", str(data_file), "--kind", kind, "--writer", MANAGER_WRITER)
                        self.assertEqual(result.returncode, 0 if valid else 2, (kind, result.stderr))
                        self.assertNotIn("do-not-echo", result.stderr)
                        if valid:
                            self.assertEqual(json.loads(result.stdout)["seq"], seq)
                    elif valid:
                        self.r.append_event(self.local, REPO_ID, work_id, kind, data, writer=MANAGER_WRITER)
                    else:
                        with self.assertRaises(ValueError):
                            self.r.append_event(self.local, REPO_ID, work_id, kind, data, writer=MANAGER_WRITER)
                history_bad = {
                    "routing-classification": {"inputRef": 99}, "routing-judgment": {"policyDigest": DIGEST_B},
                    "routing-selection": {"classificationRef": 1}, "dispatch-intent": {"reviewBase": SHA_B},
                    "dispatch-confirmed": {"clientRequestId": "synthetic-missing"}, "result": {"taskId": "synthetic-missing"},
                    "check": {"checkId": "synthetic-missing"}, "criterion-evidence": {"criterionId": "synthetic-missing"},
                    "op-intent": {}, "op-result": {"target": "synthetic-wrong"}, "scope": {"decisionRef": 1},
                    "reconciliation": {"item": {"kind": "dispatch", "ref": 99}},
                }
                if kind in history_bad:
                    bad = {**payloads[kind], **history_bad[kind]}
                    if via_cli:
                        data_file.write_bytes(canonical_json(bad))
                        result = self.command("event", "add", *args, "--file", str(data_file), "--kind", kind, "--writer", MANAGER_WRITER)
                        self.assertEqual((result.returncode, result.stdout), (2, ""), kind)
                    else:
                        with self.assertRaises(ValueError):
                            self.r.append_event(self.local, REPO_ID, work_id, kind, bad, writer=MANAGER_WRITER)
            shown = self.command("work", "show", *args)
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(len(json.loads(shown.stdout)["events"]), 15)
        self.assertEqual(self.command("work", "list", "--repo", REPO_ID).returncode, 0)
        self.assertEqual(self.command("work", "show", "--repo", REPO_ID, "--work", "missing").returncode, 2)

    def test_two_real_processes_same_sequence_different_kinds(self):
        import os
        import subprocess
        import sys
        from tests.support import BOOTSTRAP_PATH
        script = self.temp / "synthetic-racer.py"
        script.write_text('''import os, sys
from t3compose import cli, records
from pathlib import Path
local, kind, ready, release = sys.argv[1:]
original = records.jsonio.atomic_create
def publish(path, value, **kwargs):
    os.write(int(ready), b"R")
    os.read(int(release), 1)
    return original(path, value, **kwargs)
records.jsonio.atomic_create = publish
cli.helper_info = lambda: {"commit": "3" * 40}
try:
    data = {"action": "synthetic next"} if kind == "next-action" else {"question": "synthetic?", "answer": "yes", "decidedBy": "synthetic owner", "date": "2026-10-28"}
    records.append_event(Path(local), "github.com/example-org/example-repo", "WORK-1", kind, data, writer="manager@github.com/example-org/example-repo")
except ValueError:
    sys.exit(2)
''')
        ready_r, ready_w = os.pipe()
        release_r, release_w = os.pipe()
        children = []
        try:
            for kind in ("next-action", "decision"):
                children.append(subprocess.Popen([sys.executable, "-I", "-S", "-B", str(BOOTSTRAP_PATH),
                    str(script), str(self.local), kind, str(ready_w), str(release_r)],
                    pass_fds=(ready_w, release_r), stdout=subprocess.PIPE, stderr=subprocess.PIPE))
            import select
            for _ in children:
                self.assertTrue(select.select([ready_r], [], [], 15)[0], "writer barrier timed out")
                self.assertEqual(os.read(ready_r, 1), b"R")
            os.write(release_w, b"GG")
            outputs = [child.communicate(timeout=15) for child in children]
            self.assertEqual(sorted(c.returncode for c in children), [0, 2], outputs)
            files = list((self.directory / "events").iterdir())
            self.assertEqual([p.name for p in files], ["1.json"])
            before = files[0].read_bytes()
            winner = self.load()["events"][0]
            self.assertEqual(winner["kind"], ("next-action", "decision")[[c.returncode for c in children].index(0)])
            self.assertEqual(files[0].read_bytes(), before)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()
            for fd in (ready_r, ready_w, release_r, release_w):
                os.close(fd)

    def test_import_and_register_have_no_state_or_process_effects(self):
        from t3compose import cli, process
        with patch("builtins.open", side_effect=AssertionError("read")), \
                patch.object(Path, "open", side_effect=AssertionError("read")), \
                patch.object(process, "run", side_effect=AssertionError("process")):
            importlib.reload(self.r)
            registry = cli.Registry(cli.Parser(prog="synthetic"))
            self.r.register(registry)
            self.assertEqual(set(registry.validate()), {("work", action) for action in ("new", "show", "list", "snapshot", "reconcile", "repair")} | {("event", "add")})

    def test_nonobject_corruption_and_unsafe_temporary_isolate_work(self):
        import os
        event = self.add("next-action")
        file = self.directory / "events/1.json"
        for invalid in ([], None, "synthetic-invalid"):
            file.write_bytes(canonical_json(invalid))
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.load()
                self.assertEqual(len(self.r.list_work(self.local, REPO_ID)["damaged"]), 1)
        file.write_bytes(canonical_json(event))
        temporary = file.with_name(".t3c-tmp-99999999-" + "a" * 32 + ".tmp")
        temporary.symlink_to(file)
        with self.assertRaises(ValueError):
            self.load()
        temporary.unlink()
        temporary.write_bytes(b"partial helper temporary")
        os.chmod(temporary, 0o600)
        self.assertEqual(self.load()["events"], [event])
        self.assertTrue(temporary.exists())
        original_stat = os.stat
        def disappear(name, *args, **kwargs):
            if name == temporary.name:
                temporary.unlink()
            return original_stat(name, *args, **kwargs)
        with patch.object(os, "stat", disappear):
            self.assertEqual(self.load()["events"], [event])

    def test_history_joins_and_fabricated_stored_dispatch_retry(self):
        import os
        for kind in ("op-result", "scope", "reconciliation", "dispatch-confirmed", "result"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.add(kind)
        for kind, key in (("check", "checkId"), ("criterion-evidence", "criterionId")):
            with self.assertRaises(ValueError):
                self.add(kind, {**self.payloads[kind], key: "synthetic-missing"})
        intent = self.add("op-intent")
        for key, value in (("opId", "synthetic-wrong"), ("target", "synthetic-wrong"), ("commit", SHA_A), ("intentRef", 99)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.add("op-result", {**self.payloads["op-result"], "intentRef": intent["seq"], key: value})
        decision = self.add("decision", {"question": "synthetic?", "answer": {"unknown": "pending"},
                                         "decidedBy": {"unknown": "pending"}, "date": {"unknown": "pending"}})
        with self.assertRaises(ValueError):
            self.add("scope", {**self.payloads["scope"], "decisionRef": decision["seq"]})
        self.add("scope", {"decisionRef": "synthetic-external-decision", "scope": "synthetic changed scope"})
        self.assertEqual(self.load()["effectiveWork"]["scope"], "synthetic changed scope")
        self.assertEqual(self.load()["work"], self.work)
        dispatch = self.add("dispatch-intent")
        with self.assertRaises(ValueError):
            self.add("dispatch-intent", {**self.payloads["dispatch-intent"], "role": "feature-lead", "clientRequestId": "synthetic-lead"})
        file = self.directory / "events/5.json"
        file.write_bytes(canonical_json({**dispatch, "seq": 5}))
        os.chmod(file, 0o600)
        with self.assertRaises(ValueError):
            self.load()

    def test_actual_command_duplicate_writer_retry_and_preservation(self):
        import json
        checkout, _, head = self.git_fixture()
        intent = {**self.payloads["dispatch-intent"], "dispatchingCheckout": str(checkout), "provisionedCommit": head}
        data_file = self.temp / "synthetic-dispatch.json"
        def add(kind, data, expected, extra=()):
            data_file.write_bytes(canonical_json(data))
            result = self.command("event", "add", "--repo", REPO_ID, "--work", "WORK-1", "--writer", MANAGER_WRITER,
                                  "--kind", kind, "--file", str(data_file), *extra)
            self.assertEqual(result.returncode, expected, result.stderr)
            return json.loads(result.stdout) if expected == 0 else None
        first = add("dispatch-intent", intent, 0)
        self.assertEqual(add("dispatch-intent", intent, 0), first)
        from t3compose import process
        for target in (checkout, self.temp / "synthetic-missing-checkout"):
            self.assertEqual(add("dispatch-intent", intent, 0, ("--checkout", str(target))), first)
        process.run(["git", "-C", str(checkout), "update-ref", "HEAD", head + "^"], check=True, capture_output=True)
        self.assertEqual(add("dispatch-intent", intent, 0, ("--checkout", str(checkout))), first)
        add("dispatch-intent", {**intent, "clientRequestId": "synthetic-stale", "round": 2}, 2)
        process.run(["git", "-C", str(checkout), "update-ref", "HEAD", head], check=True, capture_output=True)
        second = {**intent, "clientRequestId": "synthetic-second", "round": 2}
        add("dispatch-intent", second, 2)
        add("dispatch-confirmed", self.payloads["dispatch-confirmed"], 0)
        add("dispatch-intent", second, 2)
        preservation = {
            "item": {"kind": "dispatch", "ref": 1}, "outcome": "stopped-and-preserved",
            "t3Facts": {"clientRequestId": intent["clientRequestId"], "state": "stopped",
                        "childrenReconciled": True, "evidence": "synthetic T3 facts",
                        "taskId": self.payloads["dispatch-confirmed"]["taskId"]},
            "gitFacts": {"commitsPreserved": True, "uncommittedPreserved": True,
                         "evidence": "synthetic artifact archive", "worktreePath": self.payloads["result"]["worktreePath"]},
        }
        add("reconciliation", {**preservation, "t3Facts": {"unknown": "synthetic unknown"}}, 2)
        add("dispatch-intent", second, 2)
        add("reconciliation", preservation, 0)
        self.assertEqual(add("dispatch-intent", second, 0)["seq"], 4)
        add("dispatch-intent", {**second, "briefDigest": DIGEST_B}, 2)
        self.assertEqual(len(self.load()["events"]), 4)


class OfflineReviewHistory(OfflineTestCase):
    setUp = OfflineRecordStore.setUp
    add = OfflineRecordStore.add
    load = OfflineRecordStore.load
    command = OfflineRecordStore.command

    def reviewer(self, round=1):
        request, task = f"synthetic-review-{round}", f"synthetic-native-review-{round}"
        self.add("dispatch-intent", {**self.payloads["dispatch-intent"], "role": "reviewer",
                                    "clientRequestId": request, "startCommit": SHA_B, "round": round})
        self.add("dispatch-confirmed", {**self.payloads["dispatch-confirmed"], "clientRequestId": request,
                                       "taskId": task, "childThread": f"synthetic-review-thread-{round}"})
        self.add("result", {**self.payloads["result"], "taskId": task})
        return {**self.payloads["review"], "reviewer": {"kind": "task", "taskId": task}}

    def worker_result(self):
        for kind in ("dispatch-intent", "dispatch-confirmed", "result"):
            self.add(kind)

    def refuses_replay(self, event, mutation):
        import os
        path = self.directory / "events" / f'{event["seq"]}.json'
        original = path.read_bytes()
        path.write_bytes(canonical_json({**event, **mutation}))
        try:
            with self.assertRaises(ValueError):
                self.load()
            with self.assertRaises(ValueError):
                self.add("next-action")
        finally:
            path.write_bytes(original)
            os.chmod(path, 0o600)

    def test_native_reviewer_provenance_and_two_round_finding_keys(self):
        self.worker_result()
        for reviewer in ({"kind": "task", "taskId": "synthetic-invented"},
                         {"kind": "task", "taskId": self.payloads["result"]["taskId"]}):
            with self.assertRaises(ValueError):
                self.add("review", {**self.payloads["review"], "reviewer": reviewer})
        first_data = self.reviewer()
        for mutation in ({"base": SHA_C}, {"head": SHA_C}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.add("review", {**first_data, **mutation})
        first = self.add("review", first_data)
        second = self.add("review", self.reviewer(2))
        disposition = {**self.payloads["finding-disposition"], "reviewRef": first["seq"]}
        for mutation in ({"reviewRef": 1}, {"reviewRef": 999}, {"findingId": "missing"},
                         {"reviewRef": first_data["reviewer"]["taskId"]}, {"commit": None},
                         {"evidence": {"artifact": {"unknown": "missing"}}}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.add("finding-disposition", {**disposition, **mutation})
        missing = dict(disposition)
        del missing["reviewRef"]
        with self.assertRaises(ValueError):
            self.add("finding-disposition", missing)
        fixed = self.add("finding-disposition", disposition)
        self.assertEqual([e["data"]["reviewRef"] for e in self.load()["events"]
                          if e["kind"] == "finding-disposition"], [first["seq"]])
        self.refuses_replay(fixed, {"data": {**disposition, "reviewRef": 1}})
        self.refuses_replay(second, {"data": {**second["data"], "head": SHA_C}})
        self.add("finding-disposition", {**disposition, "reviewRef": second["seq"]})
        self.assertNotEqual(first["seq"], second["seq"])
        for seq, key, value in ((4, "role", "worker"), (5, "worktreePath", "synthetic-wrong"),
                                (6, "resultCommit", SHA_C), (4, "startCommit", SHA_C)):
            event = self.load()["events"][seq - 1]
            self.refuses_replay(event, {"data": {**event["data"], key: value}})

    def test_dispositions_authorship_severity_and_evidence(self):
        self.worker_result()
        data = self.reviewer()
        data["findings"].append({"id": "minor", "severity": "minor", "summary": "synthetic style"})
        review = self.add("review", data)
        disposition = {**self.payloads["finding-disposition"], "reviewRef": review["seq"]}
        for change in ({"disposition": "withdrawn", "by": "lead:synthetic-wrong"},
                       {"disposition": "declined", "reason": "synthetic reason"},
                       {"disposition": "resolved-outside-tree", "evidence": {}},
                       {"disposition": "owner-decided", "evidence": {"decisionRef": 1}},
                       {"disposition": "rejected-by-second-review", "evidence": {"reviewRef": review["seq"]}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.add("finding-disposition", {**disposition, **change})
        self.add("finding-disposition", {**disposition, "disposition": "withdrawn",
                                         "by": "task:" + data["reviewer"]["taskId"]})
        self.add("finding-disposition", {**disposition, "findingId": "minor", "disposition": "declined", "reason": "style"})
        for state in ("resolved-outside-tree", "disputed"):
            self.add("finding-disposition", {**disposition, "disposition": state})
        decision = self.add("decision")
        self.add("finding-disposition", {**disposition, "disposition": "owner-decided",
                                         "evidence": {"decisionRef": decision["seq"]}})
        second = self.add("review", self.reviewer(2))
        self.add("finding-disposition", {**disposition, "disposition": "rejected-by-second-review",
                                         "evidence": {"reviewRef": second["seq"]}})

    def test_lead_forms_currency_historical_reviews_and_source_labels(self):
        import json
        self.worker_result()
        assignment = self.add("lead-assignment")
        evidence = self.temp / "synthetic-review.md"
        evidence.write_text("Synthetic personal review")
        data = {**self.payloads["review"], "reviewer": {"kind": "lead", "thread": assignment["data"]["thread"]},
                "evidence": {"path": str(evidence), "digest": "sha256:" + hashlib.sha256(evidence.read_bytes()).hexdigest()}}
        for mutation in ({"reviewer": {"kind": "lead", "thread": "synthetic-wrong"}}, {"evidence": None},
                         {"reviewer": {**data["reviewer"], "taskId": "synthetic-fabricated"}},
                         {"reviewer": {"kind": "worker", "taskId": "synthetic-worker"}}, {"head": SHA_C}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.add("review", {**data, **mutation})
        review = self.add("review", data)
        self.refuses_replay(assignment, {"data": {**assignment["data"], "assignedBy": "manager@synthetic/wrong"}})
        self.refuses_replay(review, {"data": {**data, "reviewer": {"kind": "lead", "thread": "synthetic-wrong"}}})
        self.add("lead-assignment", {**assignment["data"], "thread": "synthetic-new-lead"})
        self.assertIn(review, self.load()["events"])
        with self.assertRaises(ValueError):
            self.add("review", data)
        for author in ("lead:synthetic-new-lead", "task:synthetic-wrong"):
            with self.assertRaises(ValueError):
                self.add("finding-disposition", {**self.payloads["finding-disposition"], "reviewRef": review["seq"],
                                                 "disposition": "withdrawn", "by": author})
        self.add("finding-disposition", {**self.payloads["finding-disposition"], "reviewRef": review["seq"],
                                         "disposition": "withdrawn", "by": "lead:" + data["reviewer"]["thread"]})
        for source in ("feature-lead", "worker"):
            check = self.add("check", {**self.payloads["check"], "source": source})
            self.assertEqual(check["data"]["source"], source)
        # Actual entrypoint accepts the new current lead, preserving the old review.
        data["reviewer"]["thread"] = "synthetic-new-lead"
        file = self.temp / "synthetic-data.json"
        file.write_bytes(canonical_json(data))
        args = ("event", "add", "--repo", REPO_ID, "--work", "WORK-1", "--writer", MANAGER_WRITER,
                "--kind", "review", "--file", str(file))
        result = self.command(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["data"], data)
        self.assertNotIn("verified", self.load())

    def test_actual_native_reviewer_history_and_missing_completion_refusal(self):
        import json
        checkout, _, head = OfflineRecordStore.git_fixture(self)
        self.worker_result()
        task = "synthetic-cli-reviewer"
        intent = {**self.payloads["dispatch-intent"], "role": "reviewer", "clientRequestId": "synthetic-cli-request",
                  "startCommit": SHA_B, "dispatchingCheckout": str(checkout), "provisionedCommit": head}
        confirmed = {**self.payloads["dispatch-confirmed"], "clientRequestId": intent["clientRequestId"],
                     "taskId": task, "childThread": "synthetic-cli-review-thread"}
        review = {**self.payloads["review"], "reviewer": {"kind": "task", "taskId": task}}
        file = self.temp / "synthetic-cli-payload.json"
        def append(kind, data, expected=0):
            file.write_bytes(canonical_json(data))
            result = self.command("event", "add", "--repo", REPO_ID, "--work", "WORK-1", "--kind", kind,
                                  "--writer", MANAGER_WRITER, "--file", str(file))
            self.assertEqual(result.returncode, expected, result.stderr)
            if expected == 0:
                return json.loads(result.stdout)
            self.assertEqual(result.stdout, "")
        append("dispatch-intent", intent)
        append("review", review, 2)
        append("dispatch-confirmed", confirmed)
        append("review", review, 2)
        append("result", {**self.payloads["result"], "taskId": task})
        event = append("review", review)
        self.assertEqual(event["data"]["reviewer"], {"kind": "task", "taskId": task})
        self.assertEqual(self.load()["events"][-1], event)
