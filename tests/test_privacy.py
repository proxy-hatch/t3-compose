"""Synthetic repository integration and fail-closed publication checks."""
import contextlib
import importlib
import io
import json
import os
import runpy
from pathlib import Path
import subprocess
import sys
import traceback
from unittest.mock import patch

from tests.support import FakeRunner, OfflineTestCase, ROOT
from t3compose import cli, process


class OfflinePrivacy(OfflineTestCase):
    def setUp(self):
        self.allow_program("grep")
        self.local = self.temp / "state-root"
        self.list_file = self.local / "state/private-identifiers.txt"
        self.list_file.parent.mkdir(parents=True)
        self.list_file.write_text("syn-secret\nsyn-other\n")
        self.repo = self.temp / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Synthetic Tester")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.base = self.commit("base")

    def git(self, *args, input=None):
        return subprocess.run(["git", "-C", str(self.repo), *args], input=input,
                              capture_output=True, check=True).stdout.decode().strip()

    def commit(self, message, files=None):
        for name, content in (files or {}).items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode() if isinstance(content, str) else content)
        self.git("add", "--all")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-F", "-",
                 input=message.encode())
        return self.git("rev-parse", "HEAD")

    def invoke(self, *extra, revision=None, text=False, entry=False):
        argv = ["--local-dir", str(self.local), *(["--text"] if text else []),
                "privacy", "check", "--repo-path", str(self.repo),
                "--range", revision or self.base + "..HEAD", *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            if entry:
                # Execute the actual entry point under this test's offline guard.
                # A separately imported support module in a bootstrap child stacks
                # a second guard whose grep allowance cannot reach the first one.
                with patch.object(sys, "argv", [str(ROOT / "bin/t3c"), *argv]):
                    with self.assertRaises(SystemExit) as exited:
                        runpy.run_path(str(ROOT / "bin/t3c"), run_name="__main__")
                code = exited.exception.code
            else:
                code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def data(self, result, code):
        self.assertEqual(result[0], code, result[2])
        self.assertEqual(result[2], "")
        self.assertNotIn("syn-secret", result[1])
        self.assertNotIn("syn-other", result[1])
        return json.loads(result[1])

    def test_entry_clean_range_and_empty_range(self):
        self.commit("public", {"readme": "safe"})
        data = self.data(self.invoke(entry=True), 0)
        self.assertEqual(data, {"ok": True, "commits": 1, "findings": [], "action": None})
        data = self.data(self.invoke(revision="HEAD..HEAD", entry=True), 0)
        self.assertEqual(data["commits"], 0)

    def test_entry_full_message_and_intermediate_entire_trees(self):
        first = self.commit("subject\n\nbody syn-secret", {"kept": "syn-other"})
        second = self.commit("unchanged sensitive file", {"new": "public"})
        third = self.commit("removed", {"kept": "public"})
        data = self.data(self.invoke(entry=True), 1)
        findings = data["findings"]
        self.assertIn({"field": "message", "commit": first, "file": None,
                       "label": None, "lines": [1]}, findings)
        self.assertEqual({f["commit"] for f in findings if f["field"] == "file"},
                         {first, second})
        self.assertNotIn(third, {f["commit"] for f in findings})
        self.assertIn("Corrections", data["action"])
        self.assertIn("Shawn", data["action"])

    def test_entry_branch_title_body_and_private_metadata_in_both_modes(self):
        self.commit("public", {"syn-secret\tname\nfile": "syn-other"})
        self.git("branch", "syn-secret-branch")
        self.git("symbolic-ref", "HEAD", "refs/heads/syn-secret-branch")
        title, body = self.temp / "title", self.temp / "body"
        title.write_text("syn-secret title")
        body.write_text("paragraph\nsyn-other body")
        args = ("--text-file", "title=" + str(title), "--text-file", "syn-secret=" + str(body))
        data = self.data(self.invoke(*args, entry=True), 1)
        self.assertEqual({f["field"] for f in data["findings"]},
                         {"branch", "path", "file", "label", "text"})
        self.assertTrue(all(f["file"] in (None, "[redacted]") for f in data["findings"]))
        self.assertTrue(all(f["label"] in (None, "title", "[redacted]") for f in data["findings"]))
        text_result = self.invoke(*args, text=True, entry=True)
        self.assertEqual(text_result[0], 1, text_result[2])
        self.assertNotIn("syn-secret", "".join(text_result[1:]))
        self.assertNotIn("syn-other", "".join(text_result[1:]))
        empty = self.data(self.invoke("--branch", "syn-other", revision="HEAD..HEAD", entry=True), 1)
        self.assertEqual(empty["commits"], 0)

    def test_merge_side_commits_are_selected(self):
        self.git("checkout", "-q", "-b", "side")
        side = self.commit("side", {"side-file": "syn-secret"})
        self.git("checkout", "-q", "main")
        self.commit("main", {"main-file": "public"})
        self.git("-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "-m", "merge", "side")
        self.commit("clean", {"side-file": "public"})
        data = self.data(self.invoke(entry=True), 1)
        self.assertIn(side, {f["commit"] for f in data["findings"]})
        self.assertEqual(data["commits"], 4)

    def test_lock_exclusion_symlink_blob_and_identity_exclusion(self):
        outside = self.temp / "outside"
        outside.write_text("syn-secret")
        (self.repo / "link").symlink_to(outside)
        self.git("config", "user.name", "syn-secret")
        self.commit("public", {"nested/deps.lock": b"syn-secret\x00\xff", "odd\tfile\nname": "safe"})
        self.data(self.invoke(entry=True), 0)
        (self.repo / "link").unlink()
        (self.repo / "link").symlink_to("syn-secret-target")
        self.commit("link target")
        data = self.data(self.invoke(), 1)
        self.assertEqual(data["findings"][0]["file"], "link")

    def test_lf_only_regex_boundaries_and_list_reload(self):
        self.commit("public", {"file": "left\u2028syn-secret"})
        self.list_file.write_text("^syn-secret$\n")
        self.data(self.invoke(), 0)
        self.list_file.write_text("syn-secret\n")
        self.data(self.invoke(), 1)
        self.list_file.write_text("[\n")
        self.assertEqual(self.invoke()[0], 2)

    def test_invalid_ranges_and_text_specs_never_echo(self):
        for value in ("--syn-secret..HEAD", "..HEAD", "HEAD..", "HEAD...HEAD",
                      "HEAD..HEAD..HEAD", "missing..HEAD", "HEAD\nsyn-secret..HEAD"):
            with self.subTest(value=value):
                result = self.invoke(revision=value)
                self.assertEqual(result[0], 2)
                self.assertEqual(result[1], "")
                self.assertNotIn("syn-secret", result[2])
        for spec in ("syn-secret", "=path", "title=", "title=/missing/syn-secret"):
            self.assertEqual(self.invoke("--text-file", spec)[0], 2)

    def test_all_list_failures_block_even_empty_range(self):
        for content in (b"", b"[\n", b"\xff", b"nul\x00", b"a" * 65537,
                        b"x\n" * 513, b"x" * 1025):
            self.list_file.write_bytes(content)
            self.assertEqual(self.invoke()[0], 2)
        self.list_file.unlink()
        self.assertEqual(self.invoke()[0], 2)
        self.list_file.mkdir()
        self.assertEqual(self.invoke()[0], 2)

    def test_binary_oversized_and_unreadable_inputs_fail_closed(self):
        for content in (b"bad\x00", b"\xff", b"a" * 1048577):
            self.commit("public", {"file": content})
            self.assertEqual(self.invoke()[0], 2)
            self.base = self.git("rev-parse", "HEAD")
        for target in (self.temp / "absent", self.temp):
            self.assertEqual(self.invoke("--text-file", "title=" + str(target))[0], 2)
        text = self.temp / "text"
        for content in (b"\xff", b"a\x00", b"a" * 1048577):
            text.write_bytes(content)
            self.assertEqual(self.invoke("--text-file", "title=" + str(text))[0], 2)

    def test_git_failure_malformed_results_and_safe_exception_context(self):
        self.assertIsNotNone(importlib.util.find_spec("t3compose.privacy"))
        privacy = importlib.import_module("t3compose.privacy")
        for result in (subprocess.CompletedProcess([], 1, b"syn-secret", b"syn-secret"),
                       subprocess.CompletedProcess([], False, b"", b""),
                       subprocess.CompletedProcess([], 0, "not bytes", ""),
                       subprocess.CompletedProcess([], 0, b"malformed", b""),
                       subprocess.CompletedProcess([], 0, b"x" * 1048577, b""),
                       process.ProcessError("timeout"), OSError("syn-secret")):
            def git(argv, **kwargs):
                if isinstance(result, Exception):
                    raise result
                return result
            runner = FakeRunner({"git": git, "grep": lambda a, **kw: subprocess.CompletedProcess(a, 1, b"", b"")})
            with patch.object(privacy.process, "run", runner):
                result_cli = self.invoke()
                self.assertEqual(result_cli[0], 2)
                self.assertNotIn("syn-secret", "".join(result_cli[1:]))
                args = cli.build_parser().parse_args(["--local-dir", str(self.local), "privacy", "check",
                    "--repo-path", str(self.repo), "--range", "HEAD..HEAD"])
                args.local_dir = self.local
                with self.assertRaises(cli.CommandError) as raised:
                    args.handler(args)
                self.assertNotIn("syn-secret", "".join(traceback.format_exception(raised.exception)))

    def test_registration_and_import_have_no_process_or_state_io(self):
        self.assertIsNotNone(importlib.util.find_spec("t3compose.privacy"))
        module = importlib.import_module("t3compose.privacy")
        with patch.object(process, "run", side_effect=AssertionError("process")), \
                patch("builtins.open", side_effect=AssertionError("read")), \
                patch("os.open", side_effect=AssertionError("read")):
            importlib.reload(module)
            self.assertIn(("privacy", "check"), cli.command_names())

    def test_late_git_malformed_data_raises_command_error_without_context(self):
        self.assertIsNotNone(importlib.util.find_spec("t3compose.privacy"))
        # Synthetic object protocol: shallow flag, endpoints, walk, branch,
        # commit size/content, tree, blob size/content.
        a, b, c = b"a" * 40, b"b" * 40, b"c" * 40
        replies = [b"false\n", a + b" commit\n", b + b" commit\n", b + b"\n", b"main\n",
                   b"15\n", b"header\n\npublic\n", b"100644 blob " + c + b"\tfile\0", b"6\n", b"public"]
        corruptions = [(1, b"bad commit\n"), (3, b"\xff\n"), (3, b"bad\n"), (4, b"main"),
                       (5, b"1048577\n"), (6, b"no separator---"), (7, b"broken"),
                       (7, b"160000 commit " + c + b"\tsubmodule\0"), (8, b"bad\n"), (9, b"other")]
        args = cli.build_parser().parse_args(["--local-dir", str(self.local), "privacy", "check",
                                             "--repo-path", str(self.repo), "--range", "HEAD~1..HEAD"])
        args.local_dir = self.local
        for index, bad in corruptions:
            sequence = iter(replies[:index] + [bad] + replies[index + 1:])
            runner = FakeRunner({"git": lambda argv, **kw: subprocess.CompletedProcess(argv, 0, next(sequence), b""),
                                 "grep": lambda argv, **kw: subprocess.CompletedProcess(argv, 1, b"", b"")})
            with self.subTest(stage=index), patch.object(process, "run", runner):
                with self.assertRaises(cli.CommandError):
                    args.handler(args)

    def test_immutable_commit_reads_avoid_secret_argv_and_worktree_files(self):
        self.commit("public", {"file": "safe"})
        self.git("branch", "syn-secret-ref")
        (self.repo / "file").write_text("syn-secret")
        calls, original = [], process.run
        def record(argv, **kwargs):
            calls.append((argv, kwargs))
            return original(argv, **kwargs)
        with patch.object(process, "run", record):
            self.data(self.invoke("--branch", "public", revision=self.base + "..syn-secret-ref"), 0)
        for argv, kwargs in calls:
            self.assertNotIn("syn-secret", " ".join(argv))
            self.assertTrue(kwargs["capture_output"])
            self.assertFalse(kwargs.get("shell", False))
            if argv[0] == "git":
                self.assertEqual(kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
                self.assertEqual(kwargs["env"]["GIT_ALLOW_PROTOCOL"], "")
                self.assertIn("--no-optional-locks", argv)
        self.assertEqual((self.repo / "file").read_text(), "syn-secret")

    def test_shallow_detached_and_submodule_refusals(self):
        head = self.commit("public")
        self.git("checkout", "-q", "--detach")
        self.assertEqual(self.invoke()[0], 2)
        self.data(self.invoke("--branch", "publication"), 0)
        self.git("update-index", "--add", "--cacheinfo", "160000," + head + ",submodule")
        self.git("-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "submodule")
        self.assertEqual(self.invoke("--branch", "publication")[0], 2)
        (self.repo / ".git/shallow").write_text(head + "\n")
        self.assertEqual(self.invoke("--branch", "publication")[0], 2)
