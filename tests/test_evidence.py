"""Offline evidence comparisons through real, guarded RC-1 and evidence commands."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from t3compose import cli, process, records
from t3compose.jsonio import canonical_json
from tests.support import OfflineTestCase, ROOT, BOOTSTRAP_PATH

REPO = "github.com/example-org/evidence-fixture"
WRITER = "manager@" + REPO
CHECK = {"id": "SUITE", "command": ["synthetic-suite", "--offline"], "kind": "fast"}
CRITERION = {"id": "AC-1", "text": "Synthetic behavior has evidence"}


class OfflineEvidence(OfflineTestCase):
    def setUp(self):
        self.allow_program("git")
        self.local = self.temp / "synthetic-state"
        self.checkout = self.temp / "synthetic-checkout"
        self.checkout.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "remote.origin.url", "https://" + REPO + ".git")
        commits = []
        for number in range(3):
            self.git("commit", "--allow-empty", "-qm", f"Synthetic commit {number}")
            commits.append(self.git("rev-parse", "HEAD").strip())
        self.base, self.head, self.next_head = commits
        self.work_id, self.serial, self.round = "WORK-1", 0, 0
        self.current = self.base
        self.artifact = self.temp / "synthetic-review.md"
        self.artifact.write_text("Synthetic review evidence\n")
        self.descriptor = {"path": str(self.artifact), "digest": "sha256:" +
                           hashlib.sha256(self.artifact.read_bytes()).hexdigest()}
        self.new_work()

    def git(self, *args):
        return process.run(["git", "-C", str(self.checkout), *args], check=True,
                           capture_output=True, text=True).stdout

    def command(self, *args):
        return process.run([sys.executable, "-I", "-S", "-B", str(BOOTSTRAP_PATH),
                            str(ROOT / "bin/t3c"), "--local-dir", str(self.local), *args],
                           capture_output=True, text=True, timeout=20)

    def input_file(self, data):
        file = self.temp / "synthetic-input.json"
        file.write_bytes(canonical_json(data))
        return str(file)

    def new_work(self):
        work = {"workId": self.work_id, "linearIssue": None, "repository": REPO,
                "targetBranch": "main", "reviewBase": self.base, "scope": "Synthetic comparison",
                "designRefs": [], "ownedPaths": ["synthetic.txt"], "dependencies": [],
                "acceptance": [CRITERION], "requiredChecks": [CHECK], "writer": WRITER}
        result = self.command("work", "new", "--repo", str(self.checkout), "--work", self.work_id,
                              "--writer", WRITER, "--file", self.input_file(work))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["events"], [])

    def add(self, kind, data, expected=0):
        extra = ("--checkout", str(self.checkout)) if kind == "dispatch-intent" else ()
        result = self.command("event", "add", "--repo", REPO, "--work", self.work_id,
                              "--writer", WRITER, "--kind", kind,
                              "--file", self.input_file(data), *extra)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected == 0:
            self.assertEqual(result.stderr, "")
            return json.loads(result.stdout)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr)

    def native(self, role, head=None, complete=True):
        self.serial += 1
        task = f"synthetic-task-{self.serial}"
        request = f"synthetic-request-{self.serial}"
        start = self.current if role == "worker" else (head or self.head)
        if role == "worker":
            self.round += 1
        intent = self.add("dispatch-intent", {
            "role": role, "round": self.round if role == "worker" else self.serial,
            "clientRequestId": request, "briefPath": str(self.artifact),
            "briefDigest": self.descriptor["digest"],
            "selection": {"providerInstanceId": "synthetic-provider", "model": "synthetic-model", "options": {}},
            "provisionedCommit": self.next_head, "startCommit": start, "reviewBase": self.base,
            "dispatchingCheckout": str(self.checkout), "isolation": "worktree"})
        self.add("dispatch-confirmed", {"clientRequestId": request, "taskId": task,
                 "childThread": f"synthetic-thread-{self.serial}", "worktreePath": str(self.checkout)})
        result = {"taskId": task, "t3Status": {"state": "completed", "summary": "Synthetic native completion"},
                  "worktreePath": str(self.checkout), "resultCommit": head or self.head,
                  "parentCommit": start, "branchObserved": "synthetic/result",
                  "workerClaims": {"allPassed": True}, "verifiedFacts": {"accepted": True}, "questions": []}
        if complete:
            self.add("result", result)
            if role == "worker":
                self.current = result["resultCommit"]
        return task, result, intent

    def check_event(self, **changes):
        return self.add("check", {"checkId": "SUITE", "command": CHECK["command"],
            "commit": self.head, "outcome": "pass", "source": "manager",
            "evidencePath": str(self.artifact), **changes})

    def criterion(self, **changes):
        return self.add("criterion-evidence", {"criterionId": "AC-1", "commit": self.head,
            "source": "manager", "evidence": {"artifact": str(self.artifact)}, **changes})

    def assign(self, thread="synthetic-lead"):
        return self.add("lead-assignment", {"thread": thread, "assignedBy": WRITER,
                         "date": "2026-10-28", "reason": "Synthetic lead assignment"})

    def review(self, findings=(), lead=None, **changes):
        reviewer = {"kind": "lead", "thread": lead} if lead else {
            "kind": "task", "taskId": self.native("reviewer", changes.get("head"))[0]}
        return self.add("review", {"reviewer": reviewer, "base": self.base, "head": self.head,
            "verdict": "approve", "findings": list(findings), "evidence": self.descriptor, **changes})

    def finding(self, severity="important", id="F-1"):
        return {"id": id, "severity": severity, "summary": "Synthetic finding"}

    def dispose(self, review, disposition="fixed", expected=0, **changes):
        return self.add("finding-disposition", {"reviewRef": review["seq"], "findingId": "F-1",
            "response": "Synthetic response", "disposition": disposition, "by": "manager",
            "evidence": {"artifact": str(self.artifact)}, "commit": self.head, **changes}, expected)

    def ready(self, lead=None):
        self.native("worker")
        if lead:
            self.assign(lead)
        self.check_event(source="feature-lead" if lead else "manager")
        self.criterion(source="feature-lead" if lead else "manager")
        return self.review(lead=lead)

    def bytes(self):
        if not self.local.exists():
            return {}
        return {str(p.relative_to(self.local)): p.read_bytes() if p.is_file() else None
                for p in self.local.rglob("*")}

    def evidence(self, codes=(), *, head=None, pr_ready=False, repo=REPO, text=False):
        before = self.bytes()
        args = [*( ["--text"] if text else []), "evidence", "check", "--repo", repo,
                "--work", self.work_id, "--head", head or self.head]
        if pr_ready:
            args.append("--pr-ready")
        result = self.command(*args)
        self.assertEqual(result.returncode, 1 if codes else 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.bytes(), before, "evidence check mutated records/state")
        if text:
            data = {key: json.loads(value) for key, value in
                    (line.split(": ", 1) for line in result.stdout.splitlines())}
        else:
            data = json.loads(result.stdout)
        self.assertEqual(set(data), {"verified", "head", "prReady", "gaps"})
        self.assertEqual((data["verified"], data["head"], data["prReady"]),
                         (not codes, head or self.head, pr_ready))
        self.assertEqual([g["code"] for g in data["gaps"]], list(codes))
        for gap in data["gaps"]:
            self.assertEqual(set(gap), {"code", "id", "reviewRef", "eventRef"})
        return data

    def operation(self, operation, *, head=None, outcome="succeeded", summary="default", complete=True):
        self.serial += 1
        intent = self.add("op-intent", {"opId": f"synthetic-op-{self.serial}",
            "operation": operation, "target": "synthetic-target", "commit": head or self.head,
            "expectedPriorHead": "absent" if operation == "push" else None})
        data = {"intentRef": intent["seq"], **{key: intent["data"][key]
                for key in ("opId", "operation", "target", "commit")},
                "outcome": outcome, "confirmation": "Synthetic recorded confirmation"}
        if summary == "default":
            data["statusSummary"] = {"childStarted": True, "childStatus": 0, "revocation": "ok"}
        elif summary != "omit":
            data["statusSummary"] = summary
        if complete:
            self.add("op-result", data)
        return intent, data

    def test_fully_verified_api_real_entrypoint_default_text_read_only(self):
        self.ready()
        expected = self.evidence(repo=str(self.checkout))
        self.assertEqual(self.evidence(text=True), expected)
        from t3compose import evidence
        before = self.bytes()
        with patch.object(process, "run", side_effect=AssertionError("unexpected process")):
            self.assertEqual(evidence.check(self.local, REPO, self.work_id, self.head), expected)
        self.assertEqual(self.bytes(), before)
        # Descriptors survive an artifact move; acceptance never opens their paths.
        self.artifact.rename(self.temp / "synthetic-moved-review.md")
        self.evidence()

    def test_original_base_without_worker_result_never_invents_implementation(self):
        self.check_event(commit=self.base)
        self.criterion(commit=self.base)
        self.review(head=self.base)
        self.evidence(["head-missing"], head=self.base)
        self.native("worker", self.base)
        self.evidence(head=self.base)

    def test_new_commit_invalidates_old_verification_and_late_reviewer_cannot_rewind(self):
        self.ready()
        self.evidence()
        _, old_result, _ = self.native("reviewer", complete=False)
        self.native("worker", self.next_head)
        self.add("result", old_result)
        self.evidence(["check-stale", "review-stale", "criterion-stale"], head=self.next_head)
        self.evidence(["head-stale"])
        self.check_event(commit=self.next_head)
        self.criterion(commit=self.next_head)
        self.review(head=self.next_head)
        self.evidence(head=self.next_head)

    def test_missing_check_and_worker_claim_fields_do_not_grant_acceptance(self):
        self.native("worker")
        self.review()
        data = self.evidence(["check-missing", "criterion-missing"])
        self.assertEqual([(g["id"], g["eventRef"]) for g in data["gaps"]],
                         [("SUITE", None), ("AC-1", None)])
        self.add("check", {"checkId": "SUITE", "command": CHECK["command"],
            "commit": self.head, "outcome": "unknown", "source": "manager",
            "evidencePath": str(self.artifact)}, 2)
        self.evidence(["check-missing", "criterion-missing"])

    def test_applicable_checks_preserve_coverage_but_latest_failure_blocks(self):
        self.native("worker")
        self.review()
        self.criterion()
        unrelated = [({"commit": self.base}, "check-stale"),
                     ({"command": "synthetic-other"}, "check-command"),
                     ({"command": "synthetic-suite --offline"}, "check-command"),
                     ({"source": "worker"}, "check-source")]
        for change, code in unrelated:
            event = self.check_event(**change)
            data = self.evidence([code])
            self.assertEqual(data["gaps"][0]["eventRef"], event["seq"])
        self.check_event()
        for change, _ in unrelated:
            self.check_event(**change)
            self.evidence()
        for outcome in ("fail", "refused", "blocked"):
            event = self.check_event(outcome=outcome)
            data = self.evidence(["check-outcome"])
            self.assertEqual(data["gaps"][0]["eventRef"], event["seq"])
            self.check_event(source="worker")
            self.check_event(commit=self.base)
            self.evidence(["check-outcome"])
            self.check_event()
            self.evidence()

    def test_applicable_criteria_preserve_coverage_but_unknown_evidence_blocks(self):
        self.native("worker")
        self.review()
        self.check_event()
        unrelated = [({"commit": self.base}, "criterion-stale"), ({"source": "worker"}, "criterion-source")]
        for change, code in unrelated:
            self.criterion(**change)
            self.evidence([code])
        self.criterion()
        for change, _ in unrelated:
            self.criterion(**change)
            self.evidence()
        for value in ({"unknown": "Synthetic missing"}, None, []):
            self.criterion(evidence={"artifact": value})
            self.evidence(["criterion-evidence"])
            self.criterion(source="worker")
            self.criterion(commit=self.base)
            self.evidence(["criterion-evidence"])
            self.criterion()
        for source in ("manager", "reviewer", "assigned-session"):
            self.check_event(source=source)
            self.criterion(source=source)
            self.evidence()

    def test_effective_scope_new_ids_and_changed_definitions_require_fresh_evidence(self):
        self.ready()
        new_check = {**CHECK, "command": ["synthetic-suite", "--new"]}
        new_ac = {**CRITERION, "text": "Synthetic expanded acceptance"}
        self.add("scope", {"decisionRef": "synthetic-approved-change",
                          "requiredChecks": [new_check], "acceptance": [new_ac]})
        self.evidence(["check-missing", "criterion-missing"])
        self.check_event(command=new_check["command"])
        self.criterion()
        self.evidence()
        self.add("scope", {"decisionRef": "synthetic-unchanged", "requiredChecks": [new_check]})
        self.evidence()
        self.add("scope", {"decisionRef": "synthetic-replace", "acceptance": [{"id": "AC-2", "text": "New"}]})
        self.evidence(["criterion-missing"])
        self.add("scope", {"decisionRef": "synthetic-reintroduce", "acceptance": [new_ac]})
        self.evidence(["criterion-missing"])
        self.criterion()
        self.evidence()

    def test_latest_review_changes_required_or_stale_cannot_hide_behind_approval(self):
        self.ready()
        self.review(verdict="changes-required")
        self.evidence(["review-verdict"])
        self.review(head=self.base)
        self.evidence(["review-stale"])
        self.review(verdict="approve-after-fixes")
        self.evidence()

    def test_lead_review_and_check_accepted_task_substitution_refused(self):
        self.native("worker")
        self.assign()
        self.check_event(source="feature-lead")
        self.criterion(source="feature-lead")
        data = {"reviewer": {"kind": "lead", "thread": "synthetic-wrong"},
                "base": self.base, "head": self.head, "verdict": "approve", "findings": [],
                "evidence": self.descriptor}
        self.add("review", data, 2)
        self.evidence(["review-missing"])
        for changes in ({"reviewer": {"kind": "lead", "thread": "synthetic-lead"}, "evidence": None},
                        {"reviewer": {"kind": "lead", "thread": "synthetic-lead", "taskId": "synthetic-fabricated"}}):
            self.add("review", {**data, **changes}, 2)
            self.evidence(["review-missing"])
        self.review(lead="synthetic-lead")
        self.evidence()
        self.review()
        self.evidence(["review-source"])
        self.review(lead="synthetic-lead")
        self.evidence()

    def test_replaced_lead_cannot_keep_old_review_or_check_or_criterion(self):
        self.ready("synthetic-lead")
        self.assign("synthetic-replacement")
        self.evidence(["check-source", "review-source", "criterion-source"])
        self.review(lead="synthetic-replacement")
        self.evidence(["check-source", "criterion-source"])
        self.check_event(source="feature-lead")
        self.criterion(source="feature-lead")
        self.evidence()
        self.assign("synthetic-replacement")  # Reaffirming the current thread preserves its identity.
        self.evidence()

    def test_feature_lead_source_without_assignment_never_counts(self):
        self.native("worker")
        self.review()
        self.check_event(source="feature-lead")
        self.criterion(source="feature-lead")
        self.evidence(["check-source", "criterion-source"])
        self.assign()
        self.review(lead="synthetic-lead")
        self.evidence(["check-source", "criterion-source"])

    def test_findings_open_disputed_and_reopened_use_latest_disposition(self):
        self.ready()
        for severity in ("blocking", "important"):
            review = self.review([self.finding(severity)])
            self.evidence(["finding-missing"])
            self.dispose(review)
            self.evidence()
            self.dispose(review, "disputed")
            data = self.evidence(["finding-unresolved"])
            self.assertEqual(data["gaps"][0]["reviewRef"], review["seq"])
            self.dispose(review, "declined", expected=2, reason="Synthetic disagreement")
            self.evidence(["finding-unresolved"])
            self.dispose(review)
            self.evidence()

    def test_same_finding_ids_are_distinct_for_each_review_sequence(self):
        self.ready()
        first = self.review([self.finding()])
        second = self.review([self.finding()])
        self.dispose(first)
        data = self.evidence(["finding-missing"])
        self.assertEqual(data["gaps"], [{"code": "finding-missing", "id": "F-1",
                                       "reviewRef": second["seq"], "eventRef": None}])
        self.dispose(second)
        self.evidence()

    def test_minor_reasoned_decline_and_dispute_do_not_add_stricter_policy(self):
        self.ready()
        review = self.review([self.finding("minor")])
        self.evidence(["finding-missing"])
        self.dispose(review, "declined", reason="Synthetic cost exceeds style benefit")
        self.evidence()
        self.dispose(review, "disputed")
        self.evidence()

    def test_fixed_outside_tree_and_author_withdrawal_use_rc1_validation(self):
        self.ready()
        review = self.review([self.finding()])
        for mutation in ({"commit": None}, {"evidence": {}},
                         {"evidence": {"artifact": {"unknown": "Synthetic missing"}}}):
            self.dispose(review, expected=2, **mutation)
        self.evidence(["finding-missing"])
        self.dispose(review)
        self.evidence()
        self.dispose(review, "resolved-outside-tree", evidence="Synthetic external configuration corrected")
        self.evidence()
        self.dispose(review, "withdrawn", expected=2, by="task:synthetic-wrong")
        author = "task:" + review["data"]["reviewer"]["taskId"]
        self.dispose(review, "withdrawn", by=author)
        self.evidence()

    def test_historical_lead_author_withdraws_own_finding_after_replacement(self):
        self.ready("synthetic-lead")
        review = self.review([self.finding()], lead="synthetic-lead")
        self.assign("synthetic-new-lead")
        self.review(lead="synthetic-new-lead")
        self.check_event(source="feature-lead")
        self.criterion(source="feature-lead")
        self.evidence(["finding-missing"])
        self.dispose(review, "withdrawn", expected=2, by="lead:synthetic-new-lead")
        self.dispose(review, "withdrawn", by="lead:synthetic-lead")
        self.evidence()

    def test_second_review_rejection_requires_distinct_later_independent_task(self):
        self.ready()
        first = self.review([self.finding()])
        self.dispose(first, "rejected-by-second-review", expected=2, evidence={"reviewRef": first["seq"]})
        second = self.review()
        self.dispose(first, "rejected-by-second-review", evidence={"reviewRef": second["seq"]})
        self.evidence()

    def test_owner_decision_reserved_author_resolved_reference_and_provenance(self):
        self.ready()
        review = self.review([self.finding()])
        for author in ("worker", "manager", "Owner"):
            decision = self.add("decision", {"question": "Synthetic finding decision",
                "answer": 'Synthetic owner said "accept" in fixture conversation on 2026-10-28.',
                "decidedBy": author, "date": "2026-10-28"})
            self.dispose(review, "owner-decided", expected=2, evidence={"decisionRef": decision["seq"]})
            self.evidence(["finding-missing"])
        unresolved = self.add("decision", {"question": "Synthetic pending decision",
            "answer": {"unknown": "Pending"}, "decidedBy": "owner", "date": "2026-10-28"})
        for ref in (unresolved["seq"], 999):
            self.dispose(review, "owner-decided", expected=2, evidence={"decisionRef": ref})
        owner = self.add("decision", {"question": "Synthetic owner decision",
            "answer": 'Synthetic owner said "accept" in fixture conversation on 2026-10-28.',
            "decidedBy": "owner", "date": "2026-10-28"})
        self.dispose(review, "owner-decided", evidence={"decisionRef": owner["seq"]})
        self.evidence()

    def test_delivery_facts_neither_grant_nor_block(self):
        self.add("decision", {"question": "Synthetic delivery readiness", "answer": "Ready",
                              "decidedBy": "manager", "date": "2026-10-28"})
        self.evidence(["head-missing", "check-missing", "review-missing", "criterion-missing"])
        self.evidence(["head-missing", "check-missing", "review-missing", "criterion-missing"], text=True)
        self.ready()
        self.add("decision", {"question": "Synthetic delivery readiness", "answer": {"unknown": "Integration pending"},
                              "decidedBy": "manager", "date": "2026-10-28"})
        self.evidence()

    def test_pr_ready_missing_push_pr_and_fully_ready_succeeded(self):
        self.ready()
        self.evidence(["push-missing", "pr-missing"], pr_ready=True)
        self.operation("push")
        self.evidence(["pr-missing"], pr_ready=True)
        self.operation("pull-request")
        self.evidence(pr_ready=True)
        self.evidence(text=True, pr_ready=True)

    def test_pr_ready_noop_without_child_and_revocation_failure(self):
        self.ready()
        self.operation("push", outcome="no-op", summary="omit")
        self.operation("pull-request", outcome="no-op", summary="omit")
        self.evidence(pr_ready=True)
        for operation in ("push", "pull-request"):
            self.operation(operation, summary={"childStarted": True, "childStatus": 0, "revocation": "failed"})
        self.evidence(pr_ready=True)

    def test_pr_ready_latest_failures_stale_and_unknown_never_hide_behind_success(self):
        self.ready()
        self.operation("push")
        self.operation("pull-request")
        cases = [("failed", {}, "outcome"), ("refused", {}, "outcome"),
                 ("unknown", {}, "unresolved"), ("succeeded", {"head": self.base}, "stale"),
                 ("succeeded", {"summary": {"childStarted": True, "childStatus": 7}}, "status"),
                 ("succeeded", {"summary": {"childStarted": True, "childStatus": None}}, "unresolved"),
                 ("no-op", {"summary": {"childStarted": True}}, "unresolved"),
                 ("no-op", {"summary": {"childStarted": False, "refusal": "Synthetic refusal"}}, "status"),
                 ("succeeded", {"summary": "omit"}, "status"), ("no-op", {"summary": None}, "status")]
        for operation, prefix in (("push", "push"), ("pull-request", "pr")):
            for outcome, options, suffix in cases:
                with self.subTest(operation=operation, outcome=outcome, options=options):
                    intent, result = self.operation(operation, outcome=outcome, **options)
                    self.evidence([prefix + "-" + suffix], pr_ready=True)
                    self.add("op-result", {**result, "outcome": "succeeded", "statusSummary": {
                        "childStarted": True, "childStatus": 0}})
                    # Stale attempts need a new exact-head operation, not a changed join.
                    if options.get("head"):
                        self.operation(operation)
                    self.evidence(pr_ready=True)

    def test_pr_ready_later_intent_and_reopened_old_attempt_require_resolution(self):
        self.ready()
        old, old_result = self.operation("push")
        self.operation("pull-request")
        new, new_result = self.operation("push", complete=False)
        self.add("op-result", old_result)
        data = self.evidence(["push-unresolved"], pr_ready=True)
        self.assertEqual(data["gaps"][0]["eventRef"], new["seq"])
        self.add("op-result", new_result)
        self.evidence(pr_ready=True)
        self.add("reconciliation", {"item": {"kind": "operation", "ref": old["data"]["opId"]},
            "t3Facts": {"unknown": "Synthetic pending"}, "gitFacts": {"head": self.head},
            "outcome": "unknown-liveness"})
        self.evidence(["push-unresolved"], pr_ready=True)
        self.add("op-result", old_result)
        self.evidence(pr_ready=True)

    def test_pr_ready_resolved_old_attempts_preserve_new_confirmation(self):
        self.ready()
        self.operation("push")
        self.operation("pull-request")
        for operation, prefix in (("push", "push"), ("pull-request", "pr")):
            for options in ({"outcome": "refused", "summary": {"childStarted": False, "refusal": "Synthetic pre-child refusal"}},
                            {"outcome": "failed"}, {"outcome": "failed", "head": self.base}):
                _, old_result = self.operation(operation, **options)
                self.operation(operation)
                self.evidence(pr_ready=True)
                self.add("op-result", old_result)
                self.evidence(pr_ready=True)
            _, old_result = self.operation(operation, outcome="unknown")
            self.operation(operation)
            self.evidence([prefix + "-unresolved"], pr_ready=True)
            self.add("op-result", {**old_result, "outcome": "failed"})
            self.evidence(pr_ready=True)
            self.operation(operation, outcome="failed")
            self.evidence([prefix + "-outcome"], pr_ready=True)
            self.operation(operation)
            self.evidence(pr_ready=True)

    def test_bad_sha_missing_work_and_malformed_corrupt_history_exit2(self):
        self.ready()
        for head in ("short", "A" * 40, "g" * 40, self.head + "\n"):
            result = self.command("evidence", "check", "--repo", REPO, "--work", self.work_id, "--head", head)
            self.assertEqual((result.returncode, result.stdout), (2, ""))
            self.assertNotIn(head, result.stderr)
        for repo, work in ((REPO, "MISSING"), ("../synthetic-invalid", self.work_id)):
            result = self.command("evidence", "check", "--repo", repo, "--work", work, "--head", self.head)
            self.assertEqual((result.returncode, result.stdout), (2, ""))
        directory = self.local / "state/records" / REPO / "work" / self.work_id
        target = directory / "events/1.json"
        original = target.read_bytes()
        event = json.loads(original)
        for payload in (b'{"synthetic-secret":', canonical_json({**event, "schemaVersion": 999}),
                        canonical_json({**event, "data": {**event["data"], "reviewBase": self.next_head}})):
            target.write_bytes(payload)
            result = self.command("evidence", "check", "--repo", REPO, "--work", self.work_id, "--head", self.head)
            self.assertEqual((result.returncode, result.stdout), (2, ""))
            self.assertNotIn("synthetic-secret", result.stderr)
        target.write_bytes(original)
        self.evidence()

    def test_native_review_provenance_refusals_and_corruption_fail_closed(self):
        worker_task = self.native("worker")[0]
        self.check_event()
        self.criterion()
        task, result, _ = self.native("reviewer", complete=False)
        data = {"reviewer": {"kind": "task", "taskId": task}, "base": self.base,
                "head": self.head, "verdict": "approve", "findings": []}
        self.add("review", {**data, "reviewer": {"kind": "task", "taskId": worker_task}}, 2)
        self.add("review", data, 2)
        self.evidence(["review-missing"])
        self.add("result", result)
        review = self.add("review", data)
        self.evidence()
        target = self.local / "state/records" / REPO / "work" / self.work_id / "events" / f'{review["seq"]}.json'
        target.write_bytes(canonical_json({**review, "data": {**data,
            "reviewer": {"kind": "task", "taskId": "synthetic-invented"}}}))
        result = self.command("evidence", "check", "--repo", REPO, "--work", self.work_id, "--head", self.head)
        self.assertEqual((result.returncode, result.stdout), (2, ""))

    def test_import_register_purity_and_api_invalid_options(self):
        self.assertIsNotNone(importlib.util.find_spec("t3compose.evidence"), "evidence module missing")
        from t3compose import evidence
        with patch("builtins.open", side_effect=AssertionError("state read")), \
                patch.object(Path, "open", side_effect=AssertionError("path read")), \
                patch.object(process, "run", side_effect=AssertionError("process I/O")), \
                patch.object(records, "load_work", side_effect=AssertionError("record I/O")):
            importlib.reload(evidence)
            self.assertIn(("evidence", "check"), cli.command_names())
        for head, mode in ((None, False), ("a" * 39, False), (self.head, 1)):
            with self.assertRaises(cli.CommandError) as caught:
                evidence.check(self.local, REPO, self.work_id, head, pr_ready=mode)
            self.assertEqual(caught.exception.code, cli.ExitCode.INVALID_INPUT)
