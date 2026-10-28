"""Read-only PR verification with injected readers; never launches gh."""
import contextlib
import importlib
import io
import json
import os
import subprocess
import traceback
from unittest.mock import patch

from tests.support import FakeRunner, OfflineTestCase
from t3compose import cli, process


class OfflinePullRequest(OfflineTestCase):
    def setUp(self):
        self.allow_program("grep")
        self.local = self.temp / "state-root"
        self.list_file = self.local / "state/private-identifiers.txt"
        self.list_file.parent.mkdir(parents=True)
        self.list_file.write_text("syn-secret\n")
        self.payload = {"head": {"sha": "a" * 40}, "base": {"ref": "main"},
                        "state": "open", "user": {"login": "synthetic-bot[bot]"}}

    def module(self):
        self.assertIsNotNone(importlib.util.find_spec("t3compose.pullrequest"))
        return importlib.import_module("t3compose.pullrequest")

    def invoke(self, *extra, required=None, text=False):
        args = ["--local-dir", str(self.local), *(["--text"] if text else []), "pr", "verify",
                *(required if required is not None else ["--repo", "example/project", "--number", "7",
                                                         "--head", "a" * 40]), *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_reader_success_optional_omission_and_exact_mismatches(self):
        module = self.module()
        with patch.object(module, "read_pull_request", return_value=self.payload):
            result = self.invoke("--base", "main", "--author", "synthetic-bot[bot]")
            self.assertEqual(result[0], 0, result[2])
            self.assertEqual(json.loads(result[1]), {"ok": True, "mismatches": [], "action": None})
            self.payload["base"]["ref"] = "syn-secret"
            self.payload["user"]["login"] = "syn-secret"
            self.assertEqual(self.invoke()[0], 0)
        cases = [("head", {"sha": "b" * 40}, "head"), ("base", {"ref": "syn-secret"}, "base"),
                 ("state", "closed", "state"), ("user", {"login": "syn-secret"}, "author")]
        for key, value, field in cases:
            payload = {"head": {"sha": "a" * 40}, "base": {"ref": "main"},
                       "state": "open", "user": {"login": "synthetic-bot[bot]"}, key: value}
            for text in (False, True):
                with patch.object(module, "read_pull_request", return_value=payload):
                    result = self.invoke("--base", "main", "--author", "synthetic-bot[bot]", text=text)
                self.assertEqual(result[0], 1, result[2])
                self.assertIn("report to Shawn; not PR-ready", result[1])
                self.assertNotIn("syn-secret", "".join(result[1:]))
                if not text:
                    self.assertEqual(json.loads(result[1])["mismatches"], [field])

    def test_all_mismatches_and_merged_refusal(self):
        with patch.object(self.module(), "read_pull_request", return_value={
                "head": {"sha": "b" * 40}, "base": {"ref": "other"}, "state": "closed",
                "merged": True, "user": {"login": "other"}}):
            result = self.invoke("--base", "main", "--author", "expected")
        self.assertEqual(result[0], 1)
        self.assertEqual(json.loads(result[1])["mismatches"], ["head", "base", "state", "author"])

    def test_input_validation_prevents_api_call(self):
        with patch.object(self.module(), "read_pull_request", side_effect=AssertionError("must not read")):
            for flag, value in (("--repo", "--syn-secret"), ("--repo", "x/y/z"), ("--repo", "../x"),
                                ("--repo", "x/y?query"), ("--number", "0"), ("--number", "-1"),
                                ("--number", "True"), ("--head", "a" * 39), ("--head", "g" * 40),
                                ("--base", ""), ("--author", "")):
                result = self.invoke(flag, value)
                self.assertEqual(result[0], 2)
                self.assertEqual(result[1], "")
                self.assertNotIn("syn-secret", result[2])

    def test_missing_invalid_and_reloaded_lists_block_reader(self):
        module = self.module()
        with patch.object(module, "read_pull_request", return_value=self.payload) as reader:
            self.assertEqual(self.invoke()[0], 0)
            reader.reset_mock()
            for content in (b"", b"[\n", b"\xff", b"x\x00", b"x" * 65537,
                            b"x\n" * 513, b"x" * 1025):
                self.list_file.write_bytes(content)
                self.assertEqual(self.invoke()[0], 2)
            self.list_file.unlink()
            self.assertEqual(self.invoke()[0], 2)
            self.list_file.mkdir()
            self.assertEqual(self.invoke()[0], 2)
            reader.assert_not_called()

    def test_malformed_reader_results_fail_even_for_omitted_expectations(self):
        module = self.module()
        invalid = [None, [], {}, {**self.payload, "state": True}, {**self.payload, "state": "unknown"},
                   {**self.payload, "head": {"sha": "bad"}}, {**self.payload, "base": {"ref": 2}},
                   {**self.payload, "user": {"login": ""}}, {**self.payload, "base": None},
                   {**self.payload, "user": {"login": "\ud800"}}]
        for payload in invalid:
            with patch.object(module, "read_pull_request", return_value=payload):
                self.assertEqual(self.invoke()[0], 2)
        with patch.object(module, "read_pull_request", side_effect=RuntimeError("syn-secret")):
            result = self.invoke()
            self.assertEqual(result[0], 2)
            self.assertNotIn("syn-secret", "".join(result[1:]))
            args = cli.build_parser().parse_args(["--local-dir", str(self.local), "pr", "verify",
                "--repo", "example/project", "--number", "7", "--head", "a" * 40])
            args.local_dir = self.local
            with self.assertRaises(cli.CommandError) as raised:
                args.handler(args)
            self.assertNotIn("syn-secret", "".join(traceback.format_exception(raised.exception)))

    def test_production_reader_uses_single_read_only_get(self):
        module = self.module()
        runner = FakeRunner({
            "grep": lambda a, **kw: subprocess.CompletedProcess(a, 1, b"", b""),
            "gh": lambda a, **kw: subprocess.CompletedProcess(a, 0, json.dumps(self.payload).encode(), b"syn-secret")})
        with patch.object(module.process, "run", runner):
            result = self.invoke()
        self.assertEqual(result[0], 0, result[2])
        calls = [(argv, kw) for argv, kw in runner.calls if argv[0] == "gh"]
        self.assertEqual(len(calls), 1)
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["gh", "api", "--hostname", "github.com", "--method", "GET", "repos/example/project/pulls/7"])
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs.get("shell", False))
        self.assertGreater(kwargs["timeout"], 0)
        self.assertNotIn("syn-secret", "".join(result[1:]))

    def test_corrections_api_host_is_explicit_despite_ambient_host(self):
        module = self.module()
        runner = FakeRunner({"gh": lambda argv, **kw:
            subprocess.CompletedProcess(argv, 0, json.dumps(self.payload).encode(), b"")})
        with patch.dict(os.environ, {"GH_HOST": "other.example.invalid"}), \
                patch.object(process, "run", runner):
            self.assertEqual(module.read_pull_request("example/project", 7), self.payload)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][0], ["gh", "api", "--hostname", "github.com",
                                            "--method", "GET", "repos/example/project/pulls/7"])

    def test_api_process_and_json_failures_are_private(self):
        module = self.module()
        for response in (b"not json syn-secret", b"{}", b'{"state":"open","state":"closed"}',
                         b"\xff", b"x" * 1048577, b'{"x":NaN}', b'{"x":"\\ud800"}'):
            runner = FakeRunner({"gh": lambda a, **kw: subprocess.CompletedProcess(a, 0, response, b"")})
            with patch.object(module.process, "run", runner), self.assertRaises(cli.CommandError):
                module.read_pull_request("example/project", 7)
        for failure in (process.ProcessError("timeout"), OSError("syn-secret"),
                        subprocess.CompletedProcess([], 1, b"syn-secret", b"syn-secret"),
                        subprocess.CompletedProcess([], False, b"{}", b""),
                        subprocess.CompletedProcess([], 0, "{}", "")):
            def failed(a, **kw):
                if isinstance(failure, Exception):
                    raise failure
                return failure
            with patch.object(module.process, "run", FakeRunner({"gh": failed})):
                with self.assertRaises(cli.CommandError) as raised:
                    module.read_pull_request("example/project", 7)
            self.assertNotIn("syn-secret", "".join(traceback.format_exception(raised.exception)))

    def test_import_registration_do_not_read_state_or_start_processes(self):
        module = self.module()
        with patch.object(process, "run", side_effect=AssertionError("process")), \
                patch("builtins.open", side_effect=AssertionError("state")), \
                patch("os.open", side_effect=AssertionError("state")):
            importlib.reload(module)
            self.assertIn(("pr", "verify"), cli.command_names())

    def test_injected_reader_is_bounded_and_required_objects_are_objects(self):
        invalid = [{**self.payload, "extra": "x" * 1048577},
                   {**self.payload, "extra": float("inf")},
                   {**self.payload, "extra": "\ud800"}]
        for payload in invalid:
            with patch.object(self.module(), "read_pull_request", return_value=payload):
                self.assertEqual(self.invoke()[0], 2)

    def test_entry_point_with_injected_reader_and_guard(self):
        import runpy
        import sys
        from tests.support import ROOT
        out, err = io.StringIO(), io.StringIO()
        with patch.object(self.module(), "read_pull_request", return_value=self.payload), \
                patch.object(sys, "argv", [str(ROOT / "bin/t3c"), "--local-dir", str(self.local),
                    "pr", "verify", "--repo", "example/project", "--number", "7", "--head", "a" * 40]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as exited:
                runpy.run_path(str(ROOT / "bin/t3c"), run_name="__main__")
        self.assertEqual(exited.exception.code, 0, err.getvalue())
        self.assertTrue(json.loads(out.getvalue())["ok"])

    def test_reader_command_error_is_not_trusted_public_text(self):
        with patch.object(self.module(), "read_pull_request", side_effect=cli.CommandError("syn-secret")):
            result = self.invoke()
        self.assertEqual(result[0], 2)
        self.assertNotIn("syn-secret", "".join(result[1:]))
