import builtins
import copy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from tests.support import OfflineTestCase, ROOT, guarded_helper_command


def _old_config_dict():
    return json.loads((ROOT / "docs/examples/agent-routing.example.json").read_text(encoding="utf-8"))


def _matrix_config_dict():
    data = json.loads((ROOT / "docs/examples/agent-routing.v3.example.json").read_text())
    # Exercise role-block validation independently of the example's empty list.
    data["roleBlocks"] = [{"driverKind": "antigravity", "role": "worker",
                           "reason": "Synthetic probe failed.", "evidence": "Synthetic evidence."}]
    return data


def _valid_state_dict():
    return json.loads((ROOT / "docs/examples/agent-routing-state.example.json").read_text(encoding="utf-8"))


def _cfg():
    return importlib.import_module("t3compose.config")


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

    def test_v3_only_versions_and_migration(self):
        cfg = _cfg()
        migration = "Migrate routing configuration to version 3; see docs/configuration.md."
        for version in (1, 2, True, False, "3", "2", None, 4, 3.0, [], {}):
            raw = _mut(_old_config_dict(), lambda d: d.__setitem__("version", version))
            with self.subTest(version=version):
                exc = self.assert_invalid(raw, "version", "expected-version-3")
                self.assertEqual(exc.to_payload()["errors"],
                                 [{"path": "version", "rule": "expected-version-3"}])
                self.assertEqual(exc.to_payload()["migration"], migration)
                self.assertIn(migration, str(exc))
                self.write_json(self.config_path, raw)
                for loader in (cfg.load_config, cfg.load_matrix_config):
                    with self.assertRaises(cfg.ConfigValidationError) as ctx:
                        loader(self.config_path)
                    self.assertEqual(ctx.exception.to_payload(), exc.to_payload())
        self.write_json(self.state_path, _valid_state_dict())
        self.write_json(self.config_path, _old_config_dict())
        proc = subprocess.run(guarded_helper_command("--local-dir", str(self.local), "config", "check"),
                              text=True, capture_output=True)
        self.assertEqual((proc.returncode, proc.stderr), (3, ""))
        self.assertEqual(json.loads(proc.stdout), {"valid": False, "errors":
                         [{"path": "version", "rule": "expected-version-3"}], "migration": migration})

    def test_missing_version_and_cli_invalid_version_shapes(self):
        import contextlib
        import io
        from t3compose import cli
        cfg = _cfg()
        self.write_json(self.state_path, _valid_state_dict())
        for version in (True, False, "3", 3.0, 99, None, [], {}):
            data = _matrix_config_dict()
            if version is None:
                del data["version"]
            else:
                data["version"] = version
            with self.subTest(version=version):
                exc = self.assert_invalid(data, "version", "expected-version-3")
                self.write_json(self.config_path, data)
                for loader in (cfg.load_config, cfg.load_matrix_config):
                    with self.assertRaises(cfg.ConfigValidationError) as ctx:
                        loader(self.config_path)
                    self.assertEqual(ctx.exception.to_payload(), exc.to_payload())
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(["--local-dir", str(self.local), "config", "check"])
                self.assertEqual((code, err.getvalue()), (3, ""))
                self.assertEqual(json.loads(out.getvalue()), exc.to_payload())

    def test_utf8_byte_cap_and_single_bounded_read(self):
        import io
        cfg = _cfg()
        data = _matrix_config_dict()
        data["classification"]["lane"]["instructions"] = "界" * 100
        encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        at_limit = encoded + b" " * (16384 - len(encoded))
        self.assertEqual(len(at_limit), 16384)
        self.assertLess(len(at_limit.decode("utf-8")), 16384)
        for loader in (cfg.load_config, cfg.load_matrix_config):
            for raw, valid in ((at_limit, True), (at_limit + b" ", False), (b"\xff", False)):
                stream = io.BytesIO(raw)
                with self.subTest(loader=loader.__name__, size=len(raw)):
                    with patch("builtins.open", return_value=stream) as opened, patch.object(stream, "read", wraps=stream.read) as read:
                        if valid:
                            loaded = loader(self.config_path)
                            self.assertEqual(loaded.digest, "sha256:" + hashlib.sha256(raw).hexdigest())
                        else:
                            with self.assertRaises(cfg.ConfigValidationError) as ctx:
                                loader(self.config_path)
                            rule = "size-limit-exceeded" if len(raw) > 16384 else "invalid-json"
                            self.assertEqual(dict(ctx.exception.errors[0]), {"path": "config", "rule": rule})
                        opened.assert_called_once_with(self.config_path, "rb")
                        read.assert_called_once_with(16385)

    def test_removed_search_is_rejected(self):
        base = _matrix_config_dict()
        _cfg().validate_config_data(base)
        self.assert_invalid(_mut(base, lambda d: d["helpers"].__setitem__("search", d["helpers"]["text"])),
                            "helpers.search", "unknown-key")
        self.assert_invalid(_mut(base, lambda d: d["roleBlocks"][0].__setitem__("role", "search")),
                            "roleBlocks[0].role", "invalid-role-block-role")

    def test_load_config_and_state_success_and_immutability(self):
        cfg_mod = _cfg()
        self.write_json(self.config_path, _matrix_config_dict())
        self.write_json(self.state_path, _valid_state_dict())

        loaded = cfg_mod.load_config(self.config_path)
        self.assertTrue(loaded.digest.startswith("sha256:") and loaded.policy_digest.startswith("sha256:"))
        self.assertEqual((loaded.notices, loaded.data["version"]), ((), 3))
        self.assertEqual(cfg_mod.load_config().digest, loaded.digest)
        self.assertEqual(cfg_mod.load_config(local=self.local).digest, loaded.digest)

        for target, key, val in ((loaded.data, "version", 1), (loaded.data["manager"]["primary"]["options"], "x", "y")):
            with self.assertRaises(TypeError):
                target[key] = val
        self.assertIsInstance(loaded.data["providerPools"]["agy-synth-pool"]["providerInstanceIds"], tuple)
        self.assertIsInstance(loaded.data["manager"]["quotaFallback"], tuple)

        from t3compose import cli
        cli.build_parser()
        self.assertIs(_cfg(), cfg_mod)
        self.assertIs(sys.modules["t3compose.config"].ConfigValidationError, cfg_mod.ConfigValidationError)
        loaded_state = _cfg().load_quota_state(self.state_path, config=loaded)
        self.assertTrue(loaded_state.digest.startswith("sha256:"))
        self.assertEqual(cfg_mod.load_quota_state(config=loaded.data).digest, loaded_state.digest)
        self.assertIsInstance(loaded_state.data["quotaObservations"]["agy-synth-alpha"], tuple)
        for target, key, val in ((loaded_state.data, "note", "m"), (loaded_state.data["quotaObservations"]["agy-synth-alpha"][0], "status", "exhausted")):
            with self.assertRaises(TypeError):
                target[key] = val

    def test_exact_byte_digest_policy_digest_projection_and_no_caching(self):
        cfg_mod = _cfg()
        cfg_data = _matrix_config_dict()
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
            ("manager.model", lambda d: d["manager"]["primary"].__setitem__("model", "other")),
            ("worker.model", lambda d: d["matrix"]["worker"]["backend-logic"]["2"]["primary"].__setitem__("model", "other")),
            ("helper.model", lambda d: d["helpers"]["text"]["primary"].__setitem__("model", "other")),
            ("providerPools", lambda d: d["providerPools"]["agy-synth-pool"]["providerInstanceIds"].append("agy-synth-delta")),
            ("jev.timeoutSeconds", lambda d: d["jev"].__setitem__("timeoutSeconds", 60)),
            ("jev.keyFile", lambda d: d["jev"]["keyFile"].update({"path": "/synthetic/other.json", "field": "k"})),
        ):
            with self.subTest(excluded=label):
                self.write_json(self.config_path, _mut(cfg_data, fn))
                self.assertEqual(cfg_mod.load_config(self.config_path).policy_digest, loaded_compact.policy_digest)

        for label, fn in (
            ("ruleVersion", lambda d: d["classification"].__setitem__("ruleVersion", "2026-10-28-v3")),
            ("lane.instructions", lambda d: d["classification"]["lane"].__setitem__("instructions", "Updated.")),
            ("lane.criteria", lambda d: d["classification"]["lane"]["criteria"].__setitem__("BACKEND_LOGIC", "Updated.")),
            ("scope.instructions", lambda d: d["classification"]["scope"].__setitem__("instructions", "Updated.")),
            ("scope.criteria", lambda d: d["classification"]["scope"]["criteria"].__setitem__("COMPLEX", "Updated.")),
            ("riskFacts.scope", lambda d: d["riskFacts"]["shared-interface"].__setitem__("scope", "COMPLEX")),
            ("riskFacts.description", lambda d: d["riskFacts"]["shared-interface"].__setitem__("description", "Changed.")),
            ("jev.model", lambda d: d["jev"].__setitem__("model", "~typesafe/jev-pinned")),
            ("jev.minConfidence", lambda d: d["jev"].__setitem__("minConfidence", 0.75)),
            ("jev.maxContextRetries", lambda d: d["jev"].__setitem__("maxContextRetries", 0)),
        ):
            with self.subTest(included=label):
                self.write_json(self.config_path, _mut(cfg_data, fn))
                self.assertNotEqual(cfg_mod.load_config(self.config_path).policy_digest, loaded_compact.policy_digest)


    def test_boundaries_types_unknown_keys_and_keyfile_never_opened(self):
        cfg_mod = _cfg()
        base = _matrix_config_dict()
        self.assert_invalid(_mut(base, lambda d: d.__setitem__("extraTop", 1)), "extraTop", "unknown-key")
        self.assert_invalid(_mut(base, lambda d: d["jev"].__setitem__("extraJev", "x")), "jev.extraJev", "unknown-key")

        for fn, path, rule in (
            (lambda d: d.__setitem__("version", True), "version", "expected-version-3"),
            (lambda d: d["jev"].__setitem__("minConfidence", True), "jev.minConfidence", "invalid-min-confidence"),
            (lambda d: d["jev"].__setitem__("maxContextRetries", True), "jev.maxContextRetries", "invalid-max-context-retries"),
            (lambda d: d["jev"].__setitem__("timeoutSeconds", True), "jev.timeoutSeconds", "invalid-timeout-seconds"),
        ):
            self.assert_invalid(_mut(base, fn), path, rule)

        for bad_opt in (1, 1.5, None, ["a"], {"k": "v"}):
            self.assert_invalid(_mut(base, lambda d: d["manager"]["primary"]["options"].__setitem__("bad", bad_opt)), "manager.primary.options.bad", "invalid-option-value")

        for k, valid_vals, invalid_vals, rule in (
            ("minConfidence", (0.0001, 0.6, 1, 1.0), (0, 0.0, -0.1, 1.0001, "0.6", 10**400, -(10**400)), "invalid-min-confidence"),
            ("maxContextRetries", (0, 1), (-1, 2, 1.0, "1"), "invalid-max-context-retries"),
            ("timeoutSeconds", (1, 60, 120), (0, -5, 121, 30.0, "30"), "invalid-timeout-seconds"),
        ):
            for v in valid_vals:
                cfg_mod.validate_config_data(_mut(base, lambda d: d["jev"].__setitem__(k, v)))
            for v in invalid_vals:
                self.assert_invalid(_mut(base, lambda d: d["jev"].__setitem__(k, v)), f"jev.{k}", rule)

        for bad_id in ("UPPER", "snake_case", "trailing-", "-leading", "a--b", "word1", "bad\n", "bad\t", "bad\r"):
            self.assert_invalid(_mut(base, lambda d: d["riskFacts"].__setitem__(bad_id, {"description": "desc"})), f"riskFacts.{bad_id}", "invalid-fact-id")

        for fn, path, rule in (
            (lambda d: d.__setitem__("providerPools", {}), "providerPools", "empty-object"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("selection", "random"), "providerPools.agy-synth-pool.selection", "invalid-pool-selection-mode"),
            (lambda d: d["providerPools"]["agy-synth-pool"].__setitem__("providerInstanceIds", ["agy-synth-alpha", "agy-synth-alpha"]), "providerPools.agy-synth-pool.providerInstanceIds[1]", "duplicate-instance-id"),
            (lambda d: d["providerPools"].__setitem__("second-pool", {"selection": "exhaust-in-order", "providerInstanceIds": ["agy-synth-alpha"]}), "providerPools.second-pool.providerInstanceIds[0]", "duplicate-pool-member"),
            (lambda d: d["classification"]["lane"]["criteria"].__delitem__("MIXED"), "classification.lane.criteria.MIXED", "missing-key"),
            (lambda d: d["jev"].__setitem__("provider", "other"), "jev.provider", "invalid-jev-provider"),
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

    def test_refusal_shapes_and_config_check(self):
        import contextlib
        import io
        from t3compose import cli
        entry = {"providerInstanceId": "codex-synth-primary", "model": "gpt-6.1-sol",
                 "kind": "usage-limit", "observedAt": "shape only", "resetAt": None,
                 "evidence": "synthetic evidence"}
        state = _valid_state_dict()
        state["refusals"] = [entry, entry]
        for cfg in (_matrix_config_dict(),):
            self.write_json(self.config_path, cfg)
            self.write_json(self.state_path, state)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--local-dir", str(self.local), "config", "check"])
            self.assertEqual(code, 0, (out.getvalue(), err.getvalue()))
        cases = [({}, "refusals", "expected-list"),
                 ([None], "refusals[0]", "expected-object"),
                 ([{**entry, "extra": True}], "refusals[0].extra", "unknown-key"),
                 ([{**entry, "kind": "unknown"}], "refusals[0].kind", "invalid-refusal-kind")]
        for key in entry:
            missing = dict(entry)
            del missing[key]
            cases.append(([missing], f"refusals[0].{key}", "missing-key"))
        for key in ("providerInstanceId", "model", "observedAt", "evidence"):
            for value in ("", "  ", 1, None):
                cases.append(([{**entry, key: value}], f"refusals[0].{key}", "invalid-string"))
        for value in ("", "  ", 1, False):
            cases.append(([{**entry, "resetAt": value}], "refusals[0].resetAt", "invalid-reset-at"))
        for entries, path, rule in cases:
            with self.subTest(path=path, rule=rule, entries=entries):
                self.write_json(self.state_path, {**state, "refusals": entries})
                with self.assertRaises(_cfg().ConfigValidationError) as ctx:
                    _cfg().load_quota_state(self.state_path)
                self.assertEqual([dict(e) for e in ctx.exception.errors],
                                 [{"path": "state." + path, "rule": rule}])

    def test_quota_state_validation_and_sanitized_io_errors(self):
        cfg_mod = _cfg()
        frozen_cfg, _ = cfg_mod.validate_config_data(_matrix_config_dict())

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

    def test_cli_state_errors(self):
        import contextlib
        import io
        from t3compose import cli
        self.write_json(self.config_path, _matrix_config_dict())
        for state, expected in ((None, {"path": "state", "rule": "unreadable-file"}),
                                (_mut(_valid_state_dict(), lambda s: s.__setitem__("activeProviderInstanceId", "absent")),
                                 {"path": "state.activeProviderInstanceId", "rule": "unknown-pool-member"})):
            if state is not None:
                self.write_json(self.state_path, state)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["--local-dir", str(self.local), "config", "check"])
            self.assertEqual(code, 3)
            self.assertIn(expected, json.loads(out.getvalue())["errors"])

    def test_closed_objects_reject_unknown_missing_and_wrong_shapes(self):
        cfg = _cfg()
        def objects(value, path=()):
            if isinstance(value, dict):
                yield path, value
                for key, item in value.items():
                    yield from objects(item, (*path, key))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from objects(item, (*path, index))
        for base, validate in ((_matrix_config_dict(), cfg.validate_config_data),
                               (_valid_state_dict(), cfg.validate_quota_state_data)):
            for path, obj in objects(base):
                if path and path[-1] in ("providerPools", "riskFacts", "options", "quotaObservations"):
                    continue  # These mappings have dynamic keys, not closed field sets.
                changes = [("extra", "__unexpected__"), ("shape", None)]
                changes += [("missing", key) for key in obj if not (base.get("version") == 3 and (key == "prePass" or (path and path[0] == "riskFacts" and key in ("scope", "criticality"))))]
                changes += [("type", key) for key, value in obj.items() if not isinstance(value, (dict, list))]
                for kind, key in changes:
                    with self.subTest(path=path, kind=kind, key=key):
                        data = copy.deepcopy(base)
                        target = data
                        for part in path:
                            target = target[part]
                        if kind == "extra":
                            target[key] = True
                        elif kind == "missing":
                            del target[key]
                        elif kind == "type":
                            target[key] = []
                        elif path:
                            parent = data
                            for part in path[:-1]:
                                parent = parent[part]
                            parent[path[-1]] = []
                        else:
                            data = []
                        with self.assertRaises(cfg.ConfigValidationError):
                            validate(data)


class OfflineMatrixConfigValidation(OfflineTestCase):
    def test_schema_vocabulary_matches_config(self):
        from t3compose import config, schema
        self.assertEqual(dict(config.MATRIX_LANES), schema._MATRIX_LANES)
        self.assertEqual(set(config.ROLE_BLOCK_ROLES), set(schema._V3_ROLES) - {"search"})
        self.assertEqual(config.HELPER_NAMES, ("verifier", "text"))
        self.assertIn("search", schema._V3_ROLES)  # Historical selection records stay readable.
        classification = _matrix_config_dict()["classification"]
        for question, options in schema._V3_QUESTIONS.items():
            self.assertEqual(options, tuple(classification[question]["criteria"]))

    def setUp(self):
        super().setUp()
        self.local = self.temp / ".local"
        self.local.mkdir()
        self.config_path = self.local / "agent-routing.json"
        self.state_path = self.local / "agent-routing-state.json"
    write_json = OfflineConfigValidation.write_json
    assert_invalid = OfflineConfigValidation.assert_invalid
    def test_matrix_accessors_and_digest(self):
        cfg = _cfg()
        base = _matrix_config_dict()
        self.write_json(self.config_path, base)
        self.write_json(self.state_path, _valid_state_dict())
        real_open, opened = builtins.open, []
        with patch.object(builtins, "open", side_effect=lambda f, *a, **kw: (opened.append(str(f)), real_open(f, *a, **kw))[1]):
            loaded = cfg.load_matrix_config(self.config_path)
        self.assertEqual(opened, [str(self.config_path)])
        self.assertEqual((loaded.notices, loaded.data["version"]), ((), 3))
        self.assertEqual(loaded.digest, "sha256:" + hashlib.sha256(self.config_path.read_bytes()).hexdigest())
        self.assertEqual(cfg.load_matrix_config().digest, loaded.digest)
        self.assertEqual(cfg.load_matrix_config(local=self.local).digest, loaded.digest)
        for obj in (loaded.data, loaded.data["matrix"]["worker"]["backend-logic"]["2"]["primary"]["options"], cfg.MATRIX_LANES):
            with self.assertRaises(TypeError):
                obj["new"] = True
        self.assertIsInstance(loaded.data["manager"]["quotaFallback"], tuple)
        self.assertEqual(cfg.load_quota_state(config=loaded).digest, cfg.load_quota_state(config=loaded.data).digest)
        self.assertEqual(cfg.load_config(self.config_path), loaded)
        self.assertEqual(cfg.load_config(local=self.local), loaded)
        self.assertEqual(cfg.load_config(), loaded)
        self.write_json(self.config_path, [])
        for accessor in (cfg.load_config, cfg.load_matrix_config):
            with self.assertRaises(cfg.ConfigValidationError) as ctx:
                accessor(self.config_path)
            self.assertEqual(ctx.exception.to_payload(), {"valid": False, "errors": [{"path": "$", "rule": "expected-object"}]})
        for content, rule in (("{", "invalid-json"), (" " * 16385, "size-limit-exceeded")):
            self.config_path.write_text(content)
            with self.assertRaises(cfg.ConfigValidationError) as ctx:
                cfg.load_matrix_config(self.config_path)
            self.assertEqual(ctx.exception.errors[0]["rule"], rule)
        self.config_path.unlink()
        with self.assertRaises(cfg.ConfigValidationError) as ctx:
            cfg.load_matrix_config(self.config_path)
        self.assertEqual(ctx.exception.errors[0]["rule"], "unreadable-file")

    def test_matrix_policy_projection(self):
        cfg, base = _cfg(), _matrix_config_dict()
        digest = cfg.compute_policy_digest(base)
        included = [("classification", "ruleVersion"), ("riskFacts", "shared-interface", "description"),
                    ("riskFacts", "credentials-or-guardrail", "scope"), ("riskFacts", "shared-interface", "criticality"),
                    *[("classification", q, "instructions") for q in ("lane", "criticality", "scope")],
                    *[("classification", q, "criteria", next(iter(base["classification"][q]["criteria"]))) for q in ("lane", "criticality", "scope")],
                    *[("jev", k) for k in ("provider", "model", "minConfidence", "maxContextRetries")]]
        excluded = [(k,) for k in ("manager", "matrix", "helpers", "providerPools", "roleBlocks", "quota", "escalation")]
        excluded += [("jev", k) for k in ("timeoutSeconds", "keyFile")]
        for paths, changes in ((included, True), (excluded, False)):
            for path in paths:
                data = copy.deepcopy(base)
                target = data
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "changed"
                self.assertEqual(cfg.compute_policy_digest(data) != digest, changes, path)
        for indent in (None, 2):
            self.config_path.write_text(json.dumps(base, indent=indent))
            self.assertEqual(cfg.load_matrix_config(self.config_path).policy_digest, digest)

    def test_matrix_rules(self):
        cfg, base = _cfg(), _matrix_config_dict()
        cell = ("matrix", "worker", "backend-logic", "2")
        sel = (*cell, "primary")
        cases = [
            (sel, {"model": "x", "options": {}}, "missing-selection-target"),
            ((*sel, "providerPool"), "agy-synth-pool", "disjoint-selection-keys"),
            ((*sel, "providerInstanceId"), "agy-synth-alpha", "direct-selection-names-pool-member"),
            ((*sel, "model"), "", "invalid-string"), ((*sel, "options"), [], "expected-object"),
            ((*sel, "options"), {"": "x"}, "invalid-option-key"),
            ((*sel, "options", "bad"), 1, "invalid-option-value"),
            ((*sel, "roles"), ["worker"], "unknown-key"),
            ((*cell, "quotaFallback"), {}, "expected-list"),
            ((*cell, "quotaFallback"), [base["matrix"]["worker"]["backend-logic"]["2"]["primary"]], "fallback-equals-primary"),
            ((*cell, "quotaFallback"), [base["manager"]["primary"]] * 2, "duplicate-fallback"),
            (("helpers", "text", "primary", "providerPool"), "absent", "unknown-provider-pool"),
            (("helpers", "text", "primary", "options"), {"effort": "high"}, "pool-options-must-be-empty"),
            (("manager", "primary"), base["helpers"]["text"]["primary"], "expected-direct-selection"),
            (("manager", "quotaFallback"), [base["helpers"]["text"]["primary"]], "expected-direct-selection"),
            (("roleBlocks",), {}, "expected-list"),
            (("roleBlocks", 0, "driverKind"), "other", "invalid-driver-kind"),
            (("roleBlocks", 0, "role"), "manager", "invalid-role-block-role"),
            (("roleBlocks", 0, "reason"), "", "invalid-string"),
            (("roleBlocks",), base["roleBlocks"] * 2, "duplicate-role-block"),
            (("riskFacts",), {}, "empty-object"), (("riskFacts",), [], "expected-object"),
            (("riskFacts", "BAD"), {"description": "x"}, "invalid-fact-id"),
            (("riskFacts", "shared-interface", "scope"), "STANDARD", "invalid-floor"),
            (("riskFacts", "shared-interface", "criticality"), "NON_CRITICAL", "invalid-floor"),
            (("riskFacts", "shared-interface", "floor"), "medium", "unknown-key"),
            (("jev", "uncertainResult"), "manager-judgment", "unknown-key"),
        ]
        for key in ("master", "modelPreferences", "easyWorker", "textHelper", "roundTiers", "availabilityFallback"):
            cases.append(((key,), {}, "unknown-key"))
        for path, maximum, rule in ((("quota", "refusalRetryMinutes"), 1440, "invalid-refusal-retry-minutes"),
                                    (("escalation", "repairAttempts"), 10, "invalid-repair-attempts"),
                                    (("escalation", "correctionsRoundsPerFinding"), 10, "invalid-corrections-rounds-per-finding")):
            for value in (0, maximum + 1, True, "30", 30.0):
                cases.append((path, value, rule))
            for value in (1, maximum):
                data = copy.deepcopy(base)
                data[path[0]][path[1]] = value
                cfg.validate_config_data(data)
        for key, values, rule in (
            ("provider", ("other",), "invalid-jev-provider"),
            ("minConfidence", (0, 1.1, True, "0.6"), "invalid-min-confidence"),
            ("maxContextRetries", (-1, 2, True, 1.0), "invalid-max-context-retries"),
            ("timeoutSeconds", (0, 121, True, 1.0), "invalid-timeout-seconds"),
        ):
            cases.extend((("jev", key), value, rule) for value in values)
        cases += [(("jev", "keyFile", "path"), "relative", "expected-absolute-path"),
                  (("providerPools",), {}, "empty-object"),
                  (("providerPools", "agy-synth-pool", "selection"), "random", "invalid-pool-selection-mode"),
                  (("providerPools", "agy-synth-pool", "providerInstanceIds"), [], "empty-list"),
                  (("providerPools", "agy-synth-pool", "providerInstanceIds"), ["x", "x"], "duplicate-instance-id"),
                  (("providerPools", "other"), base["providerPools"]["agy-synth-pool"], "duplicate-pool-member")]
        for path, value, rule in cases:
            with self.subTest(path=path, rule=rule):
                data = copy.deepcopy(base)
                target = data
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = copy.deepcopy(value)
                with self.assertRaises(cfg.ConfigValidationError) as ctx:
                    cfg.validate_config_data(data)
                expected_path = ".".join(str(k) for k in path).replace("roleBlocks.0", "roleBlocks[0]")
                if rule == "disjoint-selection-keys":
                    expected_path = expected_path.rsplit(".", 1)[0]
                elif rule in ("fallback-equals-primary", "duplicate-fallback"):
                    expected_path += "[0]" if rule == "fallback-equals-primary" else "[1]"
                elif rule == "expected-direct-selection":
                    expected_path += "[0].providerPool" if path[-1] == "quotaFallback" else ".providerPool"
                elif rule == "duplicate-role-block":
                    expected_path += "[1]"
                elif rule == "invalid-option-key":
                    pass
                elif rule in ("duplicate-instance-id", "duplicate-pool-member"):
                    expected_path += "[1]" if rule == "duplicate-instance-id" else ".providerInstanceIds[0]"
                self.assertIn({"path": expected_path, "rule": rule}, [dict(e) for e in ctx.exception.errors])
        for path in (("manager",), ("helpers", "text"), ("matrix", "feature-lead", "default", "1"), cell):
            data = copy.deepcopy(base)
            target = data
            for key in path:
                target = target[key]
            target["prePass"] = []  # Only the placement error, without selection errors.
            self.assertEqual(self.assert_invalid(data, ".".join(path) + ".prePass", "pre-pass-outside-reviewer-cell").to_payload()["errors"],
                             [{"path": ".".join(path) + ".prePass", "rule": "pre-pass-outside-reviewer-cell"}])
        for lane in ("core", "non-critical"):
            for stage in ("1", "3"):
                data = copy.deepcopy(base)
                data["matrix"]["reviewer"][lane][stage]["prePass"] = base["manager"]["primary"]
                cfg.validate_config_data(data)
        data = copy.deepcopy(base)
        high = data["matrix"]["worker"]["backend-logic"]["2"]["quotaFallback"][0]
        xhigh = copy.deepcopy(high)
        xhigh["options"]["effort"] = "xhigh"
        data["matrix"]["worker"]["backend-logic"]["2"]["quotaFallback"] = [high, xhigh]
        cfg.validate_config_data(data)

    def test_matrix_cli(self):
        base, cfg = _matrix_config_dict(), _cfg()
        self.write_json(self.state_path, _valid_state_dict())
        example = ROOT / "docs/examples/agent-routing.v3.example.json"
        def invoke(path, *, child=False):
            if child:
                proc = subprocess.run(guarded_helper_command("--local-dir", str(self.local), "config", "check", "--config", str(path)), text=True, capture_output=True)
                self.assertEqual(proc.stderr, "")
                return proc.returncode, json.loads(proc.stdout)
            import contextlib
            import io
            from t3compose import cli
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--local-dir", str(self.local), "config", "check", "--config", str(path)])
            self.assertEqual(err.getvalue(), "")
            return code, json.loads(out.getvalue())
        code, output = invoke(example, child=True)
        self.assertEqual(code, 0)
        self.assertEqual(output, {"version": 3, "digest": "sha256:" + hashlib.sha256(example.read_bytes()).hexdigest(),
            "stateDigest": "sha256:" + hashlib.sha256(self.state_path.read_bytes()).hexdigest(),
            "policyDigest": cfg.load_matrix_config(example).policy_digest, "ruleVersion": base["classification"]["ruleVersion"],
            "riskFacts": {k: {key: val for key, val in v.items() if key != "description"} for k, v in base["riskFacts"].items()}, "notices": []})
        for path, value, error_path, rule in (
            (("manager", "quotaFallback"), [base["helpers"]["verifier"]["primary"]] * 2, "manager.quotaFallback[1]", "duplicate-fallback"),
            (("manager", "prePass"), base["manager"]["primary"], "manager.prePass", "pre-pass-outside-reviewer-cell"),
            (("matrix", "worker", "backend-logic", "2"), None, "matrix.worker.backend-logic.2", "missing-key"),
            (("riskFacts", "shared-interface", "criticality"), "OTHER", "riskFacts.shared-interface.criticality", "invalid-floor"),
            (("roleBlocks", 0, "role"), "other", "roleBlocks[0].role", "invalid-role-block-role"),
            (("quota", "refusalRetryMinutes"), 1441, "quota.refusalRetryMinutes", "invalid-refusal-retry-minutes"),
        ):
            data, target = copy.deepcopy(base), None
            target = data
            for key in path[:-1]:
                target = target[key]
            if value is None:
                del target[path[-1]]
            else:
                target[path[-1]] = value
            self.write_json(self.config_path, data)
            code, output = invoke(self.config_path)
            self.assertEqual((code, output), (3, {"valid": False, "errors": [{"path": error_path, "rule": rule}]}))
