"""Offline pilot integration: real Jev/records/derivation, injected HTTP only."""
from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
import sys
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT
from tests.test_jev import Reply, response
from t3compose import config, jsonio, paths, records

SCRIPT = ROOT / "live/router_pilot.py"
REPO = "synthetic/router-pilot"
KEY = "synthetic-pilot-secret-sentinel"
FACTS = {"credentials-or-guardrail", "recovery-or-concurrency",
         "shared-interface", "document-change"}


def digest(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class OfflineRouterPilot(OfflineTestCase):
    def setUp(self):
        self.allow_program("grep")
        self.local = paths.local_dir()
        (self.local / "state").mkdir(parents=True)
        self.listfile = self.local / "state/private-identifiers.txt"
        self.listfile.write_text("private-synthetic-pilot-identifier\n")
        self.cfg = json.loads((ROOT / "docs/examples/agent-routing.v3.example.json").read_text())
        self.keyfile = self.temp / "synthetic-pilot-key.json"
        self.write(self.keyfile, {"apiKey": KEY})
        self.cfg["jev"]["keyFile"] = {"path": str(self.keyfile), "field": "apiKey"}
        self.cfgpath = self.local / "agent-routing.json"
        self.write(self.cfgpath, self.cfg)
        self.enterContext(patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}))

        self.catalog = {"providers": [
            {"providerInstanceId": "codex", "driverKind": "codex",
             "models": [{"id": "gpt-6.1-sol", "options": [{"id": "reasoningEffort", "values": ["high"]}]}],
             "canRunChildTask": True, "canRunCrossProviderChildTask": True, "constraints": []}
        ]}
        self.catpath = self.temp / "catalog.json"
        self.write(self.catpath, self.catalog)

        self.examples_path = ROOT / "pilot/router-examples.json"
        self.assertTrue(self.examples_path.exists(), "synthetic dataset required")
        self.dataset = json.loads(self.examples_path.read_text())
        self.examples = self.temp / "examples.json"
        self.write(self.examples, self.dataset)
        self.read_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def write(self, path, value):
        path.write_bytes(jsonio.canonical_json(value))

    def invoke(self, answers=None, *, argv=None, during=None, default_local=False):
        self.assertTrue(SCRIPT.exists(), "synthetic pilot runner required")
        args = [str(SCRIPT), "--examples", str(self.examples), "--catalog", str(self.catpath),
                "--catalog-read-at", self.read_at]
        if not default_local:
            args += ["--local-dir", str(self.local)]
        if argv is not None:
            args = [str(SCRIPT), *argv]
        calls = []

        def http(request, *, timeout):
            n = len(calls)
            calls.append(request)
            body = json.loads(request.data)
            dataset = json.loads(self.examples.read_text())
            task = dataset["examples"][n]["task"]
            self.assertEqual(body["state"], {"task": task, "context": None})
            self.assertNotIn("expected", body["state"])
            self.assertEqual(body["questions"], {
                q: {"type": "choice", **self.cfg["classification"][q]}
                for q in ("lane", "criticality", "scope")
            })
            self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
            self.assertEqual(timeout, self.cfg["jev"]["timeoutSeconds"])
            if during:
                during(n, calls, body)
            ans = answers(n, task) if callable(answers) else (answers[n] if answers else None)
            if ans is None:
                gold = dataset["examples"][n]["expected"]
                g_lane = gold["lane"]
                g_crit = gold["criticality"]
                g_scope = gold["scope"]
                # In gold, floors may have raised scope/criticality. Raw might be standard/non_critical for floor examples.
                raw_lane = "MIXED" if g_lane == "MIXED" else g_lane
                raw_crit = "NON_CRITICAL" if gold["floors"] and g_crit == "CORE" else g_crit
                raw_scope = "STANDARD" if gold["floors"] and g_scope == "COMPLEX" else g_scope
                payload = response(lane=raw_lane, criticality=raw_crit, scope=raw_scope)
            elif isinstance(ans, Exception):
                raise ans
            elif isinstance(ans, dict):
                payload = ans
            else:
                payload = response(**ans)
            return Reply(jsonio.canonical_json(payload))

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch("t3compose.jev.open_http", side_effect=http):
            import live.router_pilot as pilot
            old_argv = sys.argv
            sys.argv = args
            try:
                code = pilot.main()
            finally:
                sys.argv = old_argv

        self.assertNotIn(KEY, out.getvalue())
        self.assertNotIn(KEY, err.getvalue())
        self.assertNotIn(str(self.keyfile), out.getvalue())
        self.assertNotIn(str(self.keyfile), err.getvalue())

        parsed = json.loads(out.getvalue()) if out.getvalue().strip().startswith("{") else None
        return code, parsed, err.getvalue(), calls

    def test_shipped_dataset_format_and_coverage(self):
        entries = self.dataset["examples"]
        self.assertEqual(len(entries), 14)
        self.assertEqual(self.dataset.get("label"), "synthetic")
        self.assertIn(self.dataset.get("version"), (1, 2))
        covered_floors = set()
        lanes = set()
        criticalities = set()
        scopes = set()
        doc_count = 0
        for entry in entries:
            self.assertEqual(entry["label"], "synthetic")
            gold = entry["expected"]
            lanes.add(gold["lane"])
            criticalities.add(gold["criticality"])
            scopes.add(gold["scope"])
            covered_floors.update(gold["floors"])
            if "document-change" in gold["floors"]:
                doc_count += 1
            # Verify software engineering content (no recipes or travel paragraphs)
            task_text = json.dumps(entry["task"]).lower()
            self.assertFalse(any(bad in task_text for bad in ["recipe", "travel", "bakery"]))
        self.assertTrue(FACTS <= covered_floors)
        self.assertEqual(lanes, {"BACKEND_LOGIC", "UI_FRONTEND", "MIXED"})
        self.assertEqual(criticalities, {"CORE", "NON_CRITICAL"})
        self.assertEqual(scopes, {"STANDARD", "COMPLEX"})
        self.assertGreaterEqual(doc_count, 1)

    def test_invalid_dataset_fails_before_http(self):
        data = copy.deepcopy(self.dataset)
        data["examples"][0]["expected"]["floors"].append("nonexistent-floor")
        self.write(self.examples, data)
        code, report, err, calls = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(len(calls), 0)
        self.assertIn("refused", err)

    def test_expected_labels_never_in_http_request(self):
        code, report, _, calls = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 14)
        for call in calls:
            body = json.loads(call.data)
            self.assertNotIn("expected", body["state"])
            self.assertNotIn("expected", body)

    def test_full_run_exactly_14_calls_decline_true_and_derived_outcomes(self):
        code, report, _, calls = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(len(calls), 14)
        self.assertEqual(len(report["results"]), 14)
        summary = report["summary"]
        self.assertEqual(summary["derived"], 14)
        self.assertEqual(summary["incomplete"], 0)
        self.assertEqual(summary["laneMismatches"], 0)
        self.assertEqual(summary["matchingRoutes"]["scope"], 14)
        self.assertEqual(summary["matchingRoutes"]["criticality"], 14)
        self.assertEqual(summary["mistakenCheapRoutes"]["scope"], 0)
        self.assertEqual(summary["unnecessaryExpensiveRoutes"]["scope"], 0)

        # Check records integrity: work was created, classification written, no selection events
        works = records.list_work(self.local, REPO)["works"]
        self.assertEqual(len(works), 14)
        for res in report["results"]:
            w_events = records.load_work(self.local, REPO, res["workId"])["events"]
            kinds = [e["kind"] for e in w_events]
            self.assertIn("routing-input", kinds)
            self.assertIn("routing-classification", kinds)
            self.assertNotIn("routing-selection", kinds)
            self.assertIsNotNone(res["derived"])
            self.assertIn(res["derived"]["startingStage"], (1, 2))
            self.assertIn(res["derived"]["lane"], ("backend-logic", "ui-frontend"))

    def test_defaulting_behavior_preserving_confident_answers(self):
        # Example 0: low confidence on scope (0.45)
        def answers(n, task):
            if n == 0:
                return response(lane="UI_FRONTEND", criticality="NON_CRITICAL", scope="STANDARD",
                                scope_conf=0.45)
            return None

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        res0 = report["results"][0]
        self.assertEqual(res0["classification"]["outcome"], "defaulted")
        self.assertIsNotNone(res0["judgment"])
        self.assertEqual(res0["judgment"]["basis"], "default-low-confidence")
        self.assertEqual(res0["judgment"]["defaulted"], ["scope"])
        self.assertEqual(res0["derived"]["scope"], "COMPLEX")
        self.assertEqual(res0["derived"]["criticality"], "NON_CRITICAL")
        self.assertEqual(res0["derived"]["lane"], "ui-frontend")
        # Gold expected STANDARD for scope on example 0; derived COMPLEX -> expensive mistake
        self.assertEqual(res0["scopeComparison"], "expensive")
        summary = report["summary"]
        self.assertEqual(summary["defaultedAnswers"]["counts"]["scope"], 1)
        self.assertEqual(summary["unnecessaryExpensiveRoutes"]["scope"], 1)

    def test_insufficient_context_defaults_scope(self):
        def answers(n, task):
            if n == 0:
                return response(lane="UI_FRONTEND", criticality="NON_CRITICAL", scope="INSUFFICIENT_CONTEXT")
            return None

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        res0 = report["results"][0]
        self.assertEqual(res0["judgment"]["basis"], "default-insufficient-context")
        self.assertIn("scope", res0["judgment"]["defaulted"])
        self.assertEqual(res0["derived"]["scope"], "COMPLEX")

    def test_service_failure_all_answer_api_defaults(self):
        def answers(n, task):
            if n == 0:
                raise OSError("simulated OpenRouter timeout")
            return None

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        res0 = report["results"][0]
        self.assertEqual(res0["classification"]["outcome"], "defaulted")
        self.assertEqual(res0["judgment"]["basis"], "default-jev-failure")
        self.assertEqual(set(res0["judgment"]["defaulted"]), {"lane", "criticality", "scope"})
        self.assertEqual(res0["derived"]["lane"], "backend-logic")
        self.assertEqual(res0["derived"]["criticality"], "CORE")
        self.assertEqual(res0["derived"]["scope"], "COMPLEX")

    def test_mixed_mapping_not_defaulted(self):
        # For fullstack-preference (n=13), Jev answers MIXED
        code, report, _, _ = self.invoke()
        self.assertEqual(code, 0)
        # Find the mixed example
        mixed_res = next(r for r in report["results"] if r["expected"]["lane"] == "MIXED")
        self.assertEqual(mixed_res["classification"]["answers"]["lane"]["choice"], "MIXED")
        if mixed_res["judgment"]:
            self.assertNotIn("lane", mixed_res["judgment"]["defaulted"])
        self.assertEqual(mixed_res["derived"]["lane"], "backend-logic")
        self.assertFalse(mixed_res["laneMismatch"])

    def test_risk_floors_and_stage_derivation(self):
        code, report, _, _ = self.invoke()
        self.assertEqual(code, 0)
        by_id = {r["id"]: r for r in report["results"]}
        # shared-parameter: shared-interface raises criticality to CORE, scope stays STANDARD
        sp = by_id["shared-parameter"]
        self.assertEqual(sp["derived"]["criticality"], "CORE")
        self.assertEqual(sp["derived"]["scope"], "STANDARD")
        self.assertEqual(sp["derived"]["startingStage"], 1)

        # credential-field: credentials-or-guardrail raises both
        cf = by_id["credential-field"]
        self.assertEqual(cf["derived"]["criticality"], "CORE")
        self.assertEqual(cf["derived"]["scope"], "COMPLEX")
        self.assertEqual(cf["derived"]["startingStage"], 2)

        # readme-spelling: document-change has NO raising effect
        rs = by_id["readme-spelling"]
        self.assertEqual(rs["derived"]["criticality"], "NON_CRITICAL")
        self.assertEqual(rs["derived"]["scope"], "STANDARD")
        self.assertEqual(rs["derived"]["startingStage"], 1)

    def test_cheap_and_expensive_mistakes_by_question(self):
        # Craft answers to generate cheap and expensive mistakes in scope and criticality
        def answers(n, task):
            if n == 0:
                # expected: scope STANDARD, crit NON_CRITICAL -> make expensive in both
                return response(lane="UI_FRONTEND", criticality="CORE", scope="COMPLEX")
            if n == 12:  # architecture-choice: expected scope COMPLEX, crit CORE -> make cheap in both
                return response(lane="BACKEND_LOGIC", criticality="NON_CRITICAL", scope="STANDARD")
            return None

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        summary = report["summary"]
        self.assertGreater(summary["unnecessaryExpensiveRoutes"]["scope"], 0)
        self.assertGreater(summary["unnecessaryExpensiveRoutes"]["criticality"], 0)
        self.assertGreater(summary["mistakenCheapRoutes"]["scope"], 0)
        self.assertGreater(summary["mistakenCheapRoutes"]["criticality"], 0)

    def test_mixed_direction_row(self):
        # Single row: cheap in scope, expensive in criticality
        # Example 0: gold scope STANDARD, gold crit NON_CRITICAL
        # If derived scope is STANDARD (matches), but if gold scope was COMPLEX and gold crit was NON_CRITICAL:
        # Example 12: architecture-choice gold: scope COMPLEX, crit CORE.
        # Let's override example 12 answers to be scope STANDARD (cheap!), crit CORE (match)
        # And override example 0 to be scope COMPLEX (expensive), crit NON_CRITICAL (match)
        def answers(n, task):
            if n == 3:  # unit-test-suite: gold scope STANDARD, crit NON_CRITICAL
                # Make scope COMPLEX (expensive) and crit NON_CRITICAL (match)
                return response(lane="BACKEND_LOGIC", criticality="NON_CRITICAL", scope="COMPLEX")
            return None

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        res3 = report["results"][3]
        self.assertEqual(res3["scopeComparison"], "expensive")
        self.assertEqual(res3["criticalityComparison"], "match")

    def test_floor_corrected_raw_answers_not_counted_as_mistake(self):
        # When raw was STANDARD/NON_CRITICAL for credential-field, floor raises to COMPLEX/CORE.
        # Because gold expected COMPLEX/CORE, final derived matches gold -> match!
        code, report, _, _ = self.invoke()
        self.assertEqual(code, 0)
        by_id = {r["id"]: r for r in report["results"]}
        cf = by_id["credential-field"]
        self.assertEqual(cf["scopeComparison"], "match")
        self.assertEqual(cf["criticalityComparison"], "match")

    def test_two_runs_preserve_historical_evidence(self):
        code1, report1, _, _ = self.invoke()
        self.assertEqual(code1, 0)
        code2, report2, _, _ = self.invoke()
        self.assertEqual(code2, 0)
        self.assertNotEqual(report1["runId"], report2["runId"])
        works = records.list_work(self.local, REPO)["works"]
        self.assertEqual(len(works), 28)

    def test_catalog_stale_or_malformed_refused(self):
        # Stale catalog
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=1000)).isoformat().replace("+00:00", "Z")
        code, _, err, _ = self.invoke(argv=[str(SCRIPT), "--examples", str(self.examples),
                                            "--catalog", str(self.catpath),
                                            "--catalog-read-at", stale_time,
                                            "--local-dir", str(self.local)])
        self.assertEqual(code, 2)
        self.assertIn("refused", err)

    def test_config_validation_and_exit_codes(self):
        # Bad config version
        self.write(self.cfgpath, {"version": 2})
        code, _, err, _ = self.invoke()
        self.assertEqual(code, 3)
        self.assertIn("refused", err)

    def test_storage_failure_preserves_partial_evidence(self):
        from t3compose import cli

        def fail_classify(*args, **kwargs):
            raise cli.CommandError("disk failure", cli.ExitCode.INVALID_INPUT)

        with patch("t3compose.jev.classify", side_effect=fail_classify):
            code, report, err, _ = self.invoke()
            self.assertEqual(code, 2)
            self.assertFalse(report["complete"])
            self.assertEqual(report["failure"]["stage"], "classify")
            self.assertEqual(report["results"][0]["outcome"], "incomplete")
