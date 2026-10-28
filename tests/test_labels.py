import unittest

from tests.support import OfflineTestCase, ROOT


def label_errors(classes):
    return [cls.__name__ for cls in classes
            if not issubclass(cls, OfflineTestCase) or
            not cls.__name__.startswith(("Offline", "Simulated"))]


def test_classes(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_classes(item)
        else:
            yield type(item)


class OfflineLabels(OfflineTestCase):
    def test_discovered_classes_have_guard_and_label(self):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
        self.assertEqual(label_errors(set(test_classes(suite))), [])

    def test_rejects_missing_guard_and_missing_label(self):
        unguarded = type("OfflineUnguarded", (unittest.TestCase,), {})
        unlabeled = type("Unlabeled", (OfflineTestCase,), {})
        simulated = type("SimulatedQuota", (OfflineTestCase,), {})
        self.assertEqual(label_errors([unguarded, unlabeled, simulated]),
                         ["OfflineUnguarded", "Unlabeled"])


class OfflineLifecycle(OfflineTestCase):
    def test_run_protects_setup_without_super_and_restores_nested_context(self):
        import os
        from pathlib import Path
        import socket
        import subprocess
        import urllib.request
        from t3compose.paths import local_dir

        outer_temp, outer_cwd = self.temp, Path.cwd()
        outer_env, outer_local = dict(os.environ), local_dir()
        outer_socket, outer_popen = socket.socket, subprocess.Popen
        seen = []
        owner = self

        class OfflineNested(OfflineTestCase):
            def setUp(self):
                if self._testMethodName == "test_super_setup":
                    super().setUp()
                owner.assertTrue(hasattr(self, "temp"), "run must prepare temp before setUp")
                seen.append(self.temp)
                owner.assertNotEqual(self.temp, outer_temp)
                owner.assertEqual(self.temp, self.temp.resolve())
                owner.assertNotIn(ROOT, self.temp.parents)
                owner.assertEqual(Path.cwd(), self.temp / "work")
                owner.assertEqual(local_dir(), self.temp / ".local")
                owner.assertEqual(Path(os.environ["HOME"]).parent, self.temp)
                owner.assertEqual(Path(os.environ["XDG_CONFIG_HOME"]).parent, self.temp)
                for key, value in (("GIT_CONFIG_NOSYSTEM", "1"),
                                   ("GIT_TERMINAL_PROMPT", "0"),
                                   ("GIT_ALLOW_PROTOCOL", "file"),
                                   ("GIT_CEILING_DIRECTORIES", str(self.temp))):
                    owner.assertEqual(os.environ[key], value)
                owner.assertEqual(Path(os.environ["GIT_CONFIG_GLOBAL"]).read_text(), "")
                local_dir().mkdir()
                (local_dir() / "synthetic.json").write_text('{"safe":true}')
                owner.assertEqual((local_dir() / "synthetic.json").read_text(), '{"safe":true}')
                for call in (socket.socket,
                             lambda: urllib.request.urlopen("data:text/plain,synthetic"),
                             lambda: subprocess.run(["/synthetic/bin/gh"])):
                    with owner.assertRaisesRegex(AssertionError, "offline guard"):
                        call()
                self.addCleanup(lambda: owner.assertEqual(local_dir(), self.temp / ".local"))
                if self._testMethodName == "test_setup_failure":
                    raise RuntimeError("synthetic setup failure")

            def test_success(self):
                pass

            def test_super_setup(self):
                pass

            def test_cleanup_failure(self):
                def fail_cleanup():
                    raise RuntimeError("synthetic cleanup failure")
                self.addCleanup(fail_cleanup)

            def test_setup_failure(self):
                owner.fail("failed setup must not execute body")

            def test_body_failure(self):
                raise RuntimeError("synthetic body failure")

            def test_teardown_failure(self):
                pass

            def tearDown(self):
                if self._testMethodName == "test_teardown_failure":
                    raise RuntimeError("synthetic teardown failure")

        for count, (method, errors) in enumerate((("test_success", 0),
                ("test_setup_failure", 1), ("test_body_failure", 1),
                ("test_teardown_failure", 1), ("test_cleanup_failure", 1),
                ("test_super_setup", 0)), 1):
            result = unittest.TestResult()
            OfflineNested(method).run(result)
            self.assertEqual(result.failures, [])
            self.assertEqual(len(result.errors), errors)
            self.assertEqual(len(seen), count)
            self.assertFalse(seen[-1].exists())
            self.assertEqual(Path.cwd(), outer_cwd)
            self.assertEqual(dict(os.environ), outer_env)
            self.assertEqual(local_dir(), outer_local)
            self.assertIs(socket.socket, outer_socket)
            self.assertIs(subprocess.Popen, outer_popen)
        self.assertTrue(outer_temp.exists())

    def test_git_environment_is_scrubbed_and_uninitialized_fixture_cannot_escape(self):
        import os
        from pathlib import Path
        import subprocess
        from unittest.mock import patch

        self.assertNotIn(ROOT, self.temp.parents, "fixtures must be outside checkout")
        # Every repository and configuration here is synthetic, including the
        # repository enclosing the nested context's work directory.
        decoy = self.temp / "decoy"
        decoy.mkdir()
        config = decoy / "config"
        config.write_text('[synthetic]\n    inherited = unsafe\n[credential]\n    helper = synthetic-unused\n')
        inherited = {"GIT_DIR": str(decoy / ".git"), "GIT_WORK_TREE": str(decoy),
                     "GIT_INDEX_FILE": str(decoy / "index"),
                     "GIT_CONFIG": str(config), "GIT_CONFIG_GLOBAL": str(config),
                     "GIT_CONFIG_SYSTEM": str(config), "GIT_CONFIG_NOSYSTEM": "0",
                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "synthetic.injected",
                     "GIT_CONFIG_VALUE_0": "unsafe", "GIT_CONFIG_PARAMETERS": "invalid",
                     "GIT_ASKPASS": str(decoy / "askpass"), "GIT_SSH_COMMAND": "synthetic",
                     "SSH_AUTH_SOCK": str(decoy / "socket"), "HOME": str(decoy),
                     "XDG_CONFIG_HOME": str(decoy)}
        owner = self

        class OfflineGitContext(OfflineTestCase):
            def test_baseline(self):
                for key in inherited:
                    if key not in {"HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"}:
                        owner.assertNotIn(key, os.environ)
                subprocess.run(["git", "init", str(self.temp)], check=True, capture_output=True)
                before = (self.temp / ".git/config").read_bytes()
                result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True)
                owner.assertNotEqual(result.returncode, 0)
                # A misplaced write is tested against a scratch repository only.
                result = subprocess.run(["git", "config", "--local", "synthetic.write", "bad"],
                                        capture_output=True)
                owner.assertNotEqual(result.returncode, 0)
                owner.assertEqual((self.temp / ".git/config").read_bytes(), before)
                for key in ("synthetic.inherited", "synthetic.injected", "credential.helper"):
                    result = subprocess.run(["git", "config", "--get", key], capture_output=True)
                    owner.assertEqual((result.returncode, result.stdout), (1, b""))
                # Git refuses the transport before any native network operation.
                result = subprocess.run(["git", "ls-remote", "https://example.invalid/synthetic"],
                                        capture_output=True, text=True)
                owner.assertNotEqual(result.returncode, 0)
                owner.assertIn("transport 'https' not allowed", result.stderr)

        with patch.dict(os.environ, inherited):
            result = unittest.TestResult()
            OfflineGitContext("test_baseline").run(result)
            self.assertEqual((result.errors, result.failures), ([], []))
            for key, value in inherited.items():
                self.assertEqual(os.environ[key], value)

    def test_default_state_and_environment_in_bootstrap_and_explicit_child_env(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        import textwrap
        from t3compose.paths import local_dir

        self.assertNotEqual(local_dir(), ROOT / ".local", "default state must be redirected")
        root_local_existed = (ROOT / ".local").exists()
        local_dir().mkdir()
        (local_dir() / "synthetic.json").write_text('"parent"')
        child = self.temp / "state_child.py"
        child.write_text(textwrap.dedent('''\
            import contextlib, io, json, os, subprocess, sys
            from pathlib import Path
            from t3compose import cli
            from t3compose.paths import ROOT, local_dir
            def synthetic_state_handler(args):
                args.local_dir.mkdir()
                (args.local_dir / 'synthetic.json').write_text('"child"')
                return {'state': str(args.local_dir)}
            cli.version = synthetic_state_handler
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                assert cli.main(['version']) == 0
            state = Path(json.loads(output.getvalue())['state'])
            assert state == local_dir()
            assert (state / 'synthetic.json').read_text() == '"child"'
            assert state != ROOT / '.local'
            assert Path.cwd() == state.parent / 'work'
            assert Path(os.environ['HOME']).parent == state.parent
            assert os.environ['GIT_ALLOW_PROTOCOL'] == 'file'
            output = {'state': str(state), 'root': str(ROOT)}
            if len(sys.argv) == 1:
                result = subprocess.run([sys.executable, '-I', '-S', '-B',
                    str(ROOT / 'tests/support.py'), __file__, 'grandchild'],
                    check=True, capture_output=True, text=True, env={})
                output['grandchild'] = json.loads(result.stdout)['state']
                assert not Path(output['grandchild']).exists()
            print(json.dumps(output))
            '''))
        result = subprocess.run([sys.executable, "-I", "-S", "-B",
                                 str(ROOT / "tests/support.py"), str(child)],
                                check=True, capture_output=True, text=True, env={})
        data = json.loads(result.stdout)
        self.assertEqual(data["root"], str(ROOT))
        self.assertEqual(len({data["state"], data["grandchild"], str(local_dir())}), 3)
        self.assertFalse(Path(data["state"]).exists())
        self.assertEqual((local_dir() / "synthetic.json").read_text(), '"parent"')
        self.assertEqual((ROOT / ".local").exists(), root_local_existed)


    def test_explicit_environments_keep_baseline_on_every_supported_launch(self):
        import os
        from pathlib import Path
        import shutil
        import subprocess

        program = shutil.which("printenv")
        self.assertIsNotNone(program)
        self.allow_program(program)
        keys = ["HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM",
                "GIT_TERMINAL_PROMPT", "GIT_ALLOW_PROTOCOL", "GIT_CEILING_DIRECTORIES"]
        # The system printenv accepts one name per invocation.
        for key in keys:
            with self.subTest(variable=key):
                result = subprocess.run([program, key], env={}, capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stdout), (0, os.environ[key] + "\n"))
        argv, expected = [program, "GIT_ALLOW_PROTOCOL"], ["file"]
        positional = subprocess.Popen(argv, -1, None, None, subprocess.PIPE, subprocess.PIPE,
                                      None, True, False, str(self.temp), {})
        out, err = positional.communicate()
        self.assertEqual((positional.returncode, out.decode().splitlines(), err),
                         (0, expected, b""))
        for spawn, executable in ((os.posix_spawn, program), (os.posix_spawnp, Path(program).name)):
            with self.subTest(spawn=spawn.__name__):
                output = self.temp / "environment-output"
                with output.open("wb") as stream:
                    pid = spawn(executable, argv, {}, file_actions=[
                        (os.POSIX_SPAWN_DUP2, stream.fileno(), 1)])
                    _, status = os.waitpid(pid, 0)
                self.assertEqual(status, 0)
                self.assertEqual(output.read_text().splitlines(), expected)
        # Intentional synthetic overrides are supported for later decoy tests.
        decoy = self.temp / "decoy-home"
        decoy.mkdir()
        result = subprocess.run([program, "HOME"],
                                env={"HOME": str(decoy)}, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines(), [str(decoy)])
