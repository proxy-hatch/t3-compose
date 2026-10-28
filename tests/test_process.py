"""Process contracts use inert children or in-process fixtures, never services."""
import contextlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import traceback
from unittest.mock import patch

from tests import support
from tests.support import OfflineTestCase, ROOT


class OfflineProcess(OfflineTestCase):
    def process_module(self):
        self.assertIsNotNone(importlib.util.find_spec("t3compose.process"),
                             "shared process seam must exist")
        return importlib.import_module("t3compose.process")

    def test_refuses_nonlist_empty_and_shell_before_launch(self):
        process = self.process_module()
        for argv, kwargs in (("synthetic-private-command", {}), (b"secret", {}),
                             ((), {}), (("git",), {}), ([], {}),
                             (["synthetic-private-command"], {"shell": True})):
            with self.subTest(argv=argv), patch.object(subprocess, "run") as launch:
                with self.assertRaises(process.ProcessError) as error:
                    process.run(argv, **kwargs)
                self.assertEqual(error.exception.kind, "invalid-command")
                self.assertNotIn("synthetic-private", str(error.exception))
                launch.assert_not_called()

    def test_failure_messages_suppress_argv_environment_output_and_backend(self):
        process = self.process_module()
        sentinel = "synthetic-private-sentinel-581"
        argv = ["synthetic-program", sentinel]
        failures = (
            (subprocess.CalledProcessError(7, argv, output=sentinel, stderr=sentinel), "exit", 7),
            (FileNotFoundError(sentinel), "not-found", None),
            (subprocess.TimeoutExpired(argv, 0.01, output=sentinel, stderr=sentinel), "timeout", None),
            (PermissionError(sentinel), "os-error", None),
            (ValueError(sentinel), "invalid-options", None),
            (TypeError(sentinel), "invalid-options", None),
        )
        for failure, kind, returncode in failures:
            with self.subTest(kind=kind), patch.object(subprocess, "run", side_effect=failure):
                try:
                    process.run(argv, check=True, env={"PRIVATE": sentinel})
                except process.ProcessError as error:
                    self.assertEqual((error.kind, error.returncode), (kind, returncode))
                    self.assertNotIn(sentinel, str(error))
                    self.assertNotIn(sentinel, repr(error))
                    self.assertNotIn(sentinel, str(error.args))
                    self.assertNotIn(sentinel, "".join(traceback.format_exception(
                        type(error), error, error.__traceback__)))
                else:
                    self.fail("process failure must become sanitized ProcessError")
        with patch.object(subprocess, "run", side_effect=AssertionError("offline guard")):
            with self.assertRaisesRegex(AssertionError, "offline guard"):
                process.run(["synthetic"])

    def test_real_controls_capture_input_cwd_env_failure_and_timeout(self):
        process = self.process_module()
        repo = self.temp / "scratch-repository"
        process.run(["git", "init", str(repo)], check=True, capture_output=True)
        found = process.run(["git", "rev-parse", "--show-toplevel"], cwd=repo,
                            check=True, capture_output=True, text=True)
        self.assertEqual(Path(found.stdout.strip()), repo)
        child = self.temp / "harmless.py"
        child.write_text(textwrap.dedent('''\
            import json, os, sys
            from pathlib import Path
            print(json.dumps({'input': sys.stdin.read(), 'value': os.environ['SYNTHETIC'],
                              'cwd': str(Path.cwd()), 'protocol': os.environ['GIT_ALLOW_PROTOCOL']}))
            print('synthetic captured stderr', file=sys.stderr)
            sys.exit(int(sys.argv[1]))
            '''))
        argv = [sys.executable, "-I", "-S", "-B", str(ROOT / "tests/support.py"), str(child)]
        kwargs = dict(capture_output=True, text=True, input="synthetic input",
                      env={"SYNTHETIC": "fixture"}, cwd=self.temp, timeout=10)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = process.run([*argv, "0"], check=True, shell=False, **kwargs)
        self.assertIsInstance(result, subprocess.CompletedProcess)
        data = json.loads(result.stdout)
        self.assertEqual((data["input"], data["value"], data["protocol"]),
                         ("synthetic input", "fixture", "file"))
        self.assertEqual(Path(data["cwd"]).name, "work")
        self.assertFalse(Path(data["cwd"]).exists())
        self.assertEqual(result.stderr, "synthetic captured stderr\n")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))
        result = process.run([*argv, "9"], check=False, **kwargs)
        self.assertEqual(result.returncode, 9)
        with self.assertRaises(process.ProcessError) as error:
            process.run([*argv, "9"], check=True, **kwargs)
        self.assertEqual((error.exception.kind, error.exception.returncode), ("exit", 9))
        # An inert allowed native program avoids killing a bootstrap sandbox
        # before its context can unwind during this timeout test.
        self.allow_program("/bin/sleep")
        with self.assertRaises(process.ProcessError) as error:
            process.run(["/bin/sleep", "10"], timeout=0.01, capture_output=True)
        self.assertEqual(error.exception.kind, "timeout")

    def test_version_uses_fake_runner_and_preserves_read_only_dispatch(self):
        process = self.process_module()
        self.assertTrue(hasattr(support, "FakeRunner"), "shared fixtures must be available")
        from t3compose import cli

        def git(argv, **kwargs):
            if argv == ["git", "-C", str(ROOT), "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(argv, 0, "synthetic-head\n", "")
            if argv == ["git", "-C", str(ROOT), "--no-optional-locks", "status", "--porcelain"]:
                return subprocess.CompletedProcess(argv, 0, "?? synthetic\n", "")
            self.fail("unexpected Git read")

        fake = support.FakeRunner({"git": git})
        with patch.object(process, "run", fake):
            info = cli.helper_info()
        self.assertEqual(info, {"root": str(ROOT), "commit": "synthetic-head", "dirty": True,
                                "python": sys.version.split()[0]})
        self.assertEqual(len(fake.calls), 2)
        for argv, kwargs in fake.calls:
            self.assertEqual(kwargs, {"check": True, "capture_output": True, "text": True})

    def test_fake_external_consumers_parse_fixtures_without_any_child(self):
        process = self.process_module()
        self.assertTrue(hasattr(support, "FakeRunner"))
        from t3compose import cli
        # The production dispatcher consumes a synthetic handler. Later command
        # modules use this same in-process seam; no such commands are added here.
        def consume(args):
            response = process.run(["/synthetic/bin/gh", "fixture", "--json"],
                                   check=True, capture_output=True, text=True)
            credential = process.run(["/synthetic/bin/security", "fixture"],
                                     check=True, capture_output=True, text=True)
            return {"number": json.loads(response.stdout)["number"],
                    "credentialPresent": bool(credential.stdout.strip()),
                    "local": str(args.local_dir)}

        fake = support.FakeRunner({
            "gh": lambda argv, **kw: subprocess.CompletedProcess(argv, 0, '{"number":17}', ""),
            "security": lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "synthetic-value\n", ""),
        })
        out, err = io.StringIO(), io.StringIO()
        with patch.object(process, "run", fake), patch.object(cli, "version", consume), \
                patch.object(subprocess, "Popen", side_effect=AssertionError("no real child")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["version"])
            with self.assertRaisesRegex(AssertionError, "unconfigured"):
                process.run(["/synthetic/bin/curl"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()),
                         {"number": 17, "credentialPresent": True, "local": str(self.temp / ".local")})
        self.assertEqual(err.getvalue(), "")
        self.assertEqual([call[0] for call in fake.calls], [
            ["/synthetic/bin/gh", "fixture", "--json"],
            ["/synthetic/bin/security", "fixture"], ["/synthetic/bin/curl"]])

    def test_helper_command_requires_test_substitution_for_relaunch(self):
        process = self.process_module()
        self.assertTrue(hasattr(support, "guarded_helper_command"))
        argv = process.helper_command("--text", "version")
        self.assertEqual(argv, [sys.executable, "-I", "-S", "-B", str(ROOT / "bin/t3c"),
                                "--text", "version"])
        with self.assertRaisesRegex(AssertionError, "offline guard"):
            process.run(argv)
        with patch.object(process, "helper_command", support.guarded_helper_command):
            result = process.run(process.helper_command("version"), check=True,
                                 capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["root"], str(ROOT))
        self.assertIsInstance(json.loads(result.stdout)["dirty"], bool)
