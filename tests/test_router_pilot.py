"""Offline pilot integration: real Jev/records/routing, injected HTTP only."""
import copy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
import os
import runpy
import sys
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT
from tests.test_jev import Reply, response
from t3compose import config, jsonio, paths, records, routing, schema

SCRIPT = ROOT / "live/router_pilot.py"
REPO = "synthetic/router-pilot"
KEY = "synthetic-pilot-secret-sentinel"
FACTS = {"credentials-or-guardrails", "recovery-or-concurrency",
         "shared-interface", "technical-documentation"}


def digest(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class OfflineRouterPilot(OfflineTestCase):
    def setUp(self):
        self.allow_program("grep")
        self.local = paths.local_dir()
        (self.local / "state").mkdir(parents=True)
        self.listfile = self.local / "state/private-identifiers.txt"
        self.listfile.write_text("private-synthetic-pilot-identifier\n")
        self.cfg = json.loads((ROOT / "docs/examples/agent-routing.example.json").read_text())
        self.keyfile = self.temp / "synthetic-pilot-key.json"
        self.write(self.keyfile, {"apiKey": KEY})
        self.cfg["jev"]["keyFile"] = {"path": str(self.keyfile), "field": "apiKey"}
        self.cfgpath = self.local / "agent-routing.json"
        self.statepath = self.local / "agent-routing-state.json"
        self.write(self.cfgpath, self.cfg)
        self.write(self.statepath, {"activeProviderInstanceId": "agy-synth-alpha",
                                  "quotaObservations": {}, "note": "synthetic"})
        self.enterContext(patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}))
        self.catalog = {"providers": []}
        selections = [self.cfg["master"], self.cfg["easyWorker"], self.cfg["textHelper"]]
        selections += [s for d in self.cfg["modelPreferences"].values() for s in d.values()]
        selections += [s for c in self.cfg["availabilityFallback"]["chains"]
                       for s in [c["from"], *c["to"]]]
        for instance, driver in [("claude-synth-primary", "claudeAgent"),
                                 ("codex-synth-primary", "codex"), ("pi-synth-openrouter", "pi"),
                                 *[("agy-synth-" + x, "antigravity") for x in ("alpha", "beta", "gamma")]]:
            models = {}
            for s in selections:
                if s.get("providerInstanceId") != instance and "providerPool" not in s:
                    continue
                model = models.setdefault(s["model"], {"id": s["model"], "options": []})
                for k, v in s["options"].items():
                    option = next((o for o in model["options"] if o["id"] == k), None)
                    if option is None:
                        option = {"id": k, "values": []}
                        model["options"].append(option)
                    if v not in option["values"]:
                        option["values"].append(v)
            self.catalog["providers"].append({"providerInstanceId": instance, "driverKind": driver,
                "models": list(models.values()), "canRunChildTask": True,
                "canRunCrossProviderChildTask": True, "constraints": []})
        self.catpath = self.temp / "catalog.json"
        self.write(self.catpath, self.catalog)
        self.assertTrue((ROOT / "pilot/router-examples.json").exists(), "synthetic dataset required")
        self.dataset = json.loads((ROOT / "pilot/router-examples.json").read_text())
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
            task = self.dataset["examples"][n]["task"]
            self.assertEqual(body["state"], {"task": task, "context": None})
            self.assertEqual(body["questions"], {q: {"type": "choice", **self.cfg["classification"][q]}
                                                 for q in ("domain", "difficulty")})
            self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
            self.assertEqual(timeout, self.cfg["jev"]["timeoutSeconds"])
            works = records.list_work(self.local, REPO)["works"]
            candidates = [records.load_work(self.local, REPO, w["workId"])["events"]
                          for w in works if w["workId"].endswith("-" + self.dataset["examples"][n]["id"])]
            pending = [events for events in candidates if events and events[-1]["kind"] == "routing-input"]
            self.assertEqual(len(pending), 1)  # New canonical input is durable before HTTP.
            if during:
                during(n)
            if answers is None:
                item = response()
                gold = self.dataset["examples"][n]["expected"]
                item["answers"]["domain"]["choice"] = gold["domain"]
                item["answers"]["difficulty"]["choice"] = (gold["tier"] or "medium").upper()
            else:
                item = answers[n]
            if isinstance(item, Exception):
                raise item
            if isinstance(item, dict):
                item = {**item, "id": "dec-synthetic-" + str(n)}
            return Reply(item if isinstance(item, bytes) else jsonio.canonical_json(item))

        out, err = io.StringIO(), io.StringIO()
        with patch.object(importlib.import_module("t3compose.jev"), "open_http", http), \
                patch.object(sys, "argv", args), redirect_stdout(out), redirect_stderr(err), \
                self.assertRaises(SystemExit) as caught:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        for text in (out.getvalue(), err.getvalue()):
            self.assertNotIn(KEY, text)
            self.assertNotIn(str(self.keyfile), text)
        for path in (self.local / "state/records").rglob("*.json"):
            self.assertNotIn(KEY.encode(), path.read_bytes())
            self.assertNotIn(str(self.keyfile).encode(), path.read_bytes())
        data = json.loads(out.getvalue()) if out.getvalue().startswith("{") else None
        return caught.exception.code, data, calls, err.getvalue()

    def test_shipped_fourteen_and_real_pipeline_provenance_repeat_preserves_history(self):
        entries = self.dataset["examples"]
        self.assertEqual(len(entries), 14)
        self.assertEqual(len({e["id"] for e in entries}), 14)
        self.assertEqual({e["label"] for e in entries}, {"synthetic"})
        self.assertEqual({e["expected"]["domain"] for e in entries},
                         {"SOFTWARE_ENGINEERING", "OTHER", "MIXED_OR_UNCLEAR"})
        self.assertEqual({f for e in entries for f in e["task"]["riskFacts"]}, FACTS)
        self.assertEqual([entries[i]["expected"]["tier"] for i in (0, 1, 2, 3, 4, 6, 11, 12, 13)],
                         ["easy", "easy", "easy", "medium", "medium", "challenging", "medium", "challenging", None])
        self.examples.write_bytes(b" \n" + self.examples.read_bytes() + b"\n")
        before = (self.cfgpath.read_bytes(), self.statepath.read_bytes())
        code, report, calls, err = self.invoke(default_local=True)
        self.assertEqual((code, len(calls), err, report["complete"]), (0, 14, "", True))
        self.assertEqual(report["label"], "synthetic")
        self.assertEqual(report["examplesDigest"], digest(self.examples))
        self.assertEqual(report["summary"]["selected"], 13)
        self.assertEqual(report["summary"]["matchingRoutes"], 13)
        self.assertEqual(report["summary"]["unselected"], {"manager-judgment": 1})
        loaded_cfg = config.load_config(local=self.local)
        for n, (row, entry) in enumerate(zip(report["results"], entries)):
            work = records.load_work(self.local, REPO, row["workId"])
            self.assertEqual(work["work"]["writer"], "manager@" + REPO)
            self.assertIn(report["examplesDigest"], work["work"]["scope"])
            events = work["events"]
            self.assertEqual([e["kind"] for e in events[:2]], ["routing-input", "routing-classification"])
            inp, classified = [e["data"] for e in events[:2]]
            self.assertEqual(inp["task"], entry["task"])
            self.assertEqual(inp["inputDigest"], schema.validate_routing_input({"task": entry["task"], "context": None}))
            self.assertEqual(classified["policyDigest"], loaded_cfg.policy_digest)
            self.assertEqual(row["classification"]["snapshotModel"], "typesafe/synthetic-snapshot")
            self.assertEqual(row["classification"]["responseId"], "dec-synthetic-" + str(n))
            self.assertEqual(row["recordRefs"], {"input": 1, "classification": 2, **({"selection": 3} if len(events) == 3 else {})})
            if row["selection"]:
                selected = events[2]["data"]
                self.assertEqual((selected["role"], selected["round"], selected["reviewScope"]), ("worker", "first", None))
                self.assertEqual((selected["tier"], selected["floorsApplied"]), (entry["expected"]["tier"], entry["expected"]["floors"]))
                for k, want in (("inputRef", 1), ("classificationRef", 2), ("inputDigest", inp["inputDigest"]),
                                ("policyDigest", loaded_cfg.policy_digest), ("configDigest", digest(self.cfgpath)),
                                ("catalogDigest", digest(self.catpath)), ("quotaStateDigest", digest(self.statepath)),
                                ("catalogReadAt", self.read_at)):
                    self.assertEqual(row["selection"][k], want)
                self.assertNotIn("providerInstanceId", row["selection"])
        saved = {p: p.read_bytes() for p in (self.local / "state/records").rglob("*.json")}
        code, again, calls, _ = self.invoke()
        self.assertEqual((code, len(calls)), (0, 14))
        self.assertNotEqual(report["runId"], again["runId"])
        self.assertTrue({r["workId"] for r in report["results"]}.isdisjoint(r["workId"] for r in again["results"]))
        self.assertTrue(all(p.read_bytes() == b for p, b in saved.items()))
        self.assertEqual(len(records.list_work(self.local, REPO)["works"]), 28)
        self.assertEqual(before, (self.cfgpath.read_bytes(), self.statepath.read_bytes()))

    def test_final_tiers_split_cheap_expensive_domain_and_unexpected_selection(self):
        answers = [response() for _ in range(14)]
        for item, entry in zip(answers, self.dataset["examples"]):
            item["answers"]["domain"]["choice"] = entry["expected"]["domain"]
            item["answers"]["difficulty"]["choice"] = (entry["expected"]["tier"] or "medium").upper()
        answers[0]["answers"]["difficulty"]["choice"] = "MEDIUM"  # unnecessary expensive
        answers[11]["answers"]["difficulty"]["choice"] = "EASY"  # mistaken cheap
        answers[6]["answers"]["difficulty"]["choice"] = "EASY"  # floor corrects
        answers[4]["answers"]["domain"]["choice"] = "OTHER"  # documentation mismatch
        answers[13]["answers"]["domain"]["choice"] = "SOFTWARE_ENGINEERING"  # gold cannot block route
        code, report, calls, _ = self.invoke(answers)
        self.assertEqual((code, len(calls)), (0, 14))
        summary = report["summary"]
        for k in ("mistakenCheapRoutes", "unnecessaryExpensiveRoutes", "unexpectedSelections"):
            self.assertEqual(summary[k], 1, k)
        self.assertEqual(summary["domainMismatches"], 2)
        sensitive = report["results"][6]
        self.assertEqual(sensitive["classification"]["answers"]["difficulty"]["choice"], "EASY")
        self.assertEqual((sensitive["selection"]["tier"], sensitive["tierComparison"]), ("challenging", "same"))
        self.assertEqual(report["results"][4]["domainMismatch"], True)
        self.assertEqual(report["results"][13]["unexpectedSelection"], True)

    def test_uncertainty_malformed_and_transport_error_retained_without_retry(self):
        answers = [response() for _ in range(14)]
        answers[0]["answers"]["difficulty"]["confidence"] = .45
        answers[1]["answers"]["domain"]["choice"] = "MIXED_OR_UNCLEAR"
        answers[2]["answers"]["difficulty"]["choice"] = "INSUFFICIENT_CONTEXT"
        answers[3] = b'{'
        answers[4] = TimeoutError(KEY)
        answers[5] = {"model": "typesafe/synthetic-snapshot", "id": "dec-synthetic"}
        answers[11]["answers"]["domain"]["confidence"] = 1
        answers[11]["answers"]["difficulty"]["confidence"] = .45
        code, report, calls, _ = self.invoke(answers)
        self.assertEqual((code, len(calls), report["complete"]), (0, 14, True))
        rows = report["results"]
        self.assertEqual([r["outcome"] for r in rows[:6]],
                         ["retry-with-context", "manager-judgment", "retry-with-context",
                          "manager-judgment", "manager-judgment", "manager-judgment"])
        self.assertEqual(rows[0]["classification"]["answers"]["difficulty"]["confidence"], .45)
        self.assertEqual(rows[11]["classification"]["answers"]["difficulty"]["confidence"], .45)
        self.assertEqual(report["summary"]["unnecessaryExpensiveRoutes"], 0)
        self.assertEqual(report["summary"]["mistakenCheapRoutes"], 1)  # Accepted architecture under-route only.
        for r in [*rows[:6], rows[11]]:
            self.assertIsNone(r["selection"])
            self.assertIsNone(r["tierComparison"])
            self.assertEqual(len(records.load_work(self.local, REPO, r["workId"])["events"]), 2)

    def test_invalid_examples_refused_before_any_records_or_http(self):
        def change(path, value):
            data = copy.deepcopy(self.dataset)
            target = data
            for k in path[:-1]:
                target = target[k]
            target[path[-1]] = value
            return data
        bad = [[], {**self.dataset, "extra": True}, {"examples": []}]
        bad += [change(p, v) for p, v in [(["version"], True), (["label"], "real"),
            (["examples"], self.dataset["examples"][:11]), (["examples"], self.dataset["examples"] * 3),
            (["examples", 1, "id"], self.dataset["examples"][0]["id"]), (["examples", 0, "id"], "../bad"),
            (["examples", 0, "label"], False), (["examples", 0, "expected"], []),
            (["examples", 0, "task", "intent"], "x" * 16384), (["examples", 0, "task", "acceptance"], []),
            (["examples", 0, "task", "riskFacts"], ["unknown-fact"]),
            (["examples", 0, "task", "riskFacts"], ["shared-interface", "shared-interface"]),
            (["examples", 0, "task", "interfaces"], "wrong"),
            (["examples", 0, "task", "scope"], []),
            (["examples", 0, "expected", "domain"], "UNKNOWN"), (["examples", 0, "expected", "tier"], None),
            (["examples", 0, "expected", "tier"], "EASY"),
            (["examples", 0, "expected", "floors"], ["shared-interface", "shared-interface"]),
            (["examples", 13, "expected", "tier"], "medium"), (["examples", 0, "expected", "floors"], "medium"),
            (["examples", 0, "expected", "floors"], ["unknown-fact"]),
            (["examples", 0, "expected", "floors"], ["shared-interface"])]]
        for field in ("id", "label", "task", "expected"):
            data = copy.deepcopy(self.dataset)
            del data["examples"][0][field]
            bad.append(data)
        for section in ("task", "expected"):
            data = copy.deepcopy(self.dataset)
            data["examples"][0][section]["extra"] = True
            bad.append(data)
        data = copy.deepcopy(self.dataset)
        for entry in data["examples"]:
            entry["task"]["riskFacts"] = entry["expected"]["floors"] = []
        bad.append(data)
        for data in bad:
            self.write(self.examples, data)
            with self.subTest(case=bad.index(data)):
                self.assertEqual(self.invoke()[:3], (2, None, []))
                self.assertEqual(records.list_work(self.local, REPO)["works"], [])
        for raw in (b'{', b'{"label":"synthetic","label":"synthetic"}', b' ' * (512 * 1024 + 1)):
            self.examples.write_bytes(raw)
            self.assertEqual(self.invoke()[:3], (2, None, []))

    def test_catalog_and_quota_preflight_help_and_argument_refusals(self):
        for raw in (b'{', b'{}', b'[]'):
            self.catpath.write_bytes(raw)
            self.assertEqual(self.invoke()[:3], (2, None, []))
        self.catpath.unlink()
        self.assertEqual(self.invoke()[:3], (2, None, []))
        self.write(self.catpath, self.catalog)
        for delta in (-901, 60):
            self.read_at = (datetime.now(timezone.utc) + timedelta(seconds=delta)).isoformat().replace("+00:00", "Z")
            self.assertEqual(self.invoke()[:3], (2, None, []))
        self.read_at = "invalid"
        self.assertEqual(self.invoke()[:3], (2, None, []))
        self.read_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.write(self.statepath, {})
        self.assertEqual(self.invoke()[:3], (3, None, []))
        self.write(self.statepath, {"activeProviderInstanceId": "agy-synth-alpha", "note": "synthetic",
                                   "quotaObservations": {"agy-synth-alpha": [{"observedAt": "2099-01-01T00:00:00Z",
                                       "status": "exhausted", "resetAt": None, "evidence": "synthetic"}]}})
        self.assertEqual(self.invoke()[:3], (3, None, []))
        self.assertEqual(records.list_work(self.local, REPO)["works"], [])
        with patch.object(config, "load_config", side_effect=AssertionError("must not load")):
            self.assertEqual(self.invoke(argv=["--help"])[0], 0)
            self.assertEqual(self.invoke(argv=[])[0], 2)
            self.assertEqual(self.invoke(argv=["--unknown", KEY])[0], 2)

    def test_selection_refusals_expiry_and_policy_change_are_not_tier_errors(self):
        for provider in self.catalog["providers"]:
            provider["models"] = []
        self.write(self.catpath, self.catalog)
        code, report, calls, _ = self.invoke()
        self.assertEqual((code, len(calls), report["complete"]), (0, 14, True))
        self.assertEqual(report["summary"]["unselected"], {"route-refused": 13, "manager-judgment": 1})
        self.assertEqual(report["summary"]["mistakenCheapRoutes"], 0)
        self.assertEqual(report["summary"]["unnecessaryExpensiveRoutes"], 0)
        self.assertTrue(all(r["selection"] is None for r in report["results"]))
        def expire(n):
            self.catpath.write_text('{}')
        report = self.invoke(during=expire)[1]
        self.assertEqual(report["results"][0]["reason"], "catalog unavailable or expired")
        self.write(self.catpath, self.catalog)
        with patch.object(routing, "datetime", wraps=datetime) as clock:
            def elapse(n):
                clock.now.return_value = datetime.now(timezone.utc) + timedelta(seconds=901)
            report = self.invoke(during=elapse)[1]
        self.assertEqual(report["results"][0]["reason"], "catalog unavailable or expired")
        self.assertEqual(report["summary"]["mistakenCheapRoutes"], 0)
        self.assertEqual(report["summary"]["unnecessaryExpensiveRoutes"], 0)
        def policy(n):
            self.cfg["classification"]["ruleVersion"] += "-changed"
            self.write(self.cfgpath, self.cfg)
        report = self.invoke(during=policy)[1]
        self.assertEqual(report["results"][0]["reason"], "routing policy changed")

    def test_config_key_and_identifier_failures_are_safe(self):
        saved = self.cfgpath.read_bytes()
        self.cfgpath.write_text('{}')
        self.assertEqual(self.invoke()[:3], (3, None, []))
        self.cfgpath.write_bytes(saved)
        for text in (None, "", "[\n", "Synthetic\n"):
            if text is None:
                self.listfile.unlink()
            else:
                self.listfile.write_text(text)
            code, report, calls, _ = self.invoke()
            self.assertEqual((code, calls, report["complete"]), (2, [], False))
        self.listfile.write_text("private-synthetic-pilot-identifier\n")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            code, report, calls, _ = self.invoke()
            self.assertEqual((code, calls, report["complete"]), (3, [], False))
        del os.environ["OPENROUTER_API_KEY"]
        self.assertEqual(self.invoke()[0], 0)  # Existing synthetic file key, through JV-1.
        self.keyfile.unlink()
        self.assertEqual(self.invoke()[0], 3)

    def test_storage_failure_preserves_partial_input_evidence_and_stops(self):
        append = records.append_event
        def interrupted(local, repo, work, kind, data, **kwargs):
            if kind == "routing-classification":
                raise OSError(KEY + str(self.keyfile))
            return append(local, repo, work, kind, data, **kwargs)
        with patch.object(records, "append_event", side_effect=interrupted):
            code, report, calls, _ = self.invoke()
        self.assertEqual((code, len(calls), report["complete"]), (2, 1, False))
        row = report["results"][0]
        self.assertEqual(row["recordRefs"], {"input": 1})
        self.assertIsNone(row["selection"])
        self.assertEqual([e["kind"] for e in records.load_work(self.local, REPO, row["workId"])["events"]], ["routing-input"])
        self.assertEqual(report["summary"]["unselected"]["not-run"], 13)

    def test_storage_error_after_selection_write_does_not_claim_a_completed_route(self):
        append = records.append_event
        def interrupted(*args, **kwargs):
            event = append(*args, **kwargs)
            if args[3] == "routing-selection":
                raise OSError(KEY + str(self.keyfile))
            return event
        with patch.object(records, "append_event", side_effect=interrupted):
            code, report, calls, _ = self.invoke()
        self.assertEqual((code, len(calls), report["complete"]), (2, 1, False))
        row = report["results"][0]
        self.assertEqual(row["recordRefs"], {"input": 1, "classification": 2, "selection": 3})
        self.assertEqual(row["outcome"], "incomplete")
        self.assertIsNone(row["selection"])
        self.assertEqual(report["summary"]["selected"], 0)
        events = records.load_work(self.local, REPO, row["workId"])["events"]
        self.assertEqual(events[-1]["data"]["tier"], "easy")
