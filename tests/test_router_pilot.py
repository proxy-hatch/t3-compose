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
                offered = tuple(self.cfg["classification"]["lane"]["criteria"])
                raw_lane = g_lane if g_lane in offered else "BACKEND_LOGIC"
                raising = [self.cfg["riskFacts"][fact] for fact in gold["floors"]]
                raw_crit = "NON_CRITICAL" if g_crit == "CORE" and any(
                    fact.get("criticality") == "CORE" for fact in raising) else g_crit
                raw_scope = "STANDARD" if g_scope == "COMPLEX" and any(
                    fact.get("scope") == "COMPLEX" for fact in raising) else g_scope
                payload = response(lane=raw_lane, criticality=raw_crit, scope=raw_scope, offered=offered)
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
        self.assertEqual(len(entries), 17)
        self.assertEqual(self.dataset.get("label"), "synthetic")
        self.assertEqual(self.dataset.get("version"), 2)
        covered_floors = set()
        lanes = set()
        criticalities = set()
        scopes = set()
        doc_count = 0
        docs_labeled = [entry["id"] for entry in entries if entry["expected"]["lane"] == "DOCS"]
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
        self.assertEqual(lanes, {"BACKEND_LOGIC", "UI_FRONTEND", "MIXED", "DOCS"})
        self.assertGreaterEqual(len(docs_labeled), 2)
        boundary = next(entry for entry in entries if entry["id"] == "cli-flag-with-guide")
        self.assertEqual(boundary["expected"]["lane"], "BACKEND_LOGIC")  # code and its docs together
        self.assertIn("document-change", boundary["expected"]["floors"])
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
        self.assertEqual(len(calls), 17)
        for call in calls:
            body = json.loads(call.data)
            self.assertNotIn("expected", body["state"])
            self.assertNotIn("expected", body)

    def test_full_run_exactly_17_calls_decline_true_and_derived_outcomes(self):
        code, report, _, calls = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(len(calls), 17)
        self.assertEqual(len(report["results"]), 17)
        summary = report["summary"]
        self.assertEqual(summary["derived"], 17)
        self.assertEqual(summary["incomplete"], 0)
        self.assertEqual(summary["laneMismatches"], 0)
        self.assertEqual(summary["matchingRoutes"]["scope"], 17)
        self.assertEqual(summary["matchingRoutes"]["criticality"], 17)
        self.assertEqual(summary["mistakenCheapRoutes"]["scope"], 0)
        self.assertEqual(summary["unnecessaryExpensiveRoutes"]["scope"], 0)

        # Check records integrity: work was created, classification written, no selection events
        works = records.list_work(self.local, REPO)["works"]
        self.assertEqual(len(works), 17)
        for res in report["results"]:
            w_events = records.load_work(self.local, REPO, res["workId"])["events"]
            kinds = [e["kind"] for e in w_events]
            self.assertIn("routing-input", kinds)
            self.assertIn("routing-classification", kinds)
            self.assertNotIn("routing-selection", kinds)
            by_ref = {e["seq"]: e for e in w_events}
            stored = by_ref[res["recordRefs"]["classification"]]["data"]
            for key, value in res["classification"].items():
                if key != "classificationRef":
                    self.assertEqual(value, stored[key])
            for kind in ("input", "judgment"):
                if kind in res["recordRefs"]:
                    data = by_ref[res["recordRefs"][kind]]["data"]
                    for key in ("inputDigest", "policyDigest"):
                        self.assertEqual(data[key], stored[key])
            raw = jsonio.canonical_json(w_events)
            self.assertNotIn(KEY.encode(), raw)
            self.assertNotIn(str(self.keyfile).encode(), raw)
            self.assertIsNotNone(res["derived"])
            self.assertIn(res["derived"]["startingStage"], (1, 2))
            self.assertIn(res["derived"]["lane"], ("backend-logic", "ui-frontend", "docs"))

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

    def test_docs_examples_derive_the_docs_lane(self):
        code, report, _, _ = self.invoke()
        self.assertEqual(code, 0)
        docs = [r for r in report["results"] if r["expected"]["lane"] == "DOCS"]
        self.assertGreaterEqual(len(docs), 2)
        for res in docs:
            with self.subTest(example=res["id"]):
                self.assertEqual(res["classification"]["answers"]["lane"]["choice"], "DOCS")
                self.assertEqual(res["classification"]["outcome"], "accepted")
                self.assertIsNone(res["judgment"])
                self.assertEqual(res["derived"]["lane"], "docs")
                self.assertFalse(res["laneMismatch"])
        self.assertEqual(report["summary"]["laneMismatches"], 0)
        self.assertEqual(report["summary"]["defaultedAnswers"]["counts"], {"lane": 0, "criticality": 0, "scope": 0})

    def test_docs_gold_answered_as_backend_logic_is_a_lane_mismatch(self):
        def answers(n, task):
            gold = self.dataset["examples"][n]["expected"]
            if gold["lane"] != "DOCS":
                return None
            return response(lane="BACKEND_LOGIC", criticality=gold["criticality"], scope=gold["scope"])

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        docs = [r for r in report["results"] if r["expected"]["lane"] == "DOCS"]
        for res in docs:
            with self.subTest(example=res["id"]):
                self.assertEqual(res["derived"]["lane"], "backend-logic")
                self.assertTrue(res["laneMismatch"])
        self.assertEqual(report["summary"]["laneMismatches"], len(docs))

    def test_a_docs_answer_survives_a_defaulted_neighbor(self):
        target = "docs-message-templates"

        def answers(n, task):
            if self.dataset["examples"][n]["id"] != target:
                return None
            return response(lane="DOCS", criticality="NON_CRITICAL", crit_conf=.3, scope="STANDARD")

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        row = next(r for r in report["results"] if r["id"] == target)
        self.assertEqual((row["judgment"]["lane"], row["judgment"]["defaulted"]), ("DOCS", ["criticality"]))
        self.assertEqual((row["derived"]["lane"], row["derived"]["criticality"]), ("docs", "CORE"))
        self.assertEqual((row["laneMismatch"], row["criticalityComparison"]), (False, "expensive"))

    def test_an_unsure_docs_answer_takes_the_backend_logic_default(self):
        target = "docs-operator-guide"

        def answers(n, task):
            if self.dataset["examples"][n]["id"] != target:
                return None
            return response(lane="DOCS", criticality="NON_CRITICAL", scope="STANDARD", lane_conf=.3)

        code, report, _, _ = self.invoke(answers)
        self.assertEqual(code, 0)
        row = next(r for r in report["results"] if r["id"] == target)
        self.assertEqual(row["classification"]["outcome"], "defaulted")
        self.assertEqual((row["judgment"]["lane"], row["judgment"]["basis"], row["judgment"]["defaulted"]),
                         ("BACKEND_LOGIC", "default-low-confidence", ["lane"]))
        self.assertEqual((row["derived"]["lane"], row["laneMismatch"]), ("backend-logic", True))
        defaulted = report["summary"]["defaultedAnswers"]
        self.assertEqual(defaulted["byQuestion"]["lane"], {"count": 1, "exampleIds": [target]})
        self.assertEqual(defaulted["byBasis"]["default-low-confidence"], 1)

    def test_a_config_without_the_docs_criterion_still_runs(self):
        del self.cfg["classification"]["lane"]["criteria"]["DOCS"]
        self.write(self.cfgpath, self.cfg)
        code, report, _, calls = self.invoke()
        self.assertEqual((code, report["complete"], len(calls)), (0, True, 17))
        for call in calls:
            self.assertNotIn("DOCS", json.loads(call.data)["questions"]["lane"]["criteria"])
        docs = [r for r in report["results"] if r["expected"]["lane"] == "DOCS"]
        self.assertTrue(docs)
        for res in docs:
            self.assertEqual((res["derived"]["lane"], res["laneMismatch"]), ("backend-logic", True))
        self.assertEqual(report["summary"]["laneMismatches"], len(docs))

    def test_load_examples_accepts_the_docs_label_and_refuses_other_spellings(self):
        from live.router_pilot import load_examples
        for lane, valid in (("DOCS", True), ("docs", False), ("Docs", False), ("DOCUMENTATION", False)):
            data = copy.deepcopy(self.dataset)
            data["examples"][0]["expected"]["lane"] = lane
            self.write(self.examples, data)
            with self.subTest(lane=lane):
                if valid:
                    self.assertEqual(len(load_examples(self.examples, self.cfg)[0]), 17)
                else:
                    with self.assertRaises(ValueError):
                        load_examples(self.examples, self.cfg)

    def test_summary_compares_the_docs_label_with_the_derived_lane(self):
        from live.router_pilot import summarize
        rows = []
        for ident, gold_lane, derived_lane in (("a", "DOCS", "docs"), ("b", "DOCS", "backend-logic"),
                                               ("c", "UI_FRONTEND", "ui-frontend"), ("d", "MIXED", "backend-logic"),
                                               ("e", "BACKEND_LOGIC", "docs")):
            rows.append({"id": ident, "judgment": None,
                         "expected": {"lane": gold_lane, "scope": "STANDARD", "criticality": "CORE", "floors": []},
                         "derived": {"lane": derived_lane, "scope": "STANDARD", "criticality": "CORE",
                                     "appliedFloors": []}})
        summary = summarize(rows)
        self.assertEqual([row["laneMismatch"] for row in rows], [False, True, False, False, True])
        self.assertEqual(summary["laneMismatches"], 2)

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
        from live.router_pilot import summarize
        rows = []
        for ident, gold_scope, gold_crit, scope, crit in (
                ("a", "COMPLEX", "NON_CRITICAL", "STANDARD", "CORE"),
                ("b", "STANDARD", "CORE", "COMPLEX", "NON_CRITICAL")):
            rows.append({"id": ident, "judgment": None,
                "expected": {"lane": "BACKEND_LOGIC", "scope": gold_scope,
                             "criticality": gold_crit, "floors": []},
                "derived": {"lane": "backend-logic", "scope": scope,
                            "criticality": crit, "appliedFloors": []}})
        summary = summarize(rows)
        self.assertEqual([(r["scopeComparison"], r["criticalityComparison"]) for r in rows],
                         [("cheap", "expensive"), ("expensive", "cheap")])
        for key in ("mistakenCheapRoutes", "unnecessaryExpensiveRoutes"):
            self.assertEqual(summary[key], {"scope": 1, "criticality": 1})
        self.assertEqual(summary["matchingRoutes"], {"scope": 0, "criticality": 0})

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
        self.assertEqual(len(works), 34)

    def test_catalog_stale_or_malformed_refused(self):
        """Refused: over 24 hours old, in the future, malformed. Run: within 24 hours, even past the old 15 minutes."""

        def seconds_ago(seconds):
            return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

        def run_at(read_at):
            return self.invoke(argv=["--examples", str(self.examples), "--catalog", str(self.catpath),
                                     "--catalog-read-at", read_at, "--local-dir", str(self.local)])

        for read_at in (seconds_ago(86500), seconds_ago(-60), "not-a-time"):
            code, _, err, calls = run_at(read_at)
            self.assertEqual(code, 2, read_at)
            self.assertIn("refused", err)
            self.assertEqual(calls, [])
        self.assertEqual(records.list_work(self.local, REPO)["works"], [])
        for seconds in (0, 901, 86300):
            code, _, _, _ = run_at(seconds_ago(seconds))
            self.assertEqual(code, 0, seconds)

    def test_config_validation_and_exit_codes(self):
        # Bad config version
        self.write(self.cfgpath, {"version": 2})
        code, _, err, _ = self.invoke()
        self.assertEqual(code, 3)
        self.assertIn("refused", err)

    def test_corrections_policy_changes_refuse_mismatched_floors(self):
        import live.router_pilot as pilot
        original = copy.deepcopy(self.cfg)
        real_derive = pilot.derive_route
        for mode in ("during-remove", "during-raise", "between"):
            self.cfg = copy.deepcopy(original)
            self.write(self.cfgpath, self.cfg)

            def edit():
                if mode == "during-raise":
                    self.cfg["riskFacts"]["document-change"]["scope"] = "COMPLEX"
                else:
                    self.cfg["riskFacts"]["shared-interface"].pop("criticality", None)
                self.write(self.cfgpath, self.cfg)

            def during(n, calls, body):
                if n == 0 and mode != "between":
                    edit()

            def derive(*args):
                value = real_derive(*args)
                if mode == "between":
                    edit()
                return value

            with self.subTest(mode=mode), patch.object(pilot, "derive_route", side_effect=derive):
                code, report, _, calls = self.invoke(during=during)
                self.assertEqual((code, report["complete"], len(calls)), (2, False, 1))
                self.assertIsNone(report["results"][1]["derived"])
                if mode != "between":
                    self.assertIsNone(report["results"][0]["derived"])

    def test_corrections_incomplete_decisions_never_derive(self):
        from t3compose import cli, jev
        real_classify = jev.classify
        for mode in ("refused", "missing-judgment", "unknown-answers", "mixed-without-judgment"):
            def classify(*args, **kwargs):
                result = real_classify(*args, **kwargs)
                data = copy.deepcopy(result.data)
                if mode in ("missing-judgment", "mixed-without-judgment"):
                    data.pop("judgment", None)
                if mode == "unknown-answers":
                    data["answers"] = {"unknown": "synthetic unavailable"}
                return cli.Outcome(data, 4 if mode == "refused" else 0)

            def answer(n, task):
                return response(lane="MIXED" if mode == "mixed-without-judgment" else "BACKEND_LOGIC",
                                scope_conf=.45 if mode == "missing-judgment" else .9)

            with self.subTest(mode=mode), patch.object(jev, "classify", side_effect=classify):
                code, report, _, calls = self.invoke(answer)
                self.assertEqual((code, report["complete"], len(calls)), (2, False, 1))
                self.assertIsNone(report["results"][0]["derived"])
                self.assertIn("classification", report["results"][0]["recordRefs"])

    def test_corrections_catalog_expiry_after_preflight_is_metadata_only(self):
        import live.router_pilot as pilot
        current = [datetime.now(timezone.utc)]
        clock = type("Clock", (), {"now": staticmethod(lambda zone: current[0])})

        def advance(n, calls, body):
            if n == 0:
                current[0] += timedelta(seconds=1000)

        with patch.object(pilot, "datetime", clock):
            code, report, _, calls = self.invoke(during=advance)
        self.assertEqual((code, report["complete"], len(calls)), (0, True, 17))

    def test_corrections_example_version_is_exact(self):
        from live.router_pilot import load_examples
        self.dataset["version"] = 1
        self.write(self.examples, self.dataset)
        with self.assertRaises(ValueError):
            load_examples(self.examples, self.cfg)

    def test_corrections_lane_and_criticality_defaults_are_attributed(self):
        for question, changes in (("lane", {"lane_conf": .45}),
                                  ("criticality", {"crit_conf": .45})):
            def answer(n, task):
                return response(lane="UI_FRONTEND", criticality="NON_CRITICAL", **changes) if n == 0 else None
            with self.subTest(question=question):
                code, report, _, _ = self.invoke(answer)
                row = report["results"][0]
                self.assertEqual(code, 0)
                self.assertEqual(row["judgment"]["defaulted"], [question])
                self.assertEqual(report["summary"]["defaultedAnswers"]["counts"][question], 1)

    def test_corrections_partial_judgment_append_preserves_refs(self):
        real_append = records.append_event
        def append(local, repo, work, kind, data, **kwargs):
            if kind == "routing-judgment":
                raise records.RecordError("synthetic interrupted append")
            return real_append(local, repo, work, kind, data, **kwargs)
        with patch.object(records, "append_event", side_effect=append):
            code, report, _, calls = self.invoke(lambda n, task: response(scope_conf=.45))
        self.assertEqual((code, report["complete"], len(calls)), (2, False, 1))
        row = report["results"][0]
        self.assertEqual(set(row["recordRefs"]), {"input", "classification"})
        self.assertIsNone(row["derived"])

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
