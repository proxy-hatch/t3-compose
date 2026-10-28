"""Offline entry-point and transport checks; every credential is synthetic."""
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import runpy
import ssl
import sys
import traceback
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, Mock
from urllib.error import HTTPError, URLError

from tests.support import OfflineTestCase, ROOT
from t3compose import config, records, jsonio, schema

KEY = "synthetic-jev-secret-sentinel"
REPO = "synthetic/jev-tests"


def response():
    answers = {}
    for name, options, choice in (
        ("domain", config.DOMAIN_CRITERIA_KEYS, "SOFTWARE_ENGINEERING"),
        ("difficulty", config.DIFFICULTY_CRITERIA_KEYS, "MEDIUM"),
    ):
        answers[name] = {"type": "choice", "choice": choice, "confidence": .8,
                         "probabilities": {k: 1 / len(options) for k in options}}
    return {"model": "typesafe/synthetic-snapshot", "id": "dec-synthetic",
            "answers": answers, "usage": {"cost": .000024}}


class Reply(io.BytesIO):
    status = 200

    def read(self, size=-1):
        self.read_size = size
        return super().read(size)


class OfflineJev(OfflineTestCase):
    def setUp(self):
        self.allow_program("grep")
        self.local = self.temp / "local"
        (self.local / "state").mkdir(parents=True)
        self.listfile = self.local / "state/private-identifiers.txt"
        self.listfile.write_text("private-synthetic-identifier\n")
        self.cfg = json.loads((ROOT / "docs/examples/agent-routing.example.json").read_text())
        self.keyfile = self.temp / "synthetic-key.json"
        self.keyfile.write_text(json.dumps({"apiKey": KEY}))
        self.cfg["jev"]["keyFile"] = {"path": str(self.keyfile), "field": "apiKey"}
        self.save_config()
        self.enterContext(patch.dict(os.environ, {"OPENROUTER_API_KEY": KEY}))
        self.task = {"intent": "Implement synthetic parser", "acceptance": ["Reject bad tokens"],
                     "interfaces": ["parse(text)"], "scope": ["parser.py"],
                     "riskFacts": ["shared-interface"], "dependencies": []}
        self.input = self.temp / "task.json"
        self.extra = self.temp / "extra.json"
        self.counter = 0
        self.new_work()

    def save_config(self):
        (self.local / "agent-routing.json").write_bytes(jsonio.canonical_json(self.cfg))

    def new_work(self, *, lead=False):
        self.counter += 1
        self.work = "JEV-" + str(self.counter)
        writer = ("lead:" + self.work + "@" if lead else "manager@") + REPO
        value = {"workId": self.work, "repository": REPO, "linearIssue": None,
                 "targetBranch": "main", "reviewBase": "1" * 40, "scope": "Synthetic test",
                 "designRefs": [], "ownedPaths": ["parser.py"], "dependencies": [],
                 "acceptance": [{"id": "AC-1", "text": "Synthetic classification"}],
                 "requiredChecks": [{"id": "TEST", "command": "synthetic", "kind": "fast"}],
                 "writer": writer}
        records.create_work(self.local, REPO, self.work, value, writer=writer)

    def events(self):
        return records.load_work(self.local, REPO, self.work)["events"]

    def invoke(self, reply=None, *, context=None, fault=None, raw=None, during=None,
               raw_input=None, raw_context=None):
        self.input.write_bytes(raw_input if raw_input is not None else json.dumps(self.task).encode())
        argv = [str(ROOT / "bin/t3c"), "--local-dir", str(self.local), "route", "classify",
                "--repo", REPO, "--work", self.work, "--input", str(self.input)]
        if context is not None or raw_context is not None:
            self.extra.write_bytes(raw_context if raw_context is not None else json.dumps(context).encode())
            argv += ["--context", str(self.extra)]
        jev = importlib.import_module("t3compose.jev")
        stream = Reply(raw if raw is not None else json.dumps(reply or response()).encode())
        calls = []

        def open_fake(request, *, timeout):
            calls.append(request)
            self.assertEqual(timeout, self.cfg["jev"]["timeoutSeconds"])
            self.assertEqual(request.full_url, "https://openrouter.ai/api/alpha/decisions")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + KEY)
            self.assertNotIn(KEY.encode(), request.data)
            self.assertEqual(self.events()[-1]["kind"], "routing-input")
            if during:
                during()
            if fault:
                raise fault
            return stream

        out, err = io.StringIO(), io.StringIO()
        with patch.object(jev, "open_http", open_fake), patch.object(sys, "argv", argv), \
                redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            runpy.run_path(str(ROOT / "bin/t3c"), run_name="__main__")
        for text in (out.getvalue(), err.getvalue()):
            self.assertNotIn(KEY, text)
            self.assertNotIn(str(self.keyfile), text)
        for path in (self.local / "state/records").rglob("*.json"):
            self.assertNotIn(KEY.encode(), path.read_bytes())
            self.assertNotIn(str(self.keyfile).encode(), path.read_bytes())
        if calls and not fault:
            self.assertTrue(stream.closed)
            self.assertEqual(stream.read_size, 256 * 1024 + 1)
        data = json.loads(out.getvalue()) if out.getvalue() else None
        return caught.exception.code, data, calls, err.getvalue()

    def test_success_records_request_and_canonical_provenance(self):
        code, result, calls, err = self.invoke()
        self.assertEqual((code, err, len(calls)), (0, "", 1))
        initial, classified = self.events()
        data = classified["data"]
        loaded = config.load_config(local=self.local)
        self.assertEqual(result, {**data, "classificationRef": classified["seq"]})
        self.assertEqual(data["inputRef"], initial["seq"])
        self.assertEqual(data["inputDigest"], schema.validate_routing_input({"task": self.task, "context": None}))
        self.assertEqual(data["policyDigest"], loaded.policy_digest)
        self.assertEqual(data["snapshotModel"], response()["model"])
        self.assertEqual(data["responseId"], response()["id"])
        self.assertEqual(data["answers"], response()["answers"])
        self.assertEqual(data["criteriaDigest"], "sha256:" + hashlib.sha256(jsonio.canonical_json(data["criteria"])).hexdigest())
        body = json.loads(calls[0].data)
        self.assertEqual(set(body), {"model", "state", "questions"})
        self.assertEqual(body["state"], {"task": self.task, "context": None})
        for name in ("domain", "difficulty"):
            question = self.cfg["classification"][name]
            self.assertEqual(body["questions"][name], {"type": "choice", **question})
            self.assertEqual(data["criteria"][name], {**question["criteria"], "instructions": question["instructions"]})

    def test_input_validation_and_screen_block_before_persistence(self):
        original = copy.deepcopy(self.task)
        cases = [{**original, "unknown": "boilerplate"}]
        cases += [{**original, k: v} for k, v in (
            ("intent", ""), ("acceptance", []), ("acceptance", [{"id": "A"}]),
            ("interfaces", "wrong"), ("scope", []), ("riskFacts", ["unknown-fact"]),
            ("dependencies", [True]), ("intent", "x" * 16384),
            ("intent", "private-synthetic-identifier"), ("intent", KEY))]
        for task in cases:
            with self.subTest(task_field=list(task)[0]):
                self.task = task
                code, data, calls, _ = self.invoke()
                self.assertEqual((code, data, calls, self.events()), (2, None, [], []))
        self.task = original
        for contents in (None, "", "[\n"):
            with self.subTest(identifier_list=contents):
                if contents is None:
                    self.listfile.unlink()
                else:
                    self.listfile.write_text(contents)
                self.assertEqual(self.invoke()[:3], (2, None, []))

    def test_context_shape_and_combined_limit(self):
        for raw in (b'null', b'{', b'[]', b'{"a":1,"a":2}', b' ' * 16385):
            with self.subTest(raw_context=raw[:16]):
                self.assertEqual(self.invoke(raw_context=raw)[:3], (2, None, []))
        for context in ({}, [], {"more": "x" * 16300}, {"priorClassificationRef": True},
                        {"priorClassificationRef": 2}, {"more": "private-synthetic-identifier"}):
            with self.subTest(context_type=type(context).__name__):
                self.assertEqual(self.invoke(context=context)[0], 2)
                self.assertEqual(self.events(), [])

    def test_input_bytes_exact_limit_unicode_and_invalid_configuration(self):
        for raw in (b'{', b'{"intent":1,"intent":2}', b'"\xff"', b'"\\ud800"', b'NaN',
                    b' ' * 16385, b'[]'):
            self.assertEqual(self.invoke(raw_input=raw)[:3], (2, None, []))
        self.task["intent"] = "Synthetic café parser"
        self.assertEqual(self.invoke()[0], 0)
        self.new_work()
        original = copy.deepcopy(self.task)
        size = len(jsonio.canonical_json({"task": self.task, "context": None}))
        self.task["intent"] += "x" * (16384 - size)
        self.assertEqual(self.invoke(raw_input=jsonio.canonical_json(self.task))[0], 0)
        self.new_work()
        self.task["intent"] += "x"
        self.assertEqual(self.invoke(raw_input=jsonio.canonical_json(self.task))[0], 2)
        self.task = original
        for field, value in (("minConfidence", True), ("maxContextRetries", 2), ("timeoutSeconds", 0)):
            saved = self.cfg["jev"][field]
            self.cfg["jev"][field] = value
            self.save_config()
            self.assertEqual(self.invoke()[:3], (3, None, []))
            self.cfg["jev"][field] = saved
        self.cfg[KEY] = "invalid config key must not enter diagnostics"
        self.save_config()
        self.assertEqual(self.invoke()[:3], (3, None, []))

    def test_key_precedence_file_and_invalid_setup(self):
        for invalid in ("", "bad\nheader"):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": invalid}):
                self.assertEqual(self.invoke()[:3], (3, None, []))
        self.keyfile.unlink()
        self.assertEqual(self.invoke()[0], 0)  # Environment takes precedence.
        del os.environ["OPENROUTER_API_KEY"]
        self.new_work()
        self.assertEqual(self.invoke()[:3], (3, None, []))
        self.keyfile.write_text(json.dumps({"apiKey": KEY}))
        self.assertEqual(self.invoke()[0], 0)
        for value in (None, "", True, "bad\nheader", "x" * 4097, "é"):
            with self.subTest(key_type=type(value).__name__):
                self.new_work()
                self.keyfile.write_text(json.dumps({"apiKey": value}))
                self.assertEqual(self.invoke()[:3], (3, None, []))
        for raw in ('{"apiKey":"a","apiKey":"b"}', '[]', 'x' * 16385):
            self.keyfile.write_text(raw)
            self.assertEqual(self.invoke()[0], 3)
        self.keyfile.unlink()
        os.mkfifo(self.keyfile)
        self.assertEqual(self.invoke()[0], 3)

    def test_corrections_metadata_preflight_precedes_records_and_http(self):
        original = copy.deepcopy(self.cfg)
        for path in (("classification", "ruleVersion"),
                     ("classification", "domain", "instructions"),
                     ("classification", "difficulty", "criteria", "EASY"),
                     ("jev", "model")):
            with self.subTest(config_field=".".join(path)):
                self.new_work()
                self.cfg = copy.deepcopy(original)
                target = self.cfg
                for field in path[:-1]:
                    target = target[field]
                target[path[-1]] = "synthetic\x00metadata"
                self.save_config()
                config.load_config(local=self.local)
                self.assertEqual(self.invoke()[:3], (3, None, []))
                self.assertEqual(self.events(), [])

    def test_corrections_redirect_dispatch_closes_unparsed_locations(self):
        from urllib import request as transport
        jev = importlib.import_module("t3compose.jev")
        real_build = transport.build_opener
        for code in (301, 302, 303, 307, 308):
            for location in ("https://synthetic.invalid/next", "https://["):
                with self.subTest(status=code, location=location):
                    stream = io.BytesIO(KEY.encode())
                    calls = []

                    def build(*handlers):
                        opener = real_build(*handlers)

                        def synthetic_open(request, *, timeout):
                            calls.append(request)
                            return opener.error("http", request, stream, code, KEY,
                                                {"location": location})

                        return Mock(open=synthetic_open)

                    try:
                        with patch.object(transport, "build_opener", side_effect=build):
                            with self.assertRaises(ValueError) as caught:
                                jev._request({"state": "synthetic"}, KEY, 1)
                        self.assertNotIn(KEY, "".join(traceback.format_exception(caught.exception)))
                        self.assertEqual(len(calls), 1)
                        self.assertTrue(stream.closed)
                    finally:
                        stream.close()

    def test_invalid_answers_matrix(self):
        mutations = [("type", "text"), ("choice", "UNKNOWN"), ("confidence", True),
                     ("confidence", -1), ("confidence", 1.1), ("confidence", "0.9"),
                     ("confidence", float("nan")), ("confidence", float("inf"))]
        for name in ("domain", "difficulty"):
            for field, value in mutations:
                with self.subTest(answer=name, field=field, value=value):
                    self.new_work()
                    body = response()
                    body["answers"][name][field] = value
                    self.assertEqual(self.invoke(body)[1]["outcome"], "manager-judgment")
            for value in (True, -.1, 1.1, float("nan"), float("inf"), .8):
                self.new_work()
                body = response()
                probabilities = body["answers"][name]["probabilities"]
                probabilities[next(iter(probabilities))] = value
                self.assertEqual(self.invoke(body)[1]["outcome"], "manager-judgment")
            for change in ("missing", "extra", "missing-answer", "missing-field", "extra-field"):
                self.new_work()
                body = response()
                answer = body["answers"][name]
                if change == "missing":
                    answer["probabilities"].pop(next(iter(answer["probabilities"])))
                elif change == "extra":
                    answer["probabilities"]["EXTRA"] = 0
                elif change == "missing-answer":
                    del body["answers"][name]
                elif change == "missing-field":
                    del answer["confidence"]
                else:
                    answer["extra"] = "untrusted"
                self.assertEqual(self.invoke(body)[0], 4)

    def test_confidence_and_outcome_policy(self):
        for domain, difficulty, confidence, retries, outcome in (
            ("SOFTWARE_ENGINEERING", "MEDIUM", .6, 1, "accepted"),
            ("OTHER", "EASY", .8, 1, "accepted"),
            ("MIXED_OR_UNCLEAR", "MEDIUM", .1, 1, "manager-judgment"),
            ("SOFTWARE_ENGINEERING", "INSUFFICIENT_CONTEXT", .9, 1, "retry-with-context"),
            ("SOFTWARE_ENGINEERING", "MEDIUM", .59, 1, "retry-with-context"),
            ("SOFTWARE_ENGINEERING", "MEDIUM", .59, 0, "manager-judgment"),
        ):
            with self.subTest(outcome=outcome, confidence=confidence):
                self.new_work()
                self.cfg["jev"]["maxContextRetries"] = retries
                self.save_config()
                body = response()
                body["answers"]["domain"].update(choice=domain, confidence=confidence)
                body["answers"]["difficulty"]["choice"] = difficulty
                code, data, _, _ = self.invoke(body)
                self.assertEqual((code, data["outcome"]), (0 if outcome == "accepted" else 4, outcome))
                self.assertEqual(data["inputRef"], 1)
                self.assertEqual(data["policyDigest"], config.load_config(local=self.local).policy_digest)

    def test_probability_sum_edges_and_unicode_service_metadata(self):
        for values, expected in (((.49, .5, 0), 0), ((.5, .5, .01), 0),
                                 ((.489, .5, 0), 4), ((.5, .5, .011), 4)):
            self.new_work()
            body = response()
            body["answers"]["domain"]["probabilities"] = dict(zip(config.DOMAIN_CRITERIA_KEYS, values))
            body["usage"] = {"unrelated": "中文 café"}
            self.assertEqual(self.invoke(body)[0], expected)

    def test_retry_budget_survives_invocations_and_old_reference(self):
        low = response()
        low["answers"]["difficulty"]["confidence"] = .2
        first = self.invoke(low)[1]
        context = {"priorClassificationRef": first["classificationRef"], "details": "Pure local parser"}
        second = self.invoke(low, context=context)[1]
        self.assertEqual(second["outcome"], "manager-judgment")
        self.assertNotEqual(first["inputDigest"], second["inputDigest"])
        for extra in (None, context, {**context, "details": "Changed context"},
                      {**context, "priorClassificationRef": second["classificationRef"]}):
            with self.subTest(context=extra):
                code, data, calls, _ = self.invoke(context=extra)
                self.assertEqual((code, data["outcome"], calls), (4, "manager-judgment", []))
                self.assertEqual(len(self.events()), 4)
        self.task["intent"] += " with revised requirements"
        self.assertEqual(self.invoke()[0], 0)
        self.cfg["classification"]["ruleVersion"] += "-next"
        self.save_config()
        self.assertEqual(self.invoke()[0], 0)

    def test_retry_requires_matching_latest_eligible_result(self):
        low = response()
        low["answers"]["domain"]["confidence"] = .1
        first = self.invoke(low)[1]
        self.assertEqual(self.invoke()[2], [])
        for context in ({"details": "Missing ref"}, {"priorClassificationRef": 1, "details": "Wrong kind"},
                        {"priorClassificationRef": 2, "details": ""}):
            self.assertEqual(self.invoke(context=context)[2], [])
        context = {"priorClassificationRef": first["classificationRef"], "details": "Pure function"}
        self.assertEqual(self.invoke(context=context)[0], 0)
        self.new_work()
        self.assertEqual(self.invoke(context=context)[0], 2)
        self.invoke()
        self.assertEqual(self.invoke(context=context)[2], [])

    def test_transport_and_parse_failures_record_judgment(self):
        faults = [TimeoutError(KEY), URLError(KEY)]
        faults += [HTTPError("https://synthetic.invalid", n, KEY, {}, io.BytesIO(KEY.encode()))
                   for n in (301, 302, 303, 307, 308, 400, 401, 429, 500, 503)]
        for fault in faults:
            with self.subTest(fault=type(fault).__name__):
                self.new_work()
                code, data, calls, _ = self.invoke(fault=fault)
                self.assertEqual((code, data["outcome"], len(calls)), (4, "manager-judgment", 1))
                self.assertEqual(len(self.events()), 2)
        for raw in (b"x" * (256 * 1024 + 1), b"{", b'{}{}', b'{"id":1,"id":2}',
                    b'"\\ud800"', b'"\xff"', b'{"x":1e999}', b'[]', b'null'):
            self.new_work()
            self.assertEqual(self.invoke(raw=raw)[0], 4)

    def test_secret_echo_and_safe_snapshot_fields(self):
        for field in ("id", "model", "usage"):
            for value in (KEY, "prefix-" + KEY + "-suffix", "", None, {"unknown": KEY}, "bad\nline"):
                if field == "usage" and KEY not in str(value):
                    continue
                with self.subTest(field=field):
                    self.new_work()
                    body = response()
                    body[field] = value
                    code, data, _, _ = self.invoke(body)
                    self.assertEqual((code, data["outcome"]), (4, "manager-judgment"))
                    self.assertEqual(data["answers"], {"unknown": "invalid Jev response"})
        self.new_work()
        self.cfg["classification"]["domain"]["instructions"] = KEY
        self.save_config()
        self.assertEqual(self.invoke()[:3], (2, None, []))

    def test_policy_changes_during_request_and_record_failures(self):
        before = config.load_config(local=self.local).policy_digest

        def change():
            self.cfg["jev"]["minConfidence"] = .95
            self.save_config()

        data = self.invoke(during=change)[1]
        self.assertEqual(data["policyDigest"], before)
        self.assertNotEqual(before, config.load_config(local=self.local).policy_digest)
        for seam in ("load_work", "record_routing_input", "append_event"):
            self.new_work()
            with patch.object(records, seam, side_effect=records.RecordError(KEY)):
                code, data, _, err = self.invoke()
            self.assertEqual((code, data), (2, None))
            self.assertNotIn(KEY, err)

    def test_current_writer_and_unresolved_input_are_preserved(self):
        self.new_work(lead=True)
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual({e["writer"] for e in self.events()}, {"lead:" + self.work + "@" + REPO})
        self.new_work()
        records.record_routing_input(self.local, REPO, self.work, task=self.task, context=None,
                                     policy_digest=config.load_config(local=self.local).policy_digest,
                                     writer="manager@" + REPO)
        self.assertEqual(self.invoke()[:3], (2, None, []))
        self.assertEqual(len(self.events()), 1)

    def test_key_path_and_rule_version_cannot_enter_records_or_request(self):
        original = self.task["intent"]
        for value in (KEY, str(self.keyfile)):
            self.task["intent"] = value
            self.assertEqual(self.invoke()[:3], (2, None, []))
        self.task["intent"] = original
        for field in ("ruleVersion", "instructions"):
            target = self.cfg["classification"] if field == "ruleVersion" else self.cfg["classification"]["domain"]
            target[field] = KEY
            self.save_config()
            self.assertEqual(self.invoke()[:3], (2, None, []))
            target[field] = "Synthetic rules"
        self.save_config()
        body = response()
        body["usage"] = {"echo": str(self.keyfile)}
        self.assertEqual(self.invoke(body)[0], 4)

    def test_transport_cleanup_failure_never_exposes_exception_text(self):
        jev = importlib.import_module("t3compose.jev")
        error = HTTPError("https://synthetic.invalid", 401, KEY, {}, io.BytesIO())
        error.close = Mock(side_effect=RuntimeError(KEY))
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(jev.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(OSError) as caught:
                jev.open_http(Mock(), timeout=1)
        self.assertNotIn(KEY, str(caught.exception) + repr(caught.exception))

    def test_opener_tls_redirects_and_safe_exception_text(self):
        jev = importlib.import_module("t3compose.jev")
        opener = Mock()
        with patch.object(jev.urllib.request, "build_opener", return_value=opener) as build:
            jev.open_http(Mock(), timeout=17)
        handlers = build.call_args.args
        https = next(h for h in handlers if isinstance(h, jev.urllib.request.HTTPSHandler))
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)
        redirect = next(h for h in handlers if isinstance(h, jev.NoRedirect))
        self.assertIsNone(redirect.redirect_request(Mock(), None, 302, KEY, {}, "https://synthetic.invalid"))
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 17})
        with patch.object(jev.urllib.request, "build_opener", side_effect=RuntimeError(KEY)):
            try:
                jev.open_http(Mock(), timeout=17)
            except Exception as error:
                for rendered in (str(error), repr(error), traceback.format_exc()):
                    self.assertNotIn(KEY, rendered)

    def test_import_and_registration_do_no_io(self):
        from t3compose import cli
        jev = importlib.import_module("t3compose.jev")
        with patch("builtins.open", side_effect=AssertionError("I/O")), \
                patch.object(config, "load_config", side_effect=AssertionError("config")), \
                patch.object(records, "load_work", side_effect=AssertionError("records")):
            importlib.reload(jev)
            self.assertIn(("route", "classify"), cli.command_names())

    def test_live_script_offline_one_call_and_no_duplicate_work(self):
        jev = importlib.import_module("t3compose.jev")
        script = ROOT / "live/jev_contract.py"
        for _ in range(2):
            out, err = io.StringIO(), io.StringIO()
            with patch.object(jev, "open_http", return_value=Reply(json.dumps(response()).encode())) as http, \
                    patch.object(sys, "argv", [str(script), "--local-dir", str(self.local)]), \
                    redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as caught:
                runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(caught.exception.code, 0)
            self.assertEqual(http.call_count, 1)
            self.assertIn("LIVE", out.getvalue())
            self.assertIn("synthetic", out.getvalue())
            self.assertNotIn(KEY, out.getvalue() + err.getvalue())
        listed = records.list_work(self.local, "synthetic/jev-contract")
        self.assertEqual(len(listed["works"]), 2)
        for work in listed["works"]:
            events = records.load_work(self.local, "synthetic/jev-contract", work["workId"])["events"]
            self.assertEqual([e["kind"] for e in events], ["routing-input", "routing-classification"])
