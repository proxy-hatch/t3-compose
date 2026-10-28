import builtins
import contextlib
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import t3compose
from tests.support import OfflineTestCase, ROOT, guarded_helper_command


def _valid_config_dict():
    return json.loads((ROOT / "docs/examples/agent-routing.example.json").read_text(encoding="utf-8"))


def _valid_state_dict():
    return json.loads((ROOT / "docs/examples/agent-routing-state.example.json").read_text(encoding="utf-8"))


def _cfg():
    mod = importlib.import_module("t3compose.config")
    if not hasattr(mod, "ConfigValidationError"):
        sys.modules.pop("t3compose.config", None)
        mod = importlib.import_module("t3compose.config")
    return mod


def _mut(base, fn):
    d = copy.deepcopy(base)
    fn(d)
    return d


class OfflineConfigValidation(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.local = self.temp / ".local"
        self.local.mkdir(exist_ok=True)
        self.config_path = self.local / "agent-routing.json"
        self.state_path = self.local / "agent-routing-state.json"

    def tearDown(self):
        sys.modules.pop("t3compose.config", None)
        if hasattr(t3compose, "config"):
            delattr(t3compose, "config")
        super().tearDown()

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def assert_invalid(self, cfg_data, expected_path, expected_rule):
        cfg_mod = _cfg()
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx:
            cfg_mod.validate_config_data(cfg_data)
        self.assertTrue(
            [e for e in ctx.exception.errors if e["path"] == expected_path and e["rule"] == expected_rule],
            f"Expected ({expected_path!r}, {expected_rule!r}) in {ctx.exception.errors}",
        )
        return ctx.exception

    def test_load_config_and_state_success_and_immutability(self):
        cfg_mod = _cfg()
        self.write_json(self.config_path, _valid_config_dict())
        self.write_json(self.state_path, _valid_state_dict())

        loaded = cfg_mod.load_config(self.config_path)
        self.assertTrue(loaded.digest.startswith("sha256:") and loaded.policy_digest.startswith("sha256:"))
        self.assertEqual((loaded.notices, loaded.data["version"]), ((), 2))
        self.assertEqual(cfg_mod.load_config().digest, loaded.digest)
        self.assertEqual(cfg_mod.load_config(local=self.local).digest, loaded.digest)

        for target, key, val in ((loaded.data, "version", 1), (loaded.data["modelPreferences"]["software-engineering"]["medium"]["options"], "x", "y")):
            with self.assertRaises(TypeError):
                target[key] = val
        self.assertIsInstance(loaded.data["providerPools"]["agy-synth-pool"]["providerInstanceIds"], tuple)
        self.assertIsInstance(loaded.data["availabilityFallback"]["chains"], tuple)

        loaded_state = cfg_mod.load_quota_state(self.state_path, config=loaded)
        self.assertTrue(loaded_state.digest.startswith("sha256:"))
        self.assertEqual(cfg_mod.load_quota_state(config=loaded.data).digest, loaded_state.digest)
        self.assertIsInstance(loaded_state.data["quotaObservations"]["agy-synth-alpha"], tuple)
        for target, key, val in ((loaded_state.data, "note", "m"), (loaded_state.data["quotaObservations"]["agy-synth-alpha"][0], "status", "exhausted")):
            with self.assertRaises(TypeError):
                target[key] = val

    def test_exact_byte_digest_policy_digest_projection_and_no_caching(self):
        cfg_mod = _cfg()
        cfg_data = _valid_config_dict()
        compact_bytes = json.dumps(cfg_data, separators=(",", ":")).encode("utf-8")
        spaced_bytes = (json.dumps(cfg_data, indent=4) + "\n").encode("utf-8")

        self.config_path.write_bytes(compact_bytes)
        loaded_compact = cfg_mod.load_config(self.config_path)
        self.assertEqual(loaded_compact.digest, "sha256:" + hashlib.sha256(compact_bytes).hexdigest())

        self.config_path.write_bytes(spaced_bytes)
        loaded_spaced = cfg_mod.load_config(self.config_path)
        self.assertEqual(loaded_spaced.digest, "sha256:" + hashlib.sha256(spaced_bytes).hexdigest())
        self.assertNotEqual(loaded_compact.digest, loaded_spaced.digest)
        self.assertEqual(loaded_compact.policy_digest, loaded_spaced.policy_digest)

        for label, fn in (
            ("master.model", lambda d: d["master"].__setitem__("model", "claude-opus-other")),
            ("modelPreferences", lambda d: d["modelPreferences"]["non-engineering"]["medium"].__setitem__("model", "other")),
            ("easyWorker", lambda d: d["easyWorker"].__setitem__("model", "gemini-4-flash-lite")),
            ("textHelper", lambda d: d["textHelper"].__setitem__("model", "gemini-4-flash-lite")),
            ("providerPools", lambda d: d["providerPools"]["agy-synth-pool"]["providerInstanceIds"].append("agy-synth-delta")),
            ("roundTiers", lambda d: d["roundTiers"].__setitem__("corrections", "medium")),
            ("jev.timeoutSeconds", lambda d: d["jev"].__setitem__("timeoutSeconds", 60)),
            ("jev.keyFile", lambda d: d["jev"]["keyFile"].update({"path": "/synthetic/other.json", "field": "k"})),
            ("availabilityFallback", lambda d: d["availabilityFallback"].__setitem__("chains", [])),
        ):
            with self.subTest(excluded=label):
                self.write_json(self.config_path, _mut(cfg_data, fn))
                self.assertEqual(cfg_mod.load_config(self.config_path).policy_digest, loaded_compact.policy_digest)

        for label, fn in (
            ("ruleVersion", lambda d: d["classification"].__setitem__("ruleVersion", "2026-10-28-v3")),
            ("domain.instructions", lambda d: d["classification"]["domain"].__setitem__("instructions", "Updated.")),
            ("domain.criteria", lambda d: d["classification"]["domain"]["criteria"].__setitem__("SOFTWARE_ENGINEERING", "Updated.")),
            ("difficulty.instructions", lambda d: d["classification"]["difficulty"].__setitem__("instructions", "Updated.")),
            ("difficulty.criteria", lambda d: d["classification"]["difficulty"]["criteria"].__setitem__("CHALLENGING", "Updated.")),
            ("riskFacts.floor", lambda d: d["riskFacts"]["shared-interface"].__setitem__("floor", "challenging")),
            ("riskFacts.description", lambda d: d["riskFacts"]["shared-interface"].__setitem__("description", "Changed.")),
            ("jev.model", lambda d: d["jev"].__setitem__("model", "~typesafe/jev-pinned")),
            ("jev.minConfidence", lambda d: d["jev"].__setitem__("minConfidence", 0.75)),
            ("jev.maxContextRetries", lambda d: d["jev"].__setitem__("maxContextRetries", 0)),
        ):
            with self.subTest(included=label):
                self.write_json(self.config_path, _mut(cfg_data, fn))
                self.assertNotEqual(cfg_mod.load_config(self.config_path).policy_digest, loaded_compact.policy_digest)

    def test_version_1_refusal_reports_missing_sections_and_nested_requirements(self):
        cfg_mod = _cfg()
        v1 = _valid_config_dict()
        v1["version"] = 1
        for k in ("classification", "riskFacts", "roundTiers", "availabilityFallback"):
            del v1[k]
        v1["jev"] = {"provider": "openrouter", "keyFile": {"path": "/synthetic/k"}}

        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx:
            cfg_mod.validate_config_data(v1)
        for sec in ("classification", "riskFacts", "roundTiers", "availabilityFallback", "jev.model", "jev.minConfidence", "jev.keyFile.field"):
            self.assertIn(sec, ctx.exception.missing_sections)
        self.assertIn("classification", str(ctx.exception))

        v1_partial = _valid_config_dict()
        v1_partial.update({
            "version": 1, "classification": {"domain": {"instructions": "only"}, "difficulty": {}},
            "riskFacts": {}, "roundTiers": {"corrections": "easy"},
            "availabilityFallback": {"roles": ["worker", "reviewer", "feature-lead"]},
        })
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx2:
            cfg_mod.validate_config_data(v1_partial)
        for sec in (
            "classification.ruleVersion", "classification.domain.criteria", "classification.difficulty.instructions",
            "classification.difficulty.criteria", "riskFacts", "roundTiers.correctionsReview",
            "roundTiers.documentReview", "availabilityFallback.manager", "availabilityFallback.chains",
        ):
            self.assertIn(sec, ctx2.exception.missing_sections)

    def test_selection_forms_pool_rules_roles_and_chains(self):
        cfg_mod = _cfg()
        base = _valid_config_dict()
        cfg_direct = _mut(base, lambda d: d["modelPreferences"]["software-engineering"].__setitem__(
            "medium", {"providerInstanceId": "pi-synth-openrouter", "model": "glm-5.3", "options": {}, "roles": ["worker"]}
        ))
        frozen, notices = cfg_mod.validate_config_data(cfg_direct)
        self.assertEqual((frozen["modelPreferences"]["software-engineering"]["medium"]["roles"], notices), (("worker",), ()))

        _, notices = cfg_mod.validate_config_data(_mut(base, lambda d: d["availabilityFallback"].__setitem__("chains", [d["availabilityFallback"]["chains"][0]])))
        self.assertEqual((len(notices), notices[0]["kind"]), (1, "missing-reverse-chain"))

        for roles in (["worker"], ["reviewer"], ["feature-lead"], ["worker", "reviewer"], ["reviewer", "feature-lead"], ["worker", "reviewer", "feature-lead"]):
            cfg_mod.validate_config_data(_mut(base, lambda d: d["modelPreferences"]["non-engineering"]["medium"].__setitem__("roles", list(roles))))

        for bad_roles, path_suffix, rule in (
            ([], "roles", "empty-roles"), (["unknown-role"], "roles[0]", "invalid-role"),
            (["worker", "worker"], "roles[1]", "duplicate-role"), ("worker", "roles", "expected-list"),
        ):
            self.assert_invalid(
                _mut(base, lambda d: d["modelPreferences"]["non-engineering"]["medium"].__setitem__("roles", bad_roles)),
                f"modelPreferences.non-engineering.medium.{path_suffix}", rule,
            )

        for bad_pool_roles in (None, [], ["reviewer"], ["feature-lead"], ["worker", "reviewer"]):
            for path_str, getter in (
                ("modelPreferences.software-engineering.medium", lambda d: d["modelPreferences"]["software-engineering"]["medium"]),
                ("availabilityFallback.chains[0].from", lambda d: d["availabilityFallback"]["chains"][0]["from"]),
                ("availabilityFallback.chains[1].to[0]", lambda d: d["availabilityFallback"]["chains"][1]["to"][0]),
            ):
                d = copy.deepcopy(base)
                if bad_pool_roles is None:
                    del getter(d)["roles"]
                else:
                    getter(d)["roles"] = bad_pool_roles
                self.assert_invalid(d, f"{path_str}.roles", "pool-selection-requires-worker-only-role")

        pool_sel = {"providerPool": "agy-synth-pool", "model": "gemini-4-argon-high", "options": {}, "roles": ["worker"]}
        for path_str, fn in (
            ("modelPreferences.software-engineering.challenging.providerPool", lambda d: d["modelPreferences"]["software-engineering"].__setitem__("challenging", dict(pool_sel))),
            ("availabilityFallback.chains[4].from.providerPool", lambda d: d["availabilityFallback"]["chains"][4].__setitem__("from", dict(pool_sel))),
            ("availabilityFallback.chains[4].to[0].providerPool", lambda d: d["availabilityFallback"]["chains"][4].__setitem__("to", [dict(pool_sel)])),
        ):
            self.assert_invalid(_mut(base, fn), path_str, "pool-selection-not-allowed-at-challenging-tier")

        self.assert_invalid(_mut(base, lambda d: d.__setitem__("master", dict(pool_sel))), "master.providerPool", "expected-direct-selection")

        for slot in ("easyWorker", "textHelper"):
            self.assert_invalid(_mut(base, lambda d: d[slot].__setitem__("roles", ["worker"])), f"{slot}.roles", "roles-not-allowed")
            self.assert_invalid(_mut(base, lambda d: d.__setitem__(slot, {"providerInstanceId": "claude-synth-primary", "model": "m", "options": {}})), f"{slot}.providerInstanceId", "expected-pool-selection")
            self.assert_invalid(_mut(base, lambda d: d[slot].__setitem__("providerPool", "nonexistent-pool")), f"{slot}.providerPool", "unknown-provider-pool")

        for path_prefix, getter in (
            ("master", lambda d: d["master"]),
            ("modelPreferences.software-engineering.challenging", lambda d: d["modelPreferences"]["software-engineering"]["challenging"]),
            ("modelPreferences.non-engineering.medium", lambda d: d["modelPreferences"]["non-engineering"]["medium"]),
            ("modelPreferences.non-engineering.challenging", lambda d: d["modelPreferences"]["non-engineering"]["challenging"]),
            ("availabilityFallback.chains[1].from", lambda d: d["availabilityFallback"]["chains"][1]["from"]),
            ("availabilityFallback.chains[0].to[0]", lambda d: d["availabilityFallback"]["chains"][0]["to"][0]),
        ):
            self.assert_invalid(_mut(base, lambda d: getter(d).__setitem__("providerInstanceId", "agy-synth-beta")), f"{path_prefix}.providerInstanceId", "direct-selection-names-pool-member")

        self.assert_invalid(
            _mut(base, lambda d: d["modelPreferences"]["software-engineering"].__setitem__("medium", {"providerInstanceId": "agy-synth-alpha", "model": "m", "options": {}})),
            "modelPreferences.software-engineering.medium.providerInstanceId", "direct-selection-names-pool-member",
        )
        self.assert_invalid(_mut(base, lambda d: d["master"].__setitem__("providerPool", "agy-synth-pool")), "master", "disjoint-selection-keys")
        self.assert_invalid(_mut(base, lambda d: d["master"].__delitem__("providerInstanceId")), "master", "missing-selection-target")
        self.assert_invalid(_mut(base, lambda d: d["availabilityFallback"]["chains"][0].__delitem__("tier")), "availabilityFallback.chains[0].tier", "missing-key")
        for bad_tier in ("easy", "invalid"):
            self.assert_invalid(_mut(base, lambda d: d["availabilityFallback"]["chains"][0].__setitem__("tier", bad_tier)), "availabilityFallback.chains[0].tier", "invalid-chain-tier")

        d_dup = copy.deepcopy(base)
        dup_chain = copy.deepcopy(d_dup["availabilityFallback"]["chains"][2])
        dup_chain["from"]["roles"] = ["worker"]
        d_dup["availabilityFallback"]["chains"].append(dup_chain)
        self.assert_invalid(d_dup, "availabilityFallback.chains[6].from", "duplicate-chain-source")

        for bad_to in ([], [base["master"], base["master"]]):
            self.assert_invalid(_mut(base, lambda d: d["availabilityFallback"]["chains"][2].__setitem__("to", bad_to)), "availabilityFallback.chains[2].to", "chain-requires-single-target")

        d_same_dir = copy.deepcopy(base)
        same_t = copy.deepcopy(d_same_dir["availabilityFallback"]["chains"][2]["from"])
        same_t["roles"] = ["worker"]
        d_same_dir["availabilityFallback"]["chains"][2]["to"] = [same_t]
        self.assert_invalid(d_same_dir, "availabilityFallback.chains[2].to[0]", "chain-target-equals-source")
        self.assert_invalid(
            _mut(base, lambda d: d["availabilityFallback"]["chains"][0].__setitem__("to", [copy.deepcopy(d["availabilityFallback"]["chains"][0]["from"])])),
            "availabilityFallback.chains[0].to[0]", "chain-target-equals-source",
        )

    def test_boundaries_types_unknown_keys_and_keyfile_never_opened(self):
        cfg_mod = _cfg()
        base = _valid_config_dict()
        self.assert_invalid(_mut(base, lambda d: d.__setitem__("extraTop", 1)), "extraTop", "unknown-key")
        self.assert_invalid(_mut(base, lambda d: d["jev"].__setitem__("extraJev", "x")), "jev.extraJev", "unknown-key")

        for fn, path, rule in (
            (lambda d: d.__setitem__("version", True), "version", "invalid-version"),
            (lambda d: d["jev"].__setitem__("minConfidence", True), "jev.minConfidence", "invalid-min-confidence"),
            (lambda d: d["jev"].__setitem__("maxContextRetries", True), "jev.maxContextRetries", "invalid-max-context-retries"),
            (lambda d: d["jev"].__setitem__("timeoutSeconds", True), "jev.timeoutSeconds", "invalid-timeout-seconds"),
        ):
            self.assert_invalid(_mut(base, fn), path, rule)

        for bad_opt in (1, 1.5, None, ["a"], {"k": "v"}):
            self.assert_invalid(_mut(base, lambda d: d["master"]["options"].__setitem__("bad", bad_opt)), "master.options.bad", "invalid-option-value")

        for k, valid_vals, invalid_vals, rule in (
            ("minConfidence", (0.0001, 0.6, 1, 1.0), (0, 0.0, -0.1, 1.0001, "0.6"), "invalid-min-confidence"),
            ("maxContextRetries", (0, 1), (-1, 2, 1.0, "1"), "invalid-max-context-retries"),
            ("timeoutSeconds", (1, 60, 120), (0, -5, 121, 30.0, "30"), "invalid-timeout-seconds"),
        ):
            for v in valid_vals:
                cfg_mod.validate_config_data(_mut(base, lambda d: d["jev"].__setitem__(k, v)))
            for v in invalid_vals:
                self.assert_invalid(_mut(base, lambda d: d["jev"].__setitem__(k, v)), f"jev.{k}", rule)

        for bad_id in ("UPPER", "snake_case", "trailing-", "-leading", "a--b", "word1"):
            self.assert_invalid(_mut(base, lambda d: d["riskFacts"].__setitem__(bad_id, {"description": "desc", "floor": "medium"})), f"riskFacts.{bad_id}", "invalid-fact-id")

        self.assert_invalid(_mut(base, lambda d: d["riskFacts"]["shared-interface"].__setitem__("floor", "easy")), "riskFacts.shared-interface.floor", "invalid-floor")
        for rev_field in ("correctionsReview", "documentReview"):
            self.assert_invalid(_mut(base, lambda d: d["roundTiers"].__setitem__(rev_field, "easy")), f"roundTiers.{rev_field}", "invalid-reviewer-tier")
        for valid_corr in ("easy", "medium", "challenging"):
            cfg_mod.validate_config_data(_mut(base, lambda d: d["roundTiers"].__setitem__("corrections", valid_corr)))

        for fn, path, rule in (
            (lambda d: d.__setitem__("providerPools", {}), "providerPools", "empty-object"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("selection", "random"), "providerPools.agy-synth-pool.selection", "invalid-pool-selection-mode"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("providerInstanceIds", ["agy-synth-alpha", "agy-synth-alpha"]), "providerPools.agy-synth-pool.providerInstanceIds[1]", "duplicate-instance-id"),
            (lambda d: d["providerPools"].__setitem__("second-pool", {"selection": "exhaust-in-order", "providerInstanceIds": ["agy-synth-alpha"]}), "providerPools.second-pool.providerInstanceIds[0]", "duplicate-pool-member"),
            (lambda d: d["classification"]["domain"]["criteria"].__delitem__("OTHER"), "classification.domain.criteria.OTHER", "missing-key"),
            (lambda d: d["jev"].__setitem__("provider", "other"), "jev.provider", "invalid-jev-provider"),
            (lambda d: d["jev"].__setitem__("uncertainResult", "guess"), "jev.uncertainResult", "invalid-uncertain-result"),
            (lambda d: d["availabilityFallback"].__setitem__("roles", ["worker", "reviewer"]), "availabilityFallback.roles", "invalid-fallback-roles"),
            (lambda d: d["availabilityFallback"].__setitem__("manager", "auto"), "availabilityFallback.manager", "invalid-manager-fallback"),
            (lambda d: d["availabilityFallback"].__setitem__("recordReason", False), "availabilityFallback.recordReason", "expected-true"),
            (lambda d: d["availabilityFallback"].__setitem__("reportReason", False), "availabilityFallback.reportReason", "expected-true"),
        ):
            self.assert_invalid(_mut(base, fn), path, rule)

        secret_key_file = self.temp / "secret-key.json"
        secret_key_file.write_text('{"apiKey": "SUPER-SECRET-TOKEN-999"}', encoding="utf-8")
        self.write_json(self.config_path, _mut(base, lambda d: d["jev"].__setitem__("keyFile", {"path": str(secret_key_file), "field": "apiKey"})))

        real_open, opened_paths = builtins.open, []
        with patch("builtins.open", side_effect=lambda f, *a, **kw: (opened_paths.append(str(Path(f))), real_open(f, *a, **kw))[1]):
            cfg_mod.load_config(self.config_path)
        self.assertEqual(opened_paths, [str(self.config_path)])

        opened_paths.clear()
        with patch("builtins.open", side_effect=lambda f, *a, **kw: (opened_paths.append(str(Path(f))), real_open(f, *a, **kw))[1]):
            exc = self.assert_invalid(_mut(base, lambda d: d["jev"].__setitem__("keyFile", {"path": "relative/secret-path-token", "field": "apiKey"})), "jev.keyFile.path", "expected-absolute-path")
        self.assertEqual(opened_paths, [])
        self.assertNotIn("relative/secret-path-token", str(exc))
        self.assertNotIn("SUPER-SECRET-TOKEN-999", str(exc))

    def test_quota_state_validation_and_sanitized_io_errors(self):
        cfg_mod = _cfg()
        frozen_cfg, _ = cfg_mod.validate_config_data(_valid_config_dict())

        for fn, path, rule in (
            (lambda s: s["quotaObservations"].__setitem__("agy-synth-alpha", {"observedAt": "2026-10-28T00:00:00Z", "status": "available", "resetAt": None, "evidence": "legacy"}), "quotaObservations.agy-synth-alpha", "expected-list"),
            (lambda s: s.__setitem__("activeProviderInstanceId", "unknown-instance"), "activeProviderInstanceId", "unknown-pool-member"),
            (lambda s: s["quotaObservations"].__setitem__("unknown-instance", []), "quotaObservations.unknown-instance", "unknown-pool-member"),
            (lambda s: s["quotaObservations"]["agy-synth-alpha"].__setitem__(0, {"observedAt": "", "status": "unknown", "resetAt": "", "evidence": "  ", "extra": 1}), "quotaObservations.agy-synth-alpha[0].status", "invalid-quota-status"),
            (lambda s: s["quotaObservations"]["agy-synth-alpha"][0].__setitem__("resetAt", ""), "quotaObservations.agy-synth-alpha[0].resetAt", "invalid-reset-at"),
            (lambda s: s["quotaObservations"]["agy-synth-alpha"][0].__setitem__("extra", 1), "quotaObservations.agy-synth-alpha[0].extra", "unknown-key"),
        ):
            with self.assertRaises(cfg_mod.ConfigValidationError) as ctx:
                cfg_mod.validate_quota_state_data(_mut(_valid_state_dict(), fn), config_data=frozen_cfg)
            self.assertIn({"path": path, "rule": rule}, [dict(e) for e in ctx.exception.errors])

        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_miss:
            cfg_mod.load_config(self.temp / "missing-secret-name.json")
        self.assertEqual([dict(e) for e in ctx_miss.exception.errors], [{"path": "config", "rule": "unreadable-file"}])
        self.assertNotIn("missing-secret-name.json", str(ctx_miss.exception))

        self.config_path.write_text("{not-json: SECRET-VALUE-123}", encoding="utf-8")
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_json:
            cfg_mod.load_config(self.config_path)
        self.assertEqual([dict(e) for e in ctx_json.exception.errors], [{"path": "config", "rule": "invalid-json"}])
        self.assertNotIn("SECRET-VALUE-123", str(ctx_json.exception))

        self.config_path.write_text('{"note": "' + ("x" * 17000) + '"}', encoding="utf-8")
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_size:
            cfg_mod.load_config(self.config_path)
        self.assertEqual([dict(e) for e in ctx_size.exception.errors], [{"path": "config", "rule": "size-limit-exceeded"}])

    def test_cli_config_check_and_example_fixtures(self):
        from t3compose import cli
        cfg_mod = _cfg()
        ex_cfg = ROOT / "docs" / "examples" / "agent-routing.example.json"
        ex_state = ROOT / "docs" / "examples" / "agent-routing-state.example.json"
        loaded_ex = cfg_mod.load_config(ex_cfg)
        loaded_ex_state = cfg_mod.load_quota_state(ex_state, config=loaded_ex)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["config", "check", "--config", str(ex_cfg), "--state", str(ex_state)])
        self.assertEqual((code, err.getvalue()), (0, ""))
        payload = json.loads(out.getvalue())
        self.assertEqual((payload["digest"], payload["policyDigest"], payload["stateDigest"]), (loaded_ex.digest, loaded_ex.policy_digest, loaded_ex_state.digest))
        self.assertEqual((payload["ruleVersion"], payload["notices"]), ("2026-10-28-v2", []))
        self.assertEqual(payload["roundTiers"], {"corrections": "easy", "correctionsReview": "medium", "documentReview": "medium"})

        self.write_json(self.config_path, _valid_config_dict())
        out_t, err_t = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_t), contextlib.redirect_stderr(err_t):
            code_t = cli.main(["--text", "--local-dir", str(self.local), "config", "check"])
        self.assertEqual(code_t, 0)
        self.assertIn("policyDigest:", out_t.getvalue())
        self.assertIn("stateDigest: null", out_t.getvalue())

        self.write_json(self.config_path, _mut(_valid_config_dict(), lambda d: (d.__setitem__("version", 1), d.__delitem__("classification"))))
        out_f, err_f = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_f), contextlib.redirect_stderr(err_f):
            code_f = cli.main(["--local-dir", str(self.local), "config", "check"])
        self.assertEqual((code_f, err_f.getvalue()), (3, ""))
        fail_payload = json.loads(out_f.getvalue())
        self.assertFalse(fail_payload["valid"])
        self.assertIn("classification", fail_payload["missingSections"])

        self.write_json(self.config_path, _valid_config_dict())
        self.write_json(self.state_path, _mut(_valid_state_dict(), lambda s: s.__setitem__("activeProviderInstanceId", "not-a-member")))
        out_sf, err_sf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_sf), contextlib.redirect_stderr(err_sf):
            code_sf = cli.main(["--local-dir", str(self.local), "config", "check"])
        self.assertEqual(code_sf, 3)
        self.assertIn({"path": "state.activeProviderInstanceId", "rule": "unknown-pool-member"}, json.loads(out_sf.getvalue())["errors"])

        proc = subprocess.run(
            guarded_helper_command("config", "check", "--config", str(ex_cfg), "--state", str(ex_state)),
            cwd=self.temp, text=True, capture_output=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["policyDigest"], loaded_ex.policy_digest)

    def test_structural_shapes_and_edge_branches(self):
        cfg_mod = _cfg()
        base = _valid_config_dict()
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_root:
            cfg_mod.validate_config_data(["not-a-dict"])
        self.assertEqual(ctx_root.exception.to_payload(), {"valid": False, "errors": [{"path": "$", "rule": "expected-object"}]})
        self.assertEqual(cfg_mod._collect_missing_v2_sections(["not-a-dict"]), ())

        v1_extra = _mut(base, lambda d: (d.__setitem__("version", 1), d.__delitem__("riskFacts"), d.__setitem__("unknownV1Key", True)))
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_v1:
            cfg_mod.validate_config_data(v1_extra)
        self.assertIn("missingSections", ctx_v1.exception.to_payload())
        self.assertIn({"path": "unknownV1Key", "rule": "unknown-key"}, [dict(e) for e in ctx_v1.exception.errors])

        for fn, path, rule in (
            (lambda d: d["master"].__setitem__("options", "bad"), "master.options", "expected-object"),
            (lambda d: d["master"]["options"].__setitem__("   ", "val"), "master.options", "invalid-option-key"),
            (lambda d: d.__setitem__("master", "bad"), "master", "expected-object"),
            (lambda d: d["easyWorker"].__setitem__("providerPool", ""), "easyWorker.providerPool", "invalid-string"),
            (lambda d: d["easyWorker"].__setitem__("model", ""), "easyWorker.model", "invalid-string"),
            (lambda d: d["modelPreferences"]["software-engineering"]["medium"].__setitem__("providerPool", ""), "modelPreferences.software-engineering.medium.providerPool", "invalid-string"),
            (lambda d: d["modelPreferences"]["software-engineering"]["medium"].__setitem__("providerPool", "missing"), "modelPreferences.software-engineering.medium.providerPool", "unknown-provider-pool"),
            (lambda d: d["modelPreferences"]["software-engineering"]["medium"].__setitem__("model", ""), "modelPreferences.software-engineering.medium.model", "invalid-string"),
            (lambda d: d["master"].__setitem__("providerInstanceId", ""), "master.providerInstanceId", "invalid-string"),
            (lambda d: d["master"].__setitem__("model", ""), "master.model", "invalid-string"),
            (lambda d: d.__setitem__("providerPools", "bad"), "providerPools", "expected-object"),
            (lambda d: d["providerPools"].__setitem__("  ", {"selection": "exhaust-in-order", "providerInstanceIds": ["x"]}), "providerPools.  ", "invalid-pool-name"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("providerInstanceIds", "bad"), "providerPools.agy-synth-pool.providerInstanceIds", "expected-list"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("providerInstanceIds", []), "providerPools.agy-synth-pool.providerInstanceIds", "empty-list"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("providerInstanceIds", [""]), "providerPools.agy-synth-pool.providerInstanceIds[0]", "invalid-string"),
            (lambda d: d.__setitem__("availabilityFallback", "bad"), "availabilityFallback", "expected-object"),
            (lambda d: d["availabilityFallback"].__setitem__("roles", "bad"), "availabilityFallback.roles", "expected-list"),
            (lambda d: d.__setitem__("modelPreferences", "bad"), "modelPreferences", "expected-object"),
            (lambda d: d["modelPreferences"].__delitem__("non-engineering"), "modelPreferences.non-engineering", "missing-key"),
            (lambda d: d["modelPreferences"].__setitem__("non-engineering", "bad"), "modelPreferences.non-engineering", "expected-object"),
            (lambda d: d.__setitem__("classification", "bad"), "classification", "expected-object"),
            (lambda d: d["classification"].__setitem__("ruleVersion", ""), "classification.ruleVersion", "invalid-string"),
            (lambda d: d["classification"].__delitem__("domain"), "classification.domain", "missing-key"),
            (lambda d: d["classification"].__setitem__("domain", "bad"), "classification.domain", "expected-object"),
            (lambda d: d["classification"]["domain"].__setitem__("instructions", ""), "classification.domain.instructions", "invalid-string"),
            (lambda d: d["classification"]["domain"].__setitem__("criteria", "bad"), "classification.domain.criteria", "expected-object"),
            (lambda d: d["classification"]["domain"]["criteria"].__setitem__("OTHER", ""), "classification.domain.criteria.OTHER", "invalid-string"),
            (lambda d: d.__setitem__("riskFacts", "bad"), "riskFacts", "expected-object"),
            (lambda d: d["riskFacts"].__setitem__("shared-interface", "bad"), "riskFacts.shared-interface", "expected-object"),
            (lambda d: d["riskFacts"]["shared-interface"].__setitem__("description", ""), "riskFacts.shared-interface.description", "invalid-string"),
            (lambda d: d.__setitem__("roundTiers", "bad"), "roundTiers", "expected-object"),
            (lambda d: d["roundTiers"].__setitem__("corrections", "invalid"), "roundTiers.corrections", "invalid-tier"),
            (lambda d: d.__setitem__("jev", "bad"), "jev", "expected-object"),
            (lambda d: d["jev"].__setitem__("model", ""), "jev.model", "invalid-string"),
            (lambda d: d["jev"].__setitem__("keyFile", "bad"), "jev.keyFile", "expected-object"),
            (lambda d: d["jev"]["keyFile"].__setitem__("field", ""), "jev.keyFile.field", "invalid-string"),
            (lambda d: d["availabilityFallback"].__setitem__("chains", "bad"), "availabilityFallback.chains", "expected-list"),
            (lambda d: d["availabilityFallback"].__setitem__("chains", ["bad"]), "availabilityFallback.chains[0]", "expected-object"),
            (lambda d: d["availabilityFallback"]["chains"][0].__setitem__("to", "bad"), "availabilityFallback.chains[0].to", "expected-list"),
        ):
            self.assert_invalid(_mut(base, fn), path, rule)

        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_s_root:
            cfg_mod.validate_quota_state_data(["bad"])
        self.assertEqual([dict(e) for e in ctx_s_root.exception.errors], [{"path": "$", "rule": "expected-object"}])

        for fn, path, rule in (
            (lambda s: s.__setitem__("activeProviderInstanceId", ""), "activeProviderInstanceId", "invalid-string"),
            (lambda s: s.__setitem__("note", 123), "note", "invalid-string"),
            (lambda s: s.__setitem__("quotaObservations", "bad"), "quotaObservations", "expected-object"),
            (lambda s: s["quotaObservations"].__setitem__("  ", []), "quotaObservations.  ", "invalid-instance-id"),
            (lambda s: s["quotaObservations"].__setitem__("agy-synth-alpha", ["bad"]), "quotaObservations.agy-synth-alpha[0]", "expected-object"),
        ):
            with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_s:
                cfg_mod.validate_quota_state_data(_mut(_valid_state_dict(), fn))
            self.assertIn({"path": path, "rule": rule}, [dict(e) for e in ctx_s.exception.errors])

        self.state_path.write_text('["not-an-object"]', encoding="utf-8")
        with self.assertRaises(cfg_mod.ConfigValidationError) as ctx_ls:
            cfg_mod.load_quota_state(self.state_path)
        self.assertEqual([dict(e) for e in ctx_ls.exception.errors], [{"path": "state", "rule": "expected-object"}])
