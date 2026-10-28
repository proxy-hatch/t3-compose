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

from tests.support import AllowedPrograms, OfflineTestCase, ROOT


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
        self.assertIsInstance(data["dirty"], bool)
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

    def test_outcome_nonzero_structured_data_and_invalid_codes(self):
        import t3compose
        from t3compose.cli import CommandError, ExitCode, Outcome, PassThrough, main
        for bad_code in (True, False, 6, 70, -1, "1", None):
            with self.subTest(bad_outcome_code=bad_code), self.assertRaises(ValueError):
                Outcome({"ok": False}, bad_code)
        for bad_err_code in (0, ExitCode.SUCCESS, True, False, 6, 70, -1, "2", None):
            with self.subTest(bad_error_code=bad_err_code), self.assertRaises(ValueError):
                CommandError("public message", bad_err_code)
        for bad_status in (True, False, 256, -128, -200, "0", None):
            with self.subTest(bad_passthrough=bad_status), self.assertRaises(ValueError):
                PassThrough(bad_status)
        self.assertEqual(PassThrough(0).status, 0)
        self.assertEqual(PassThrough(19).status, 19)
        self.assertEqual(PassThrough(-15).status, 143)
        self.assertEqual(PassThrough(-9).status, 137)

        (self.temp / "evidence.py").write_text(textwrap.dedent("""\
            import sys
            from t3compose.cli import ExitCode, Outcome
            def register(registry):
                p = registry.group("evidence").add_parser("check")
                p.add_argument("code", type=int)
                def check(args):
                    if args.code == 99:
                        return Outcome({"bad": float("nan")}, ExitCode.CHECK_FAILED)
                    print("public warning on stderr", file=sys.stderr)
                    return Outcome({"verified": False, "gaps": ["missing-review"]}, args.code)
                p.set_defaults(handler=check)
            """))
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            for code in (0, 1, 2, 3, 4, 5, 75):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = main(["evidence", "check", str(code)])
                self.assertEqual(status, code)
                self.assertEqual(json.loads(out.getvalue()),
                                 {"verified": False, "gaps": ["missing-review"]})
                self.assertEqual(err.getvalue(), "public warning on stderr\n")

            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = main(["--text", "evidence", "check", "4"])
            self.assertEqual(status, 4)
            self.assertEqual(out.getvalue(), 'verified: false\ngaps: ["missing-review"]\n')

            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                status = main(["evidence", "check", "99"])
            self.assertEqual(status, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertIn("ValueError", err.getvalue())

    def test_passthrough_restricted_to_publish_run(self):
        import t3compose
        from t3compose.cli import main
        (self.temp / "token_run.py").write_text(textwrap.dedent("""\
            from t3compose.cli import PassThrough
            def register(registry):
                p = registry.group("publish").add_parser("run")
                p.add_argument("status", type=int)
                p.set_defaults(handler=lambda args: PassThrough(args.status))
                other = registry.group("publish").add_parser("check")
                other.set_defaults(handler=lambda args: PassThrough(0))
            """))
        (self.temp / "heavy.py").write_text(textwrap.dedent("""\
            from t3compose.cli import PassThrough
            def register(registry):
                p = registry.group("heavy").add_parser("run")
                p.set_defaults(handler=lambda args: PassThrough(7))
            """))
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            for raw, expected in ((0, 0), (17, 17), (-15, 143)):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = main(["publish", "run", str(raw)])
                self.assertEqual((status, out.getvalue(), err.getvalue()), (expected, "", ""))

            for bad_cmd in (["publish", "check"], ["heavy", "run"]):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = main(bad_cmd)
                self.assertEqual(status, 2)
                self.assertEqual(out.getvalue(), "")
                self.assertNotEqual(err.getvalue(), "")

    def test_global_text_and_local_dir_isolation_from_leaf_options(self):
        import t3compose
        from t3compose.cli import main
        (self.temp / "privacy.py").write_text(textwrap.dedent("""\
            import argparse
            def register(registry):
                p = registry.group("privacy").add_parser("check")
                p.add_argument("--text", action="append", default=None)
                p.add_argument("--label", default="default")
                p.set_defaults(handler=lambda args: {
                    "leaf_text": args.text,
                    "label": args.label,
                    "local": str(args.local_dir),
                })
                r = registry.group("publish").add_parser("exec")
                r.add_argument("--status-file", required=True)
                r.add_argument("child_argv", nargs=argparse.REMAINDER)
                r.set_defaults(handler=lambda args: {
                    "status_file": args.status_file,
                    "child_argv": args.child_argv,
                })
            """))
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            def invoke(args):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = main(args)
                    except SystemExit as exc:
                        code = exc.code
                return code, out.getvalue(), err.getvalue()

            # Leaf --text does not enable global text mode.
            code, out, err = invoke(["privacy", "check", "--text", "title=/tmp/t"])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["leaf_text"], ["title=/tmp/t"])

            # Leaf default None for --text does not clear global --text.
            code, out, err = invoke(["--text", "privacy", "check"])
            self.assertEqual((code, err), (0, ""))
            self.assertTrue(out.startswith("leaf_text: null\n"))

            # Option value equal to "--text" does not switch output or help to text mode.
            code, out, err = invoke(["privacy", "check", "--label=--text"])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["label"], "--text")

            code, out, err = invoke(["privacy", "check", "--label=--text", "--help"])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("usage: t3c privacy check", json.loads(out)["help"])

            # Leaf "--text" followed by "--help" stays JSON help when root --text was not given.
            code, out, err = invoke(["privacy", "check", "--text", "title=/tmp/t", "--help"])
            self.assertEqual((code, err), (0, ""))
            self.assertIn("usage: t3c privacy check", json.loads(out)["help"])

            # Root --text before command makes subcommand help plain text.
            code, out, err = invoke(["--text", "version", "--help"])
            self.assertEqual((code, err), (0, ""))
            self.assertTrue(out.startswith("usage: t3c version"))

            # Global --text after a command that does not declare --text fails as invalid arguments.
            code, out, err = invoke(["version", "--text"])
            self.assertEqual((code, out), (2, ""))
            self.assertIn("invalid arguments for t3c; use --help", err)

            # argparse.REMAINDER retains leading "--" and preserves "--text" in child argv.
            code, out, err = invoke(["publish", "exec", "--status-file", "/tmp/s",
                                     "--", "git", "commit", "-m", "--text"])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out), {
                "status_file": "/tmp/s",
                "child_argv": ["--", "git", "commit", "-m", "--text"],
            })

    def test_no_abbreviations_and_registration_validation_and_command_names(self):
        import t3compose
        from t3compose.cli import CommandError, build_parser, command_names, main
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            self.assertEqual(command_names(), (("version",),))
            parser = build_parser()
            self.assertIs(parser.allow_abbrev, False)

            (self.temp / "routing.py").write_text(textwrap.dedent("""\
                def register(registry):
                    g = registry.group("route")
                    g.add_parser("select").set_defaults(handler=lambda args: {})
                    g.add_parser("input").set_defaults(handler=lambda args: {})
                """))
            (self.temp / "jev.py").write_text(textwrap.dedent("""\
                def register(registry):
                    g = registry.group("route")
                    p = g.add_parser("classify")
                    p.add_argument("--work", required=True)
                    p.set_defaults(handler=lambda args: {"work": args.work})
                """))
            self.assertEqual(command_names(), (
                ("route", "classify"),
                ("route", "input"),
                ("route", "select"),
                ("version",),
            ))

            # Abbreviations are rejected at root, group and leaf levels.
            for bad_args, expected_prog in (
                (["--tex", "version"], "t3c"),
                (["--local-di", str(self.temp), "version"], "t3c"),
                (["route", "classify", "--wor", "W1"], "t3c route classify"),
            ):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(bad_args)
                self.assertEqual((code, out.getvalue()), (2, ""))
                self.assertIn("invalid arguments for " + expected_prog + "; use --help", err.getvalue())
                self.assertNotIn("W1", err.getvalue())

            # Invalid registrations fail deterministically across Python versions.
            bad_modules = (
                # Duplicate leaf in shared group across modules
                "def register(registry):\n"
                "    registry.group('route').add_parser('select').set_defaults(handler=lambda a: {})\n",
                # Group colliding with version
                "def register(registry):\n"
                "    registry.group('version').add_parser('sub').set_defaults(handler=lambda a: {})\n",
                # Root leaf colliding with version
                "def register(registry):\n"
                "    registry.commands.add_parser('version').set_defaults(handler=lambda a: {})\n",
                # Root leaf colliding with existing group
                "def register(registry):\n"
                "    registry.commands.add_parser('route').set_defaults(handler=lambda a: {})\n",
                # Group colliding with root leaf
                "def register(registry):\n"
                "    registry.commands.add_parser('solo').set_defaults(handler=lambda a: {})\n"
                "    registry.group('solo').add_parser('sub').set_defaults(handler=lambda a: {})\n",
                # Missing handler on leaf
                "def register(registry):\n"
                "    registry.group('route').add_parser('broken')\n",
                # Noncallable handler on leaf
                "def register(registry):\n"
                "    registry.group('route').add_parser('broken').set_defaults(handler='not-callable')\n",
                # Reserved destination local_dir
                "def register(registry):\n"
                "    p = registry.group('route').add_parser('broken')\n"
                "    p.add_argument('--local-dir')\n"
                "    p.set_defaults(handler=lambda a: {})\n",
                # Reserved default local_dir
                "def register(registry):\n"
                "    p = registry.group('route').add_parser('broken')\n"
                "    p.set_defaults(local_dir='/tmp', handler=lambda a: {})\n",
                # Reserved prefix t3c_ in argument dest
                "def register(registry):\n"
                "    p = registry.group('route').add_parser('broken')\n"
                "    p.add_argument('--t3c-secret')\n"
                "    p.set_defaults(handler=lambda a: {})\n",
                # Reserved prefix t3c_ in set_defaults
                "def register(registry):\n"
                "    p = registry.group('route').add_parser('broken')\n"
                "    p.set_defaults(t3c_text=True, handler=lambda a: {})\n",
            )
            for idx, source in enumerate(bad_modules):
                with self.subTest(bad_module_index=idx):
                    (self.temp / "briefs.py").write_text(source)
                    sys.modules.pop("t3compose.briefs", None)
                    with self.assertRaises(CommandError) as ctx:
                        command_names()
                    self.assertEqual(ctx.exception.code, 2)
                    self.assertIn("cannot load command module: briefs", str(ctx.exception))

    def _invoke_corrections_module(self, source, argv):
        import importlib
        import t3compose
        from t3compose.cli import main
        (self.temp / "config.py").write_text(source)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            sys.modules.pop("t3compose.config", None)
            importlib.invalidate_caches()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_corrections_reserved_registration_at_every_level(self):
        cases = (
            ("registry.parser.set_defaults(t3c_text=True)", ""),
            ("registry.parser.set_defaults(t3c_local_dir='synthetic-redirect')", ""),
            ("registry.parser.add_argument('--collision', dest='t3c_text')", ""),
            ("registry.parser.add_argument('--collision', dest='t3c_local_dir')", ""),
            ("registry.parser.add_argument('--collision', dest='t3c_command')", ""),
            ("", "registry.commands.choices['sample'].add_argument('--collision', dest='t3c_sample_command')"),
            ("", "registry.commands.choices['sample'].set_defaults(t3c_path=('other',))"),
            ("", "p.set_defaults(t3c_path=('other',))"),
            ("registry.parser.add_argument('--handler')", ""),
            ("", "registry.commands.choices['sample'].add_argument('--handler')"),
            ("", "p.add_argument('--handler')"),
        )
        for before, after in cases:
            source = ("def register(registry):\n" +
                      ("    " + before + "\n" if before else "") +
                      "    p = registry.group('sample').add_parser('show')\n" +
                      ("    " + after + "\n" if after else "") +
                      "    p.set_defaults(handler=lambda args: {'ok': True})\n")
            with self.subTest(before=before, after=after):
                code, out, err = self._invoke_corrections_module(source, ['sample', 'show'])
                self.assertEqual((code, out), (2, ''))
                self.assertIn('cannot load command module: config', err)
                self.assertNotIn('synthetic-redirect', err)
        # Ordinary module defaults and callable handlers remain supported.
        code, out, err = self._invoke_corrections_module(
            "def register(registry):\n"
            "    p = registry.group('sample').add_parser('show')\n"
            "    p.set_defaults(label='synthetic', handler=lambda a: {'label': a.label})\n",
            ['sample', 'show'])
        self.assertEqual((code, json.loads(out), err), (0, {'label': 'synthetic'}, ''))

    def test_corrections_aliases_are_rejected_without_changing_canonical_paths(self):
        cases = (
            ("def register(registry):\n"
             "    p = registry.commands.add_parser('other', aliases=['version'])\n"
             "    p.set_defaults(handler=lambda a: {'replaced': True})\n", ['version']),
            ("def register(registry):\n"
             "    p = registry.commands.add_parser('other', aliases=['alternate'])\n"
             "    p.set_defaults(handler=lambda a: {})\n", ['other']),
            ("from t3compose.cli import PassThrough\n"
             "def register(registry):\n"
             "    p = registry.group('publish').add_parser('run', aliases=['execute'])\n"
             "    p.set_defaults(handler=lambda a: PassThrough(7))\n", ['publish', 'run']),
        )
        for source, argv in cases:
            with self.subTest(argv=argv):
                code, out, err = self._invoke_corrections_module(source, argv)
                self.assertEqual((code, out), (2, ''))
                self.assertIn('cannot load command module: config', err)
        source = ("from t3compose.cli import PassThrough\n"
                  "def register(registry):\n"
                  "    p = registry.group('publish').add_parser('run')\n"
                  "    p.set_defaults(handler=lambda a: PassThrough(7))\n")
        self.assertEqual(self._invoke_corrections_module(source, ['publish', 'run']), (7, '', ''))

    def test_corrections_text_keys_cannot_forge_output_lines(self):
        source = ("def register(registry):\n"
                  "    p = registry.group('sample').add_parser('show')\n"
                  "    p.set_defaults(handler=lambda a: {"
                  "'note\\nverified': 'forged', 'tab\\tkey': 'value', "
                  "'split\\u2028key': 'separator', 'ok': False})\n")
        code, out, err = self._invoke_corrections_module(source, ['--text', 'sample', 'show'])
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out, 'note\\nverified: "forged"\ntab\\tkey: "value"\n'
                             'split\\u2028key: "separator"\nok: false\n')
        self.assertEqual(len(out.splitlines()), 4)

    def test_helper_info_preserves_git_index_and_version_failure_is_sanitized(self):
        from t3compose import cli
        from t3compose.cli import helper_info, main

        repo = self.temp / "scratch-repo"
        repo.mkdir()
        git_env = {
            **os.environ,
            "GIT_CEILING_DIRECTORIES": str(self.temp),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "Synthetic Tester",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Tester",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
        }
        subprocess.run(["git", "init", str(repo)], check=True, env=git_env,
                       capture_output=True, text=True)
        tracked = repo / "tracked.txt"
        tracked.write_text("clean content\n")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True,
                       env=git_env, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True,
                       env=git_env, capture_output=True, text=True)
        expected_head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                       check=True, env=git_env,
                                       capture_output=True, text=True).stdout.strip()

        # Touch tracked file's mtime without changing content so a non-read-only status
        # would refresh and replace .git/index.
        index_path = repo / ".git/index"
        stat_before = index_path.stat()
        bytes_before = index_path.read_bytes()
        os.utime(tracked, (stat_before.st_atime + 60, stat_before.st_mtime + 60))

        with patch.object(cli, "ROOT", repo), patch.dict(os.environ, git_env, clear=True):
            info = helper_info()
            self.assertEqual(info, {
                "root": str(repo),
                "commit": expected_head,
                "dirty": False,
                "python": sys.version.split()[0],
            })
            stat_after = index_path.stat()
            self.assertEqual(stat_after.st_ino, stat_before.st_ino)
            self.assertEqual(stat_after.st_mtime_ns, stat_before.st_mtime_ns)
            self.assertEqual(index_path.read_bytes(), bytes_before)

            # Untracked file makes dirty True while still not rewriting .git/index.
            (repo / "untracked.txt").write_text("untracked\n")
            self.assertIs(helper_info()["dirty"], True)
            self.assertEqual(index_path.stat().st_ino, stat_before.st_ino)

            # When Git fails inside version/helper_info, main sanitizes the error,
            # names the command path and exception class, and suppresses stderr/cmd details.
            not_a_repo = self.temp / "not-a-repo-synthetic-secret"
            not_a_repo.mkdir()
            out, err = io.StringIO(), io.StringIO()
            with patch.object(cli, "ROOT", not_a_repo), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(["version"])
            self.assertEqual(code, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(
                err.getvalue(),
                "t3c version: command failed (ProcessError); check local inputs and helper installation\n",
            )
            self.assertNotIn("not-a-repo-synthetic-secret", err.getvalue())

    def test_unexpected_exception_encoding_text_modes_and_local_dir_resolution(self):
        import t3compose
        from t3compose.cli import main
        from t3compose.paths import local_dir

        (self.temp / "config.py").write_text(textwrap.dedent("""\
            from t3compose.cli import ExitCode, Outcome
            def register(registry):
                g = registry.group("sample")
                g.add_parser("boom").set_defaults(handler=boom)
                g.add_parser("bad-json").set_defaults(handler=lambda a: {"bad": object()})
                g.add_parser("bad-surrogate").set_defaults(handler=lambda a: {"s": "\\ud800"})
                g.add_parser("newlines").set_defaults(handler=lambda a: Outcome({
                    "note": "line1\\nverified: True",
                    "nested": {"k": "v\\n2"},
                    "ok": False,
                }, ExitCode.CHECK_FAILED))
                g.add_parser("list-data").set_defaults(handler=lambda a: ["a", "b\\nc", 1])
                g.add_parser("none-data").set_defaults(handler=lambda a: None)
                p = g.add_parser("local")
                p.add_argument("secret_arg", nargs="?", default="unused")
                p.set_defaults(handler=lambda a: {"local": str(a.local_dir)})

            def boom(args):
                raise KeyError("synthetic-private-sentinel-98765")
            """))

        with patch.object(t3compose, "__path__", [str(self.temp)]), patch.dict(sys.modules):
            def invoke(args):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main(args)
                return code, out.getvalue(), err.getvalue()

            # Unexpected handler exception names declared command path and exception class,
            # never the exception value or raw argv.
            code, out, err = invoke(["sample", "boom"])
            self.assertEqual((code, out), (2, ""))
            self.assertEqual(
                err,
                "t3c sample boom: command failed (KeyError); check local inputs and helper installation\n",
            )
            self.assertNotIn("synthetic-private-sentinel-98765", err)

            # Unencodable return value and lone surrogate produce no partial stdout.
            for leaf, exc_name in (("bad-json", "TypeError"), ("bad-surrogate", "UnicodeEncodeError")):
                for prefix in ([], ["--text"]):
                    code, out, err = invoke([*prefix, "sample", leaf])
                    self.assertEqual((code, out), (2, ""))
                    self.assertEqual(
                        err,
                        f"t3c sample {leaf}: command failed ({exc_name}); "
                        "check local inputs and helper installation\n",
                    )

            # Text mode escapes embedded newlines in dictionary values so lines cannot be forged.
            code, out, err = invoke(["--text", "sample", "newlines"])
            self.assertEqual((code, err), (1, ""))
            self.assertEqual(
                out.splitlines(),
                [
                    'note: "line1\\nverified: True"',
                    'nested: {"k":"v\\n2"}',
                    "ok: false",
                ],
            )
            self.assertNotIn("verified: True", out.splitlines())

            # Non-dict text mode prints one canonical JSON representation.
            code, out, err = invoke(["--text", "sample", "list-data"])
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(out, '["a","b\\nc",1]\n')

            # None returns JSON null in both JSON and text modes.
            for prefix in ([], ["--text"]):
                code, out, err = invoke([*prefix, "sample", "none-data"])
                self.assertEqual((code, out, err), (0, "null\n", ""))

            # Relative and symlinked --local-dir overrides resolve through cwd and symlinks.
            real_local = (self.temp / "real-local").resolve()
            real_local.mkdir()
            link_local = self.temp / "link-local"
            link_local.symlink_to(real_local)
            subdir = self.temp / "work-cwd"
            subdir.mkdir()

            old_cwd = Path.cwd() if "Path" in dir() else os.getcwd()
            try:
                os.chdir(subdir)
                code, out, err = invoke(["--local-dir", "../link-local", "sample", "local"])
            finally:
                os.chdir(old_cwd)
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out), {"local": str(real_local)})
            self.assertEqual(local_dir(), self.temp / ".local")


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
        import unittest
        class OfflineOther(OfflineTestCase):
            def test_refusal(self):
                with self.assertRaisesRegex(AssertionError, "offline guard"):
                    subprocess.run(["/bin/echo", "synthetic"])
        result = unittest.TestResult()
        OfflineOther("test_refusal").run(result)
        self.assertEqual((result.errors, result.failures), ([], []))
        # The nested context restores this test's allowance.
        self.assertEqual(subprocess.run(["/bin/echo", "outer"], capture_output=True,
                                        text=True).stdout, "outer\n")

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

    def test_allowance_refuses_resolved_harness_paths(self):
        marker = self.temp / "ran"
        target = self.temp / "1.2.3"
        target.write_text("#!/bin/sh\ntouch '%s'\n" % marker)
        target.chmod(0o755)
        (self.temp / "claude").symlink_to(target)
        (self.temp / "alias").symlink_to(target)
        path = str(self.temp) + os.pathsep + os.environ.get("PATH", "")
        with patch.dict(os.environ, {"PATH": path}):
            for program in (target, self.temp / "alias", self.temp / "claude"):
                with self.subTest(program=program.name), \
                        self.assertRaisesRegex(AssertionError, "offline guard"):
                    self.allow_program(program)
            with self.assertRaisesRegex(AssertionError, "offline guard"):
                subprocess.run([str(self.temp / "alias")])
        self.assertFalse(marker.exists())

    def test_harness_paths_survive_path_changes(self):
        marker = self.temp / "ran"
        target = self.temp / "2.0.0"
        target.write_text("#!/bin/sh\ntouch '%s'\n" % marker)
        target.chmod(0o755)
        harness_dir = self.temp / "harness-bin"
        harness_dir.mkdir()
        (harness_dir / "codex").symlink_to(target)
        alias = self.temp / "codex-alias"
        alias.symlink_to(target)
        exposed = str(harness_dir) + os.pathsep + os.environ.get("PATH", "")
        # Found at installation, then hidden from PATH: still refused.
        with patch.dict(os.environ, {"PATH": exposed}):
            allowed = AllowedPrograms()
        with patch.dict(os.environ, {"PATH": os.defpath}):
            self.assertTrue(allowed.is_harness(target.resolve()))
            allowed.add(target.resolve())
            self.assertNotIn(target.resolve(), allowed)
        # Allowed while hidden, then exposed on PATH: refused at launch.
        self.allow_program(target)
        with patch.dict(os.environ, {"PATH": exposed}):
            with self.assertRaisesRegex(AssertionError, "offline guard"):
                subprocess.run([str(alias)])
        self.assertFalse(marker.exists())

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
            import socket
            import urllib.request
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
            # Network/name-lookup refusals also remain installed in grandchildren.
            launches.extend([
                socket.socket,
                lambda: socket.getaddrinfo('example.invalid', 443),
                lambda: socket.gethostbyname('example.invalid'),
                lambda: socket.gethostbyname_ex('example.invalid'),
                lambda: socket.gethostbyaddr('192.0.2.1'),
                lambda: socket.getnameinfo(('192.0.2.1', 443), 0),
                lambda: urllib.request.urlopen('data:text/plain,synthetic'),
            ])
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
                     lambda: socket.gethostbyname_ex("example.invalid"),
                     lambda: socket.gethostbyaddr("192.0.2.1"),
                     lambda: socket.getnameinfo(("192.0.2.1", 443), 0),
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
