"""Process contracts use inert children or in-process fixtures, never services."""
import contextlib
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
from subprocess import Popen as PopenHandle
import sys
import textwrap
import traceback
from unittest.mock import patch

from tests import support
from tests.support import OfflineTestCase, ROOT
from t3compose import process


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


class OfflineProcessStart(OfflineTestCase):
    def assert_public_start_failure(self, argv, kind, **kwargs):
        try:
            process.start(argv, **kwargs)
        except process.ProcessError as error:
            self.assertEqual((error.kind, error.returncode), (kind, None))
            for display in (str(error), repr(error), repr(error.args),
                            "".join(traceback.format_exception(error))):
                self.assertNotIn("synthetic-private-sentinel", display)
            self.assertTrue(error.__suppress_context__)
            self.assertIsNone(error.__cause__)
        else:
            self.fail("launch failure must become sanitized ProcessError")

    def test_start_refuses_invalid_command_before_popen(self):
        sentinel = "synthetic-private-sentinel"
        for argv, kwargs in ((sentinel, {}), (b"synthetic", {}), (None, {}),
                             ((), {}), (("git",), {}), ({"git": 1}, {}),
                             ([], {}), ([sentinel], {"shell": True}),
                             ([sentinel], {"shell": sentinel})):
            with self.subTest(argv=argv), patch.object(subprocess, "Popen") as launch:
                with self.assertRaises(process.ProcessError) as caught:
                    process.start(argv, **kwargs)
                self.assertEqual(caught.exception.kind, "invalid-command")
                self.assertNotIn(sentinel, repr(caught.exception))
                launch.assert_not_called()

    def test_start_sanitizes_backend_failures_and_exception_display(self):
        sentinel = "synthetic-private-sentinel"
        for failure, kind in ((FileNotFoundError(2, sentinel, sentinel), "not-found"),
                              (PermissionError(sentinel), "os-error"),
                              (OSError(sentinel), "os-error"),
                              (TypeError(sentinel), "invalid-options"),
                              (ValueError(sentinel), "invalid-options")):
            with self.subTest(kind=kind), patch.object(subprocess, "Popen", side_effect=failure):
                self.assert_public_start_failure(["synthetic", sentinel], kind,
                                                 env={"SYNTHETIC": sentinel})

    def test_start_sanitizes_real_launch_and_option_errors(self):
        missing = self.temp / "synthetic-private-sentinel-missing"
        denied = self.temp / "synthetic-private-sentinel-denied"
        denied.write_text("synthetic fixture")
        denied.chmod(0o600)
        for program, kind in ((missing, "not-found"), (denied, "os-error")):
            self.allow_program(program)
            with self.subTest(kind=kind):
                self.assert_public_start_failure([str(program)], kind)
        self.allow_program("/bin/sleep")
        for options in ({"bufsize": "synthetic-private-sentinel"},
                        {"unknown_option": "synthetic-private-sentinel"},
                        {"creationflags": 1}):
            with self.subTest(options=options):
                self.assert_public_start_failure(["/bin/sleep", "0"],
                                                 "invalid-options", **options)

    def test_start_preserves_guard_refusal(self):
        with self.assertRaisesRegex(AssertionError, "offline guard"):
            process.start(["/synthetic/bin/curl"])

    def test_start_forwards_controls_without_defaults_or_handle_wrapping(self):
        argv, handle = ["synthetic"], object()
        controls = dict(env={"SYNTHETIC": "fixture"}, cwd=self.temp,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, pass_fds=(7,), text=True,
                        shell=False, start_new_session=True)
        for kwargs in ({}, controls):
            with self.subTest(kwargs=kwargs), patch.object(subprocess, "Popen", return_value=handle) as launch:
                self.assertIs(process.start(argv, **kwargs), handle)
                launch.assert_called_once_with(argv, **kwargs)

    def test_start_returns_real_handle_with_streams_environment_cwd_and_fds(self):
        script = self.temp / "start-child.py"
        script.write_text(textwrap.dedent('''\
            import json, os, sys
            from pathlib import Path
            print(json.dumps({'input': sys.stdin.read(), 'value': os.environ['SYNTHETIC'],
                              'fd': os.read(int(sys.argv[1]), 100).decode(),
                              'script': str(Path(__file__).resolve())}))
            print('synthetic stderr', file=sys.stderr)
            sys.exit(9)
            '''))
        payload = self.temp / "payload"
        payload.write_bytes(b"synthetic inherited data")
        with payload.open("rb") as inherited:
            argv = [sys.executable, "-I", "-S", "-B", str(ROOT / "tests/support.py"),
                    script.name, str(inherited.fileno())]
            with process.start(argv, cwd=self.temp, env={"SYNTHETIC": "fixture"},
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               pass_fds=(inherited.fileno(),)) as child:
                self.assertIsInstance(child, PopenHandle)
                stdout, stderr = child.communicate("synthetic input", timeout=10)
                self.assertEqual(child.wait(timeout=10), 9)
            self.assertTrue(child.stdout.closed)
            self.assertTrue(child.stderr.closed)
        self.assertEqual(json.loads(stdout), {"input": "synthetic input", "value": "fixture",
                                             "fd": "synthetic inherited data", "script": str(script)})
        self.assertEqual(stderr, "synthetic stderr\n")

    def test_start_leaves_termination_and_waiting_to_caller(self):
        self.allow_program("/bin/sleep")
        with process.start(["/bin/sleep", "30"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as child:
            self.assertIsNone(child.poll())
            try:
                child.terminate()
                self.assertLess(child.wait(timeout=10), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=10)
