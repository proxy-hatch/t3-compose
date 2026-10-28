import contextlib
import io
import json
import os
import runpy
import socket
import subprocess
import sys
from unittest.mock import patch
import urllib.request

from tests.support import OfflineTestCase, ROOT


class OfflineCLI(OfflineTestCase):
    def run_cli(self, *args, flags=("-I", "-S"), entry=None):
        return subprocess.run([sys.executable, *flags, str(entry or ROOT / "bin/t3c"), *args],
                              cwd=self.temp, capture_output=True, text=True)

    def test_version_from_other_directory_and_symlink(self):
        alias = self.temp / "helper"
        alias.symlink_to(ROOT / "bin/t3c")
        result = self.run_cli("version", entry=alias)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["root"], str(ROOT))
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(data["commit"], head)
        self.assertIs(data["dirty"], True)  # This test's temporary directory is untracked.
        self.assertEqual(data["python"], sys.version.split()[0])
        self.assertEqual(result.stderr, "")

    def test_missing_either_flag_refuses_with_correct_command(self):
        for flags in ((), ("-I",), ("-S",)):
            with self.subTest(flags=flags):
                result = self.run_cli("version", flags=flags)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("-I -S", result.stderr)
                self.assertIn(str(ROOT / "bin/t3c"), result.stderr)

    def test_text_and_invalid_input(self):
        result = self.run_cli("--text", "version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("root:", result.stdout)
        for args in ((), ("synthetic-secret",), ("version", "--synthetic-secret")):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("synthetic-secret", result.stderr)

    def test_help_respects_output_mode_and_old_python_refuses(self):
        for args in (("--help",), ("version", "--help")):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage:", json.loads(result.stdout)["help"])
        result = self.run_cli("--text", "--help")
        self.assertTrue(result.stdout.startswith("usage:"))
        err = io.StringIO()
        with patch.object(sys, "version_info", (3, 8)), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(ROOT / "bin/t3c"))
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn("3.9+", err.getvalue())

    def test_registry_missing_shared_groups_and_broken_modules(self):
        import t3compose
        from t3compose.cli import main
        # Use real imports to distinguish an absent plugin from a broken dependency.
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            def invoke(args):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(args)
                return code, out.getvalue(), err.getvalue()

            self.assertEqual(invoke(["version"])[0], 0)
            for module, command in (("config", "first"), ("records", "second")):
                (self.temp / (module + ".py")).write_text(
                    "def register(registry):\n"
                    "    p = registry.group('shared').add_parser('" + command + "')\n"
                    "    p.set_defaults(handler=lambda args: {'command': '" + command + "'})\n")
            for command in ("first", "second"):
                code, out, err = invoke(["shared", command])
                self.assertEqual((code, json.loads(out), err),
                                 (0, {"command": command}, ""))
            for source in ("import synthetic_missing_dependency\n", "def broken(\n", "pass\n",
                           "def register(registry): raise RuntimeError('synthetic-secret')\n"):
                (self.temp / "evidence.py").write_text(source)
                sys.modules.pop("t3compose.evidence", None)
                code, out, err = invoke(["version"])
                self.assertEqual((code, out), (2, ""))
                self.assertIn("evidence", err)
                self.assertNotIn("synthetic_missing_dependency", err)
                self.assertNotIn("synthetic-secret", err)

    def test_local_dir_and_exit_codes(self):
        from t3compose.cli import CommandError, ExitCode, main
        import t3compose
        (self.temp / "config.py").write_text(
            "from t3compose.cli import CommandError\n"
            "def register(registry):\n"
            "    p = registry.group('sample').add_parser('run')\n"
            "    p.add_argument('code', type=int)\n"
            "    def run(args):\n"
            "        if args.code: raise CommandError('check refused', args.code)\n"
            "        return {'local': str(args.local_dir)}\n"
            "    p.set_defaults(handler=run)\n")
        self.assertEqual({int(code) for code in ExitCode}, {0, 1, 2, 3, 4, 5, 75})
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            for code in (0, 1, 2, 3, 4, 5, 75):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = main(["--local-dir", str(self.temp), "sample", "run", str(code)])
                self.assertEqual(status, code)
                if not code:
                    self.assertEqual(json.loads(out.getvalue()), {"local": str(self.temp)})
                else:
                    self.assertEqual(out.getvalue(), "")
                    self.assertIn("check refused", err.getvalue())


class OfflineGuard(OfflineTestCase):
    def test_network_and_urllib_are_blocked(self):
        for call in (lambda: socket.socket(), lambda: socket.socketpair(),
                     lambda: socket.SocketType(),
                     lambda: socket.create_connection(("example.invalid", 443)),
                     lambda: socket.getaddrinfo("example.invalid", 443),
                     lambda: socket.gethostbyname("example.invalid"),
                     lambda: urllib.request.urlopen("https://example.invalid"),
                     lambda: urllib.request.build_opener().open("https://example.invalid")):
            with self.assertRaisesRegex(AssertionError, "offline guard"):
                call()

    def test_credential_and_network_commands_are_blocked(self):
        for command in ("security", "gh", "ssh", "curl"):
            for args in ([command], ["/synthetic/bin/" + command], ["env", command]):
                with self.subTest(args=args), self.assertRaisesRegex(AssertionError, "offline guard"):
                    subprocess.run(args)
        with self.assertRaises(AssertionError):
            subprocess.run(["synthetic"], executable="/usr/bin/security")
        with self.assertRaises(AssertionError):
            subprocess.run("echo synthetic", shell=True)
        for call in (lambda: subprocess.run(["/bin/sh", "-c", "curl https://example.invalid"]),
                     lambda: os.system("curl https://example.invalid"),
                     lambda: os.popen("curl https://example.invalid")):
            with self.assertRaises(AssertionError):
                call()
