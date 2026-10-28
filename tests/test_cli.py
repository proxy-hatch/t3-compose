import contextlib
import io
import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
import textwrap
from unittest.mock import patch
import urllib.request

from tests.support import OfflineTestCase, ROOT


class OfflineCLI(OfflineTestCase):
    def run_cli(self, *args, flags=("-I", "-S", "-B"), entry=None):
        target = str(entry or ROOT / "bin/t3c")
        return subprocess.run([sys.executable, *flags, str(ROOT / "tests/support.py"), target, *args],
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

    def test_registry_system_exit_at_import(self):
        import t3compose
        from t3compose.cli import main
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            (self.temp / "evidence.py").write_text("import sys; sys.exit(0)\n")
            sys.modules.pop("t3compose.evidence", None)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["version"])
            self.assertEqual((code, out.getvalue()), (2, ""))
            self.assertIn("evidence", err.getvalue())

    def test_registry_system_exit_in_register(self):
        import t3compose
        from t3compose.cli import main
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            (self.temp / "evidence.py").write_text("import sys\ndef register(registry):\n    sys.exit(0)\n")
            sys.modules.pop("t3compose.evidence", None)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["version"])
            self.assertEqual((code, out.getvalue()), (2, ""))
            self.assertIn("evidence", err.getvalue())

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
    def assert_launch_refused(self, launch, marker):
        with self.assertRaisesRegex(AssertionError, "offline guard"):
            result = launch()
            if hasattr(result, "communicate"):
                result.communicate()
            elif isinstance(result, int):
                os.waitpid(result, 0)
        self.assertFalse(marker.exists())

    def test_python_executable_ignores_custom_argv_zero(self):
        marker = self.temp / "marker"
        payload = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        argv = [str(ROOT / "tests/support.py"), "-c", payload]
        for launch in (lambda: subprocess.run(argv, executable=sys.executable),
                       lambda: subprocess.Popen(argv, -1, sys.executable),
                       lambda: os.posix_spawn(sys.executable, argv, os.environ),
                       lambda: os.posix_spawnp(sys.executable, argv, os.environ)):
            with self.subTest(launch=launch):
                self.assert_launch_refused(launch, marker)

    def test_relative_bootstrap_with_decoy_child_cwd_is_refused(self):
        marker = self.temp / "marker"
        decoy = self.temp / "tests/support.py"
        decoy.parent.mkdir()
        decoy.write_text(f"from pathlib import Path; Path({str(marker)!r}).touch()\n")
        self.assert_launch_refused(
            lambda: subprocess.run([sys.executable, "-I", "-S", "-B",
                                    "tests/support.py", "unused"], cwd=self.temp), marker)

    def test_env_launchers_are_refused(self):
        marker = self.temp / "marker"
        gh = self.temp / "gh"
        gh.write_text(f"#!/bin/sh\necho ran > {marker}\n")
        gh.chmod(0o755)
        payload = f"from pathlib import Path; Path({str(marker)!r}).touch()"
        for command in ([sys.executable, "-I", "-S", "-B", "-c", payload], [str(gh)]):
            for env in ("/usr/bin/env", "env"):
                argv = [env, *command]
                for launch in (lambda: subprocess.run(argv),
                               lambda: os.posix_spawn("/usr/bin/env", argv, os.environ),
                               lambda: os.posix_spawnp("env", argv, os.environ)):
                    with self.subTest(argv=argv, launch=launch):
                        self.assert_launch_refused(launch, marker)

    def test_program_requires_explicit_per_test_allowance(self):
        with self.assertRaisesRegex(AssertionError, "offline guard"):
            subprocess.run(["/bin/echo", "synthetic"])
        self.allow_program("/bin/echo")
        result = subprocess.run(["/bin/echo", "synthetic"], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (0, "synthetic\n"))

        # A new test case's guard does not inherit this test's extra program.
        other = OfflineTestCase()
        try:
            other.setUp()
            with self.assertRaisesRegex(AssertionError, "offline guard"):
                subprocess.run(["/bin/echo", "synthetic"])
        finally:
            other.doCleanups()

    def test_allowance_refuses_blocked_names_env_and_python(self):
        for name in ("security", "gh", "ssh", "curl", "sh", "bash", "zsh", "dash", "ksh", "fish",
                     "claude", "codex", "agy", "pi",
                     "env", "python", "python3", "python3.14"):
            path = self.temp / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, "offline guard"):
                self.allow_program(path)
        for name, target in (("interpreter-alias", sys.executable), ("env-alias", "/usr/bin/env"),
                             ("shell-alias", "/bin/sh")):
            alias = self.temp / name
            alias.symlink_to(target)
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, "offline guard"):
                self.allow_program(alias)

    def test_program_resolution_uses_child_path_and_cwd(self):
        marker = self.temp / "marker"
        decoy = self.temp / "git"
        decoy.write_text(f"#!/bin/sh\necho ran > {marker}\n")
        decoy.chmod(0o755)
        for launch in (
                lambda: subprocess.run(["git"], env={"PATH": str(self.temp)}),
                lambda: subprocess.run(["git"], cwd=self.temp, env={"PATH": "."}),
                lambda: subprocess.run(["./git"], cwd=self.temp),
                lambda: subprocess.Popen(["git"], -1, None, None, None, None, None,
                                         True, False, str(self.temp), {"PATH": "."})):
            self.assert_launch_refused(launch, marker)
        with patch.dict(os.environ, {"PATH": str(self.temp)}):
            self.assert_launch_refused(
                lambda: os.posix_spawnp("git", ["git"], os.environ), marker)

    def test_strings_bytes_and_positional_shell_are_refused(self):
        for command in ("git --version", b"git --version"):
            with self.assertRaisesRegex(AssertionError, "offline guard"):
                subprocess.run(command)
        with self.assertRaisesRegex(AssertionError, "offline guard"):
            subprocess.Popen(["git", "--version"], -1, None, None, None, None, None, True, True)

    def test_bootstrap_child_refuses_custom_argv_zero_and_env(self):
        marker = self.temp / "marker"
        gh = self.temp / "gh"
        gh.write_text(f"#!/bin/sh\necho ran > {marker}\n")
        gh.chmod(0o755)
        child = self.temp / "child.py"
        child.write_text(textwrap.dedent(f"""\
            import os
            from pathlib import Path
            import subprocess
            import sys
            import unittest
            check = unittest.TestCase()
            marker = Path({str(marker)!r})
            payload = "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()"
            argv = [{str(ROOT / 'tests/support.py')!r}, '-c', payload]
            launches = [
                lambda: subprocess.run(argv, executable=sys.executable),
                lambda: subprocess.Popen(argv, -1, sys.executable),
                lambda: os.posix_spawn(sys.executable, argv, os.environ),
                lambda: os.posix_spawnp(sys.executable, argv, os.environ),
                lambda: subprocess.run(['/usr/bin/env', sys.executable, '-I', '-S', '-B', '-c', payload]),
                lambda: subprocess.run(['/usr/bin/env', {str(gh)!r}]),
                lambda: os.posix_spawnp('env', ['env', sys.executable, '-c', payload], os.environ),
            ]
            for launch in launches:
                with check.assertRaisesRegex(AssertionError, 'offline guard'):
                    result = launch()
                    if hasattr(result, 'communicate'):
                        result.communicate()
                    elif isinstance(result, int):
                        os.waitpid(result, 0)
                check.assertFalse(marker.exists())
            # The child's allowlist admits git and a guarded grandchild too.
            result = subprocess.run(['git', '--version'], capture_output=True, text=True)
            check.assertEqual(result.returncode, 0)
            check.assertIn('git version', result.stdout)
            if len(sys.argv) == 1:
                subprocess.run([sys.executable, '-I', '-S', '-B',
                                {str(ROOT / 'tests/support.py')!r}, __file__, 'grandchild'], check=True)
            """))
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
                                 str(ROOT / "tests/support.py"), str(child)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

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
        # Synthetic paths only: if the guard broke, a bare harness name would
        # start a real agent.
        for harness in ("claude", "codex", "agy", "pi"):
            for args in (["/synthetic/bin/" + harness], ["env", "/synthetic/bin/" + harness]):
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

    def test_offline_guard_regression_coverage(self):
        marker = self.temp / "marker.txt"
        for name in ("security", "gh", "ssh", "curl"):
            exe = self.temp / name
            exe.write_text(f"#!/bin/sh\necho ran > {marker}\n")
            exe.chmod(0o755)

        # os.posix_spawn and os.posix_spawnp
        for name in ("security", "gh", "ssh", "curl"):
            exe = str(self.temp / name)
            with self.assertRaises(AssertionError):
                os.posix_spawn(exe, [exe], os.environ)
            self.assertFalse(marker.exists())
            with self.assertRaises(AssertionError):
                os.posix_spawnp(exe, [exe], os.environ)
            self.assertFalse(marker.exists())

        # os.spawnv, and one os.exec* function
        with self.assertRaises(AssertionError):
            os.spawnv(os.P_WAIT, str(self.temp / "security"), [str(self.temp / "security")])
        self.assertFalse(marker.exists())
        with self.assertRaises(AssertionError):
            os.execv(str(self.temp / "security"), [str(self.temp / "security")])
        self.assertFalse(marker.exists())

        # subprocess.Popen with the program as the third positional argument
        with self.assertRaises(AssertionError):
            subprocess.Popen(["synthetic"], -1, str(self.temp / "security"))
        self.assertFalse(marker.exists())

        # a Python child started without the bootstrap
        with self.assertRaises(AssertionError):
            subprocess.run([sys.executable, "-c", "pass"])
        self.assertFalse(marker.exists())
        script = self.temp / "script.py"
        script.write_text("pass\n")
        with self.assertRaises(AssertionError):
            subprocess.run([sys.executable, "-I", "-S", str(script)])
        self.assertFalse(marker.exists())

        # a Python child started through the bootstrap that tries to start the synthetic gh,
        # to create a socket, and to call urllib.request.urlopen on a data: URL (each refused in child)
        child_code = (
            "import socket\n"
            "import subprocess\n"
            "import sys\n"
            "import urllib.request\n\n"
            f"gh_path = {repr(str(self.temp / 'gh'))}\n"
            "try:\n"
            "    subprocess.run([gh_path])\n"
            "except AssertionError:\n"
            "    pass\n"
            "else:\n"
            "    sys.exit(10)\n\n"
            "try:\n"
            "    socket.socket()\n"
            "except AssertionError:\n"
            "    pass\n"
            "else:\n"
            "    sys.exit(11)\n\n"
            "try:\n"
            "    urllib.request.urlopen('data:text/plain,synthetic')\n"
            "except AssertionError:\n"
            "    pass\n"
            "else:\n"
            "    sys.exit(12)\n\n"
            "sys.exit(0)\n"
        )
        child_script = self.temp / "child_runner.py"
        child_script.write_text(child_code)
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(ROOT / "tests/support.py"), str(child_script)],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"Child failed: {result.stderr}")
        self.assertFalse(marker.exists())

        # an allowed program, git --version, still runs through subprocess.run and through os.posix_spawn
        git_res = subprocess.run(["git", "--version"], capture_output=True, text=True)
        self.assertEqual(git_res.returncode, 0)
        self.assertIn("git version", git_res.stdout)

        git_path = shutil.which("git") or "/usr/bin/git"
        null_fd = os.open(os.devnull, os.O_WRONLY)
        try:
            pid = os.posix_spawn(git_path, [git_path, "--version"], os.environ,
                                 file_actions=[(os.POSIX_SPAWN_DUP2, null_fd, 1)])
            _, status = os.waitpid(pid, 0)
        finally:
            os.close(null_fd)
        exit_code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else os.WEXITSTATUS(status)
        self.assertEqual(exit_code, 0)
