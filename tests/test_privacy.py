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

    def test_corrections_branch_tag_collision_keeps_anchored_branch_screen(self):
        self.git("branch", "syn-secret")
        self.git("tag", "syn-secret")
        self.git("symbolic-ref", "HEAD", "refs/heads/syn-secret")
        self.list_file.write_text("^syn-secret$\n")
        for extra in ((), ("--branch", "syn-secret")):
            with self.subTest(explicit=bool(extra)):
                data = self.data(self.invoke(*extra, revision="HEAD..HEAD", entry=True), 1)
                self.assertEqual(data["commits"], 0)
                self.assertEqual(data["findings"], [{"field": "branch", "commit": None,
                    "file": None, "label": None, "lines": [1]}])

    def test_corrections_empty_directory_paths_are_screened(self):
        empty = self.git("mktree", input=b"")
        tree = self.git("mktree", input=("040000 tree " + empty + "\tsyn-secret-dir\n").encode())
        commit = self.git("commit-tree", tree, "-p", self.base, input=b"public\n")
        data = self.data(self.invoke(revision=self.base + ".." + commit, entry=True), 1)
        self.assertEqual(data["commits"], 1)
        self.assertEqual(data["findings"], [{"field": "path", "commit": commit,
            "file": "[redacted]", "label": None, "lines": [1]}])

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
        replies = [b"false\n", a + b" commit\n", b + b" commit\n", b + b"\n", b"refs/heads/main\n",
                   b"15\n", b"header\n\npublic\n", b"100644 blob " + c + b"\tfile\0", b"6\n", b"public"]
        corruptions = [(1, b"bad commit\n"), (3, b"\xff\n"), (3, b"bad\n"), (4, b"main"), (4, b"refs/tags/main\n"),
                       (5, b"1048577\n"), (6, b"no separator---"), (7, b"broken"),
                       (7, b"160000 commit " + c + b"\tsubmodule\0"), (8, b"bad\n"), (9, b"other")]
        args = cli.build_parser().parse_args(["--local-dir", str(self.local), "privacy", "check",
                                             "--repo-path", str(self.repo), "--range", "HEAD~1..HEAD"])
        args.local_dir = self.local
        for index, bad in corruptions:
            sequence = iter(replies[:index] + [bad] + replies[index + 1:])
            def reply(argv, **kwargs):
                if "--show-object-format" in argv:
                    return subprocess.CompletedProcess(argv, 0, b"sha1\n", b"")
                if kwargs.get("input") in (a + b"\n", b + b"\n"):
                    return subprocess.CompletedProcess(argv, 0, kwargs["input"][:-1] + b" commit\n", b"")
                # The shared core rechecks complete history after CLI selection.
                if "--is-shallow-repository" in argv and replied_shallow[0]:
                    return subprocess.CompletedProcess(argv, 0, b"false\n", b"")
                if "--is-shallow-repository" in argv:
                    replied_shallow[0] = True
                return subprocess.CompletedProcess(argv, 0, next(sequence), b"")
            replied_shallow = [False]
            runner = FakeRunner({"git": reply,
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

    # Removing exact-set validation or recomputing BASE..HEAD breaks these
    # direct API assertions; fixtures contain only synthetic identifiers.
    def scan(self, commits, **kwargs):
        from t3compose import privacy
        options = dict(base=self.base, head=self.git("rev-parse", "HEAD"), branch="main")
        options.update(kwargs)
        self.assertTrue(callable(getattr(privacy, "scan_commits", None)),
                        "missing shared exact-set scanner")
        return privacy.scan_commits(self.repo, self.local, commits, **options)

    def test_api_exact_subset_does_not_recompute_cli_history(self):
        first = self.commit("syn-secret", {"file": "syn-other"})
        clean = self.commit("clean", {"file": "public"})
        self.assertEqual(self.scan([clean]),
                         {"ok": True, "commits": 1, "findings": [], "action": None})
        result = self.scan([first, clean])
        self.assertFalse(result["ok"])
        self.assertEqual(result["commits"], 2)
        self.assertEqual([f["field"] for f in result["findings"]], ["message", "file"])
        self.assertEqual(self.data(self.invoke(entry=True), 1)["commits"], 2)
        # Selection need not be reachable from head: later push adapters have
        # their own complete selection responsibility.
        self.assertEqual(self.scan([first], head=self.base)["commits"], 1)

    def test_api_preserves_commit_and_text_order_and_redacts_metadata(self):
        first = self.commit("syn-secret")
        second = self.commit("syn-other")
        report = self.scan((first, second), branch="syn-secret-branch",
                           texts=(("title", "syn-other"), ("syn-secret", "syn-secret")))
        self.assertEqual([f["field"] for f in report["findings"]],
                         ["branch", "text", "label", "text", "message", "message"])
        self.assertEqual([f["commit"] for f in report["findings"][-2:]], [first, second])
        self.assertEqual(report["findings"][2]["label"], "[redacted]")
        reverse = self.scan([second, first])
        self.assertEqual([f["commit"] for f in reverse["findings"]], [second, first])
        self.assertNotIn("syn-secret", json.dumps(report))
        self.assertNotIn("syn-other", json.dumps(report))

    def test_api_rejects_invalid_selections_and_endpoints_safely(self):
        head = self.commit("public")
        blob = self.git("hash-object", "-w", "--stdin", input=b"public")
        tree = self.git("rev-parse", "HEAD^{tree}")
        self.git("-c", "tag.gpgSign=false", "tag", "-a", "annotated", "-m", "public")
        tag = self.git("rev-parse", "annotated")
        invalid_ids = (None, 3, b"a" * 40, "HEAD", head[:12], head.upper(),
                       "f" * 40, blob, tree, tag, "syn-secret\n", "a" * 64)
        for value in invalid_ids:
            with self.subTest(value=value):
                with self.assertRaises(cli.CommandError) as raised:
                    self.scan([value])
                self.assertNotIn("syn-secret", str(raised.exception))
                for endpoint in ("base", "head"):
                    with self.assertRaises(cli.CommandError):
                        self.scan([], **{endpoint: value})
        for selection in (None, head, head.encode(), {head}, {head: True},
                          iter([head]), [head, head]):
            with self.subTest(selection=type(selection).__name__):
                with self.assertRaises(cli.CommandError):
                    self.scan(selection)

    def test_api_rejects_invalid_decoded_text_and_pair_shapes(self):
        invalid = (None, b"text", 1, "nul\0", "\ud800", "é" * 524289)
        for value in invalid:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(cli.CommandError):
                    self.scan([], branch=value)
                with self.assertRaises(cli.CommandError):
                    self.scan([], texts=[(value, "public")])
                with self.assertRaises(cli.CommandError):
                    self.scan([], texts=[("title", value)])
        for texts in (None, "title", {"title": "public"}, iter([]),
                      ["ab"], [("title",)], [("title", "public", "extra")], [("", "public")]):
            with self.subTest(texts=type(texts).__name__):
                with self.assertRaises(cli.CommandError):
                    self.scan([], texts=texts)
        with self.assertRaises(cli.CommandError):
            self.scan([], branch="")
        self.assertTrue(self.scan([], texts=[("title", "")])["ok"])

    def test_api_list_is_fresh_even_empty_and_captured_once(self):
        from t3compose import identifier_list
        self.assertTrue(self.scan([])["ok"])
        original = identifier_list.load_identifiers
        def captured(local):
            patterns = original(local)
            self.list_file.write_text("different-pattern\n")
            return patterns
        with patch.object(identifier_list, "load_identifiers", side_effect=captured) as load:
            report = self.scan([], branch="syn-secret", texts=[("title", "syn-other")])
        self.assertEqual(load.call_count, 1)
        self.assertEqual([f["field"] for f in report["findings"]], ["branch", "text"])
        self.assertTrue(self.scan([], branch="syn-secret")["ok"])
        for raw in (b"", b"[\n", b"\xff", b"nul\0", b"a" * 65537, b"x\n" * 513):
            self.list_file.write_bytes(raw)
            with self.assertRaises(cli.CommandError):
                self.scan([])
        self.list_file.unlink()
        with self.assertRaises(cli.CommandError):
            self.scan([])

    def test_api_and_cli_explicitly_refuse_sha256_repository(self):
        self.repo = self.temp / "sha256-repo"
        self.repo.mkdir()
        self.git("init", "-q", "--object-format=sha256", "-b", "main")
        self.git("config", "user.name", "Synthetic Tester")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.base = self.commit("base")
        with self.assertRaisesRegex(cli.CommandError, "SHA-1"):
            self.scan([])
        result = self.invoke(revision="HEAD..HEAD", entry=True)
        self.assertEqual(result[0], 2)
        self.assertEqual(result[1], "")
        self.assertIn("SHA-1", result[2])

    def test_api_refuses_inherited_binary_and_oversize_even_after_base(self):
        for content in (b"jpeg\0\xff", b"\xff", b"x" * 1048577):
            self.base = self.commit("inherited", {"asset": content})
            head = self.commit("public")
            with self.subTest(size=len(content)):
                with self.assertRaises(cli.CommandError):
                    self.scan([head])
                result = self.invoke()
                self.assertEqual(result[0], 2)
                self.assertEqual(result[1], "")

    def test_api_later_failure_never_returns_partial_findings(self):
        first = self.commit("syn-secret")
        bad = self.commit("public", {"asset": b"\0"})
        (self.repo / "asset").unlink()
        clean = self.commit("removed")
        with self.assertRaises(cli.CommandError):
            self.scan([first, bad, clean])
        result = self.invoke()
        self.assertEqual(result[0], 2)
        self.assertEqual(result[1], "")
        self.assertNotIn("syn-secret", result[2])

    def test_api_merge_side_unsupported_tree_is_not_hidden_by_clean_head(self):
        self.git("checkout", "-q", "-b", "side")
        side = self.commit("side", {"asset": b"\0"})
        self.git("checkout", "-q", "main")
        self.commit("main")
        self.git("-c", "core.hooksPath=/dev/null", "merge", "--no-ff", "-m", "merge", "side")
        (self.repo / "asset").unlink()
        clean = self.commit("removed")
        self.assertTrue(self.scan([clean])["ok"])
        with self.assertRaises(cli.CommandError):
            self.scan([side, clean])
        result = self.invoke()
        self.assertEqual(result[0], 2)
        self.assertEqual(result[1], "")

    def test_cli_uses_shared_core_with_resolved_endpoints_and_complete_set(self):
        from t3compose import privacy
        first = self.commit("first")
        head = self.commit("second")
        self.assertTrue(callable(getattr(privacy, "scan_commits", None)),
                        "missing shared exact-set scanner")
        with patch.object(privacy, "scan_commits", wraps=privacy.scan_commits) as scan:
            self.data(self.invoke(), 0)
        args, kwargs = scan.call_args
        self.assertEqual(list(args[2]), [head, first])
        self.assertEqual(kwargs["base"], self.base)
        self.assertEqual(kwargs["head"], head)
        self.assertEqual(kwargs["branch"], "main")


class OfflineArtifactProvenance(OfflineTestCase):
    # Removing any join or replacing the fresh authority load with cached state
    # must break these synthetic authorizations. No real decision is represented.
    setUp = OfflinePrivacy.setUp
    git = OfflinePrivacy.git
    commit = OfflinePrivacy.commit
    invoke = OfflinePrivacy.invoke

    def digest(self, value):
        import hashlib
        from t3compose.jsonio import canonical_json
        raw = value if isinstance(value, bytes) else canonical_json(value)
        return 'sha256:' + hashlib.sha256(raw).hexdigest()

    def write(self, name, value):
        from t3compose.jsonio import canonical_json
        target = self.temp / name
        target.write_bytes(canonical_json(value))
        return self.digest(target.read_bytes())

    def fixture(self, change=None):
        from t3compose import privacy, records
        self.assertTrue(callable(getattr(privacy, 'load_artifact_policy', None)),
                        'missing internal provenance loader')
        self.git('remote', 'add', 'origin', 'https://example.invalid/team/repo.git')
        self.patterns = ['syn-secret', 'syn-other']
        self.manifest = dict(schemaVersion=1, repository='example.invalid/team/repo',
                             objectFormat='sha1', baseline=self.base,
                             entries=[dict(path='asset.bin', mode='100644', blobOid='a'*40, size=9)])
        prep = privacy.prepare_policy_identifiers(self.local)
        self.receipt = dict(schemaVersion=1, receiptId='synthetic-inventory',
                            repository=self.manifest['repository'], baseline=self.base,
                            manifestDigest=self.write('manifest', self.manifest),
                            policyVersion=prep['policyVersion'], policyDigest=prep['policyDigest'],
                            listDigest=prep['listDigest'], authorityWorkId='SYNTHETIC', approvalSeq=1,
                            sourceFile='source', sourceDigest='', verificationFile='verification',
                            verificationDigest='')
        self.source = dict(schemaVersion=1, receiptId=self.receipt['receiptId'], contextDigest='',
                           sourceRef='synthetic:owner-message', date='2026-11-04',
                           quote='SYNTHETIC ONLY: accept this exact inventory risk.')
        self.verification = dict(schemaVersion=1, receiptId=self.receipt['receiptId'],
                                 contextDigest='', verifier='synthetic-independent-reviewer',
                                 sourceRef='synthetic:inventory-observation', method='git-object-inventory',
                                 baselineEvidence=dict(repository=self.manifest['repository'],
                                                       commit=self.base, objectFormat='sha1',
                                                       entriesDigest=self.digest(self.manifest['entries'])),
                                 inventoryDigest=self.receipt['manifestDigest'], result='verified')
        if change:
            change()
        self.bind()
        identity = self.manifest['repository']
        self.writer = 'manager@' + identity
        work = dict(workId='SYNTHETIC', linearIssue=None, repository=identity,
                    targetBranch='main', reviewBase=self.base, scope=['asset.bin'], designRefs=[],
                    ownedPaths=['asset.bin'], dependencies=[], acceptance=[dict(id='AC', text='Synthetic')],
                    requiredChecks=[dict(id='CHK', command='synthetic', kind='fast')], writer=self.writer)
        records.create_work(self.local, identity, 'SYNTHETIC', work, writer=self.writer)
        self.decision('approve', answer=self.source['quote'], date=self.source['date'])

    def bind(self):
        self.receipt['manifestDigest'] = self.write('manifest', self.manifest)
        keys = ('receiptId', 'repository', 'baseline', 'manifestDigest', 'policyVersion',
                'policyDigest', 'listDigest', 'authorityWorkId')
        context = dict(schemaVersion=1, **{k: self.receipt[k] for k in keys})
        for value in (self.source, self.verification):
            value['contextDigest'] = self.digest(context)
        self.receipt['sourceDigest'] = self.write('source', self.source)
        self.receipt['verificationDigest'] = self.write('verification', self.verification)
        context.update(sourceDigest=self.receipt['sourceDigest'],
                       verificationDigest=self.receipt['verificationDigest'])
        self.context = self.digest(context)
        self.policy = dict(schemaVersion=1, policyVersion=self.receipt['policyVersion'],
                           manifestFile='manifest', manifestDigest=self.receipt['manifestDigest'],
                           receiptFile='receipt', receiptDigest=self.write('receipt', self.receipt),
                           authorityWorkId='SYNTHETIC')
        self.write('policy', self.policy)

    def decision(self, action, *, context=None, answer='Synthetic decision',
                 date='2026-11-04', owner='owner', question=None):
        from t3compose import records
        return records.append_event(self.local, self.manifest['repository'], 'SYNTHETIC', 'decision',
            dict(question=question or 'privacy-artifact/v1 ' + action + ' ' +
                 self.receipt['receiptId'] + ' ' + (context or self.context),
                 answer=answer, date=date, decidedBy=owner), writer=self.writer)

    def load(self, **kwargs):
        from t3compose import privacy
        options = dict(base=self.base, patterns=self.patterns)
        options.update(kwargs)
        return privacy.load_artifact_policy(self.repo, self.local, self.temp/'policy', **options)

    def refuse(self, **kwargs):
        with self.assertRaises(cli.CommandError) as error:
            self.load(**kwargs)
        self.assertNotIn(str(self.temp), str(error.exception))
        self.assertNotIn('SYNTHETIC ONLY', str(error.exception))
        self.assertNotIn('syn-secret', str(error.exception))

    def test_valid_bound_immutable_observation_and_no_exemption(self):
        from t3compose import records, privacy
        self.fixture()
        result = self.load()
        self.assertEqual(result['contextDigest'], self.context)
        self.assertEqual(result['authorityCheckpoint'], records.snapshot(
            self.local, self.manifest['repository'], 'SYNTHETIC'))
        self.assertEqual(result['authorityEventCount'], 1)
        self.assertLessEqual(result['observedStart'], result['observedEnd'])
        with self.assertRaises(TypeError):
            result['manifest']['entries'][0]['size'] = 1
        self.assertNotIn('quote', result)
        head = self.commit('opaque', {'asset.bin': b'\0'})
        with self.assertRaises(cli.CommandError):
            privacy.scan_commits(self.repo, self.local, [head], base=self.base, head=head, branch='main')
        self.assertEqual(self.invoke('--privacy-policy', str(self.temp/'policy'))[0], 2)

    def test_corrections_origins_never_use_ambient_directory_git(self):
        from t3compose import records
        self.fixture()
        origins = ('https://EXAMPLE.invalid/team/repo.git',
                   'ssh://git@EXAMPLE.invalid/team/repo.git',
                   'git@EXAMPLE.invalid:team/repo.git')
        selectors = dict(GIT_DIR=str(self.repo/'.git'), GIT_WORK_TREE=str(self.temp),
                         GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='remote.origin.url',
                         GIT_CONFIG_VALUE_0='https://decoy.invalid/other/repo.git')
        for origin in origins:
            self.git('remote', 'set-url', 'origin', origin)
            collision = self.temp/origin
            for directory in (False, True):
                if directory:
                    collision.mkdir(parents=True)
                for ambient_env in ({}, selectors):
                    with self.subTest(origin=origin, directory=directory, selectors=bool(ambient_env)):
                        with contextlib.chdir(self.temp), patch.dict(os.environ, ambient_env), patch.object(
                                records, 'git', return_value='https://example.invalid/team/repo.git') as ambient:
                            result = self.load()
                        self.assertEqual(ambient.call_count, 0, 'origin normalization performed ambient Git')
                        self.assertEqual(result['repository'], self.manifest['repository'])
                        self.assertEqual(result['contextDigest'], self.context)
        # Preserve the URI grammar as well as the supported forms above.
        for origin in ('https://example.invalid/team/repo',
                       'ssh://EXAMPLE.invalid/team/repo.git',
                       'git@EXAMPLE.invalid:team/repo'):
            self.git('remote', 'set-url', 'origin', origin)
            self.assertEqual(self.load()['repository'], self.manifest['repository'])
        for origin in ('https://user:synthetic-password@example.invalid/team/repo.git',
                       'https://example.invalid:443/team/repo.git',
                       'ssh://git@example.invalid:22/team/repo.git',
                       'https://example.invalid/team/repo.git?query',
                       'https://example.invalid/team/repo.git#fragment',
                       'ssh://git@example.invalid/team/repo.git?query',
                       'ssh://git@example.invalid/team/repo.git#fragment',
                       'https://[broken/team/repo.git', 'https:///team/repo.git',
                       'git@example.invalid:22/team/repo.git',
                       'git@example.invalid:team/repo.git?query',
                       'git@example.invalid:team/repo.git#fragment',
                       'file://example.invalid/team/repo.git',
                       'example.invalid/team/repo', 'git@example.invalid:team//repo.git'):
            self.git('remote', 'set-url', 'origin', origin)
            with self.subTest(rejected=origin):
                with self.assertRaises(cli.CommandError) as error:
                    self.load()
                self.assertEqual(str(error.exception), 'invalid artifact provenance input')

    def test_corrections_snapshot_descriptor_ownership_on_setup_failure(self):
        import errno
        self.fixture()
        original_open, original_fdopen = os.open, os.fdopen
        target = self.temp/'policy'
        selected = []
        def opened(path, *args, **kwargs):
            fd = original_open(path, *args, **kwargs)
            if path == target:
                selected.append(fd)
            return fd
        def fdopened(fd, *args, **kwargs):
            if fd in selected:
                raise OSError('synthetic wrapper setup failure')
            return original_fdopen(fd, *args, **kwargs)
        with patch.object(os, 'open', side_effect=opened), patch.object(os, 'fdopen', side_effect=fdopened):
            with self.assertRaises(cli.CommandError) as error:
                self.load()
        self.assertEqual(str(error.exception), 'invalid artifact provenance input')
        self.assertEqual(len(selected), 1)
        try:
            os.fstat(selected[0])
        except OSError as error:
            self.assertEqual(error.errno, errno.EBADF)
        else:
            os.close(selected[0])  # Clean up the old implementation's leak on red.
            self.fail('raw policy descriptor remained open after fdopen failure')
        # A subsequent ordinary read accepts the same bytes and closes its FD.
        selected.clear()
        raw = target.read_bytes()
        with patch.object(os, 'open', side_effect=opened):
            result = self.load()
        self.assertEqual(result['policyFileDigest'], self.digest(raw))
        self.assertEqual(result['contextDigest'], self.context)
        self.assertEqual(len(selected), 1)
        with self.assertRaises(OSError) as error:
            os.fstat(selected[0])
        self.assertEqual(error.exception.errno, errno.EBADF)

    def test_preparation_semantics_and_missing_invalid_list(self):
        from t3compose import privacy, identifier_list
        self.assertTrue(callable(getattr(privacy, 'prepare_policy_identifiers', None)),
                        'missing counts/digests helper')
        self.list_file.write_text('x\n\nx\n')
        result = privacy.prepare_policy_identifiers(self.local)
        self.assertEqual(set(result), {'policyVersion','policyDigest','listDigest','patternCount'})
        self.assertEqual(result['patternCount'], 3)
        self.assertEqual(result['listDigest'], self.digest(['x', '', 'x']))
        for data in (b'', b'[', b'\xff', b'\0'):
            self.list_file.write_bytes(data)
            with self.assertRaises(cli.CommandError):
                privacy.prepare_policy_identifiers(self.local)
        self.list_file.unlink()
        with self.assertRaises(cli.CommandError):
            privacy.prepare_policy_identifiers(self.local)

    def test_loader_uses_captured_list_not_reloaded_list(self):
        from t3compose import identifier_list
        self.fixture()
        self.list_file.write_text('different\n')
        with patch.object(identifier_list, 'load_identifiers', side_effect=AssertionError('second load')):
            self.assertEqual(self.load()['contextDigest'], self.context)
        self.refuse(patterns=['different'])
        self.refuse(patterns=['['])

    def test_closed_schema_scalar_and_path_matrix(self):
        self.fixture()
        import copy
        originals = {name: copy.deepcopy(getattr(self, name)) for name in
                     ('manifest', 'receipt', 'source', 'verification', 'policy')}
        cases = [('manifest','objectFormat','sha256'), ('manifest','baseline',True),
                 ('manifest','repository','EXAMPLE.invalid/team/repo'),
                 ('receipt','approvalSeq',True), ('receipt','approvalSeq',0),
                 ('receipt','receiptId','bad id'), ('receipt','policyDigest','sha256:'+'0'*64),
                 ('receipt','listDigest','sha256:'+'0'*64), ('receipt','authorityWorkId','OTHER'),
                 ('source','date','2026-02-30'), ('source','sourceRef',' '), ('source','quote',''),
                 ('verification','sourceRef',''), ('verification','verifier',''),
                 ('verification','method','approved'), ('verification','result','approved'),
                 ('verification','baselineEvidence',{'commit':self.base})]
        for name, key, value in cases:
            with self.subTest(name=name, key=key, value=value):
                document = copy.deepcopy(originals[name]); document[key] = value
                self.write(name, document)
                # Re-pin raw digest to exercise semantics rather than tamper only.
                if name in ('source','verification'):
                    receipt = copy.deepcopy(originals['receipt'])
                    receipt[name+'Digest'] = self.digest((self.temp/name).read_bytes())
                    self.write('receipt', receipt)
                    policy = copy.deepcopy(originals['policy'])
                    policy['receiptDigest'] = self.digest((self.temp/'receipt').read_bytes())
                    self.write('policy', policy)
                self.refuse()
                for n,v in originals.items(): self.write(n,v)
        for name, original in originals.items():
            for key in original:
                document = dict(original); del document[key]
                self.write(name, document); self.refuse(); self.write(name, original)
            self.write(name, dict(original, unexpected=True)); self.refuse(); self.write(name, original)
        for path in ('/absolute','../asset','a//b','a/./b','a/../b','a\\b','a*','a?','a[0]',''):
            self.manifest = copy.deepcopy(originals['manifest'])
            self.manifest['entries'][0]['path'] = path
            self.bind(); self.refuse()
        for field, values in [('size',[True,-1,2**63,1.5,'9']),
                              ('mode',['100755','120000','160000',100644]),
                              ('blobOid',['A'*40,'a'*64,'HEAD'])]:
            for value in values:
                self.manifest = copy.deepcopy(originals['manifest'])
                self.manifest['entries'][0][field] = value
                self.bind(); self.refuse()
        self.manifest = copy.deepcopy(originals['manifest'])
        self.manifest['entries'] *= 2
        self.bind(); self.refuse()

    def test_regular_bounded_strict_json_snapshots(self):
        self.fixture()
        target = self.temp/'policy'; original = target.read_bytes()
        for raw in (b'{}', b'{"schemaVersion":1,"schemaVersion":1}', b'NaN', b'1e999',
                    b'\xff', b'"\\ud800"', b'"\\u0000"', b'['*40+b'0'+b']'*40,
                    b' '*1048577):
            target.write_bytes(raw); self.refuse()
        target.unlink(); target.mkdir(); self.refuse(); target.rmdir()
        os.mkfifo(target); self.refuse(); target.unlink()
        target.symlink_to('/dev/null'); self.refuse(); target.unlink()
        target.write_bytes(original)
        for name in ('manifest','receipt','source','verification'):
            path = self.temp/name; data = path.read_bytes()
            path.write_bytes(data+b' '); self.refuse(); path.write_bytes(data)
            path.unlink(); os.mkfifo(path); self.refuse(); path.unlink(); path.write_bytes(data)
        for name in ('manifestFile','receiptFile'):
            policy = dict(self.policy); policy[name] = '/dev/null'
            self.write('policy',policy); self.refuse()
        self.write('policy', self.policy)
        self.assertEqual(self.load()['contextDigest'],self.context)

    def test_owner_source_verification_and_exact_decision_joins(self):
        self.fixture()
        from t3compose import records, jsonio
        _, path = records.location(self.local, self.manifest['repository'], 'SYNTHETIC')
        event_file = path/'events/1.json'
        event = json.loads(event_file.read_bytes())
        import copy
        for key, values in dict(decidedBy=['manager','coordinator'], answer=['unrelated',{'unknown':'pending'}],
                                date=['2026-11-03'], question=['generic approval',
                                'privacy-artifact/v1 approve other '+self.context,
                                'privacy-artifact/v1 approve '+self.receipt['receiptId']+' sha256:'+'0'*64]).items():
            for value in values:
                changed = copy.deepcopy(event); changed['data'][key] = value
                event_file.write_bytes(jsonio.canonical_json(changed)); self.refuse()
        event_file.write_bytes(jsonio.canonical_json(event))
        event_file.unlink(); self.refuse()

    def test_fresh_terminal_revocation_even_after_context_change(self):
        self.fixture()
        first = self.load()
        self.decision('revoke')
        self.refuse()
        self.source['sourceRef'] = 'synthetic:changed-source'
        self.bind()
        self.receipt['approvalSeq'] = 3
        self.bind()
        self.decision('approve', answer=self.source['quote'], date=self.source['date'])
        self.refuse()
        self.assertEqual(first['authorityEventCount'], 1)

    def test_unknown_nonowner_and_unresolved_authority_fail_closed(self):
        self.fixture()
        self.decision('unknown-action'); self.refuse()

    def test_nonowner_revocation_cannot_be_ignored(self):
        self.fixture()
        self.decision('revoke', owner='manager'); self.refuse()

    def test_local_identity_commit_ancestry_and_no_ambient_origin_read(self):
        from t3compose import privacy, records
        self.fixture()
        with patch.object(records, 'git', side_effect=AssertionError('ambient Git')):
            self.load()
        for base in ('HEAD','a'*40,True): self.refuse(base=base)
        self.git('checkout','--orphan','disconnected')
        other = self.commit('unrelated')
        self.refuse(base=other)
        self.git('remote','set-url','origin','https://example.invalid/other/repo.git')
        self.refuse()

    def replace_approval(self):
        from t3compose import records, jsonio
        _, path = records.location(self.local, 'example.invalid/team/repo', 'SYNTHETIC')
        target = path/'events/1.json'
        event = json.loads(target.read_bytes())
        event['data'] = dict(question='privacy-artifact/v1 approve '+self.receipt['receiptId']+' '+self.context,
                             answer=self.source['quote'], date=self.source['date'], decidedBy='owner')
        target.write_bytes(jsonio.canonical_json(event))

    def test_repinned_evidence_rejects_semantic_mismatches(self):
        # Even a matching raw receipt/owner question cannot bless an invalid
        # verifier or a source referring to a different inventory context.
        import copy
        self.fixture()
        original = copy.deepcopy(self.verification)
        for key, value in [('result','approved'), ('method','label-only'), ('verifier',' '),
                           ('sourceRef',''), ('inventoryDigest','sha256:'+'0'*64),
                           ('baselineEvidence',dict(repository=self.manifest['repository'],
                            commit='0'*40, objectFormat='sha1', entriesDigest=self.digest(self.manifest['entries'])))]:
            self.verification = copy.deepcopy(original)
            self.verification[key] = value
            self.bind(); self.replace_approval(); self.refuse()
        self.verification = original
        source = copy.deepcopy(self.source)
        for key, value in [('sourceRef',''), ('date','2026-02-30'), ('quote',' '), ('receiptId','other')]:
            self.source = copy.deepcopy(source); self.source[key] = value
            self.bind()
            # malformed date may not be written into valid records; keep the
            # original decision while asserting the input fails closed.
            if key != 'date': self.replace_approval()
            self.refuse()
        self.source = source
        self.bind(); self.replace_approval()
        self.assertEqual(self.load()['contextDigest'], self.context)

    def test_exact_accepted_bytes_and_one_snapshot_per_reference(self):
        from t3compose import privacy
        self.fixture()
        original = privacy._policy_snapshot
        snapshots = []
        policy_bytes = (self.temp/'policy').read_bytes()
        def replaced(path):
            value = original(path)
            snapshots.append(Path(path).name)
            # Replacement after the accepted read cannot alter accepted bytes.
            Path(path).write_bytes(b'{}')
            return value
        with patch.object(privacy, '_policy_snapshot', side_effect=replaced):
            result = self.load()
        self.assertEqual(sorted(snapshots), ['manifest','policy','receipt','source','verification'])
        self.assertEqual(result['policyFileDigest'], self.digest(policy_bytes))
        self.refuse()

    def test_malformed_or_unresolved_scoped_authority(self):
        from t3compose import records, jsonio
        self.fixture()
        self.decision('approve')
        _, path = records.location(self.local, self.manifest['repository'], 'SYNTHETIC')
        target = path/'events/2.json'
        event = json.loads(target.read_bytes())
        import copy
        for question in ('privacy-artifact', 'privacy-artifact/v2 revoke bad',
                         'privacy-artifact/v1 revoke '+self.receipt['receiptId']):
            changed = copy.deepcopy(event); changed['data']['question'] = question
            target.write_bytes(jsonio.canonical_json(changed)); self.refuse()
        for key in ('answer','decidedBy','date'):
            changed = copy.deepcopy(event); changed['data'][key] = {'unknown':'pending'}
            # Existing schema requires all resolution fields together; corrupt
            # and partially unresolved streams must fail too.
            target.write_bytes(jsonio.canonical_json(changed)); self.refuse()


class OfflineInheritedArtifacts(OfflineTestCase):
    setUp = OfflinePrivacy.setUp
    git = OfflinePrivacy.git
    commit = OfflinePrivacy.commit
    invoke = OfflinePrivacy.invoke
    data = OfflinePrivacy.data
    digest = OfflineArtifactProvenance.digest
    write = OfflineArtifactProvenance.write
    fixture = OfflineArtifactProvenance.fixture
    bind = OfflineArtifactProvenance.bind
    decision = OfflineArtifactProvenance.decision
    replace_approval = OfflineArtifactProvenance.replace_approval

    def inventory(self, body=b'\0opaque', name='asset.bin'):
        self.base = self.commit('inventory baseline', {name: body})
        oid = self.git('rev-parse', 'HEAD:'+name)
        def actual():
            self.manifest['entries'] = [dict(path=name, mode='100644', blobOid=oid, size=len(body))]
            self.verification['baselineEvidence']['entriesDigest'] = self.digest(self.manifest['entries'])
            self.verification['inventoryDigest'] = self.write('manifest', self.manifest)
        self.fixture(actual)
        return self.commit('inherited')

    def scan(self, commits=None, **options):
        from t3compose import privacy
        head = self.git('rev-parse', 'HEAD')
        args = dict(base=self.base, head=head, branch='main', privacy_policy=self.temp/'policy')
        args.update(options)
        return privacy.scan_commits(self.repo, self.local, [head] if commits is None else commits, **args)

    def rejected(self, commits=None, **options):
        with self.assertRaises(cli.CommandError) as error:
            self.scan(commits, **options)
        for private in (str(self.temp), 'syn-secret', 'SYNTHETIC ONLY', 'synthetic:owner'):
            self.assertNotIn(private, str(error.exception))

    def test_opaque_raw_nonpass_and_bounded_reads(self):
        from t3compose import privacy, identifier_list
        for body in (b'\0opaque', b'\xff\xd8'+b'x'*(privacy.MAX_BYTES+1), b'x'*(privacy.MAX_BYTES+1)):
            with self.subTest(size=len(body)):
                if (self.repo/'asset.bin').exists():
                    self.git('remote', 'remove', 'origin')
                    import shutil
                    shutil.rmtree(self.local/'repos') if (self.local/'repos').exists() else None
                    # Each inventory gets its own independent authority namespace.
                    self.local = self.temp/('state-'+str(len(body))+'-'+str(body[0]))
                    self.list_file = self.local/'state/private-identifiers.txt'
                    self.list_file.parent.mkdir(parents=True)
                    self.list_file.write_text('syn-secret\nsyn-other\n')
                head = self.inventory(body)
                original = privacy._git
                def guarded(repo, *argv, **kw):
                    if argv[:2] == ('cat-file', 'blob') and len(body) > privacy.MAX_BYTES:
                        self.fail('oversized body loaded')
                    return original(repo, *argv, **kw)
                with patch.object(privacy, '_git', side_effect=guarded), patch.object(
                        identifier_list, 'load_identifiers', wraps=identifier_list.load_identifiers) as load:
                    result = self.scan()
                self.assertEqual(load.call_count, 1)
                self.assertFalse(result['ok'])
                self.assertEqual(result['findings'], [])
                self.assertEqual(result['action'], 'policy-evaluation-required')
                coverage = result['coverage']
                opaque = coverage['approvedUnscanned'][0]
                self.assertEqual(opaque['size'], len(body))
                self.assertFalse(opaque['contentScreened'])
                self.assertEqual(coverage['commits'], [head])
                self.assertEqual(coverage['selectionDigest'], self.digest([head]))
                self.assertEqual(coverage['provenance']['contextDigest'], self.context)
                self.rejected(privacy_policy=None)

    def test_supported_inventory_text_and_metadata_are_screened(self):
        head = self.inventory(b'syn-secret', 'syn-other/asset')
        self.commit('syn-secret', {'target': 'syn-other', 'ignored.lock': b'\0'})
        os.symlink('syn-secret', self.repo/'link')
        head = self.commit('links')
        result = self.scan(branch='syn-secret', texts=[('syn-other', 'syn-secret')])
        self.assertFalse(result['ok'])
        self.assertEqual(result['coverage']['approvedUnscanned'], [])
        fields = {v['field'] for v in result['findings']}
        self.assertTrue({'branch','label','text','path','file'} <= fields)
        encoded = json.dumps(result)
        self.assertNotIn('syn-secret', encoded)
        self.assertNotIn('syn-other', encoded)
        self.assertEqual(result['coverage']['excluded'], [dict(commit=head, file='ignored.lock', reason='lock-suffix')])
        self.assertTrue(any(v['file'] == 'link' and v['field'] == 'file' for v in result['coverage']['screened']))

    def test_actual_baseline_and_base_inventory_tuples(self):
        import copy
        self.inventory()
        original = copy.deepcopy(self.manifest)
        for field, value in [('path','absent'), ('blobOid','a'*40), ('size',100)]:
            self.manifest = copy.deepcopy(original)
            self.manifest['entries'][0][field] = value
            self.verification['baselineEvidence']['entriesDigest'] = self.digest(self.manifest['entries'])
            self.verification['inventoryDigest'] = self.write('manifest', self.manifest)
            self.bind(); self.replace_approval()
            self.rejected([])
        self.manifest = original
        self.verification['inventoryDigest'] = self.write('manifest', self.manifest)
        self.verification['baselineEvidence']['entriesDigest'] = self.digest(self.manifest['entries'])
        self.bind(); self.replace_approval()
        changed = self.commit('changed base', {'asset.bin': 'safe'})
        self.rejected([], base=changed)

    def test_transition_matrix_and_intermediate_history(self):
        self.inventory()
        anchor = self.git('rev-parse', 'HEAD')
        for kind in ('byte','mode','rename','copy','delete-readd','change-revert','add-delete'):
            with self.subTest(kind=kind):
                self.git('reset','--hard',anchor)
                first = None
                if kind == 'byte': self.commit('changed', {'asset.bin': b'\0changed'})
                elif kind == 'mode':
                    os.chmod(self.repo/'asset.bin', 0o755); self.commit('mode')
                elif kind == 'rename':
                    (self.repo/'asset.bin').rename(self.repo/'renamed'); self.commit('rename')
                elif kind == 'copy': self.commit('copy', {'copied': b'\0opaque'})
                elif kind == 'delete-readd':
                    (self.repo/'asset.bin').unlink(); self.commit('deleted')
                    self.commit('restored', {'asset.bin': b'\0opaque'})
                elif kind == 'change-revert':
                    first = self.commit('changed', {'asset.bin': b'\0changed'})
                    self.commit('reverted', {'asset.bin': b'\0opaque'})
                else:
                    first = self.commit('added', {'unknown': b'\0new'})
                    (self.repo/'unknown').unlink(); self.commit('removed')
                self.rejected([first, self.git('rev-parse','HEAD')] if first else None)
        self.git('reset','--hard',anchor)
        (self.repo/'asset.bin').unlink(); self.commit('deletion allowed')
        self.assertTrue(self.scan()['ok'])

    def test_root_and_every_merge_parent_even_outside_selection(self):
        self.inventory()
        anchor = self.git('rev-parse','HEAD')
        self.rejected([self.base])  # inventory introduction is not inheritance
        for changed in (None, b'\0different'):
            self.git('checkout','-B','side',anchor)
            if changed is None: (self.repo/'asset.bin').unlink()
            else: (self.repo/'asset.bin').write_bytes(changed)
            side = self.commit('side')
            self.git('checkout','-B','main',anchor)
            self.commit('main')
            # Construct real merge commit retaining main tree; parent side differs.
            tree = self.git('rev-parse','HEAD^{tree}')
            merge = self.git('commit-tree',tree,'-p',self.git('rev-parse','HEAD'),'-p',side,
                             input=b'merge\n')
            self.rejected([merge], head=merge)
        # Both exact parents are eligible, even when outside the selection.
        merge = self.git('commit-tree',tree,'-p',anchor,'-p',self.git('rev-parse','HEAD'),input=b'merge\n')
        self.assertFalse(self.scan([merge], head=merge)['ok'])

    def test_fresh_authority_list_and_snapshot_binding(self):
        self.inventory()
        first = self.scan()
        reordered = self.scan([], texts=[('a','b'), ('a','c')])
        self.assertNotEqual(first['coverage']['selectionDigest'], reordered['coverage']['selectionDigest'])
        self.assertEqual(len(reordered['coverage']['texts']), 2)
        self.decision('revoke'); self.rejected()
        self.assertEqual(first['coverage']['provenance']['authorityEventCount'], 1)
        self.list_file.write_text('different\n'); self.rejected([])

    def test_real_entry_nonpass_and_late_failure_empty_stdout(self):
        self.inventory()
        data = self.data(self.invoke('--privacy-policy',str(self.temp/'policy'),entry=True), 1)
        self.assertEqual(data['action'], 'policy-evaluation-required')
        self.commit('late syn-secret', {'zz-unknown': b'\0unapproved'})
        code, out, err = self.invoke('--privacy-policy',str(self.temp/'policy'),entry=True)
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertNotIn('syn-secret', err)
        self.assertNotIn(str(self.temp), err)

    def test_read_failure_is_never_an_opaque_fallback(self):
        from t3compose import privacy
        self.inventory()
        original = privacy._git
        def failed(repo, *argv, **kw):
            if argv[:2] == ('cat-file','blob'):
                raise cli.CommandError('privacy Git read failed')
            return original(repo, *argv, **kw)
        with patch.object(privacy, '_git', side_effect=failed): self.rejected()

    def test_coverage_shape_and_digest_bindings(self):
        from t3compose import privacy
        head = self.inventory()
        result = self.scan(texts=[('title','safe'), ('title','changed')])
        coverage = result['coverage']
        self.assertEqual(set(coverage), set('version repository base head commits selectionDigest branchDigest texts provenance screened approvedUnscanned excluded'.split()))
        self.assertEqual(coverage['version'], 'artifact-coverage/v1')
        self.assertEqual(coverage['base'], self.base)
        self.assertEqual(coverage['head'], head)
        self.assertEqual(coverage['branchDigest'], self.digest(b'main'))
        self.assertEqual(coverage['texts'], [dict(labelDigest=self.digest(b'title'), textDigest=self.digest(v))
                                              for v in (b'safe',b'changed')])
        expected = set('receiptId authorityWorkId approvalSeq inventoryContextDigest contextDigest policyFileDigest manifestDigest receiptDigest sourceDigest verificationDigest policyVersion policyDigest listDigest patternCount authorityEventCount authorityCheckpoint authorityWorkDigest authorityEventsDigest observedStart observedEnd baseline'.split())
        self.assertEqual(set(coverage['provenance']), expected)
        for item in coverage['screened']:
            self.assertEqual(set(item), set('field commit file label contentDigest'.split()))
        self.assertEqual(set(coverage['approvedUnscanned'][0]), set('commit file mode blobOid size receiptId contextDigest contentScreened'.split()))
        # A second inherited commit makes order independently observable.
        later = self.commit('later')
        forward = self.scan([head,later])['coverage']
        reverse = self.scan([later,head])['coverage']
        self.assertNotEqual(forward['selectionDigest'], reverse['selectionDigest'])
        with patch.object(privacy.identifier_list, 'load_identifiers', side_effect=cli.CommandError('invalid list')):
            self.rejected([])

    def test_supported_new_changed_and_empty_text_remain_screened(self):
        self.inventory(b'clean inventory')
        self.commit('new text', {'new':'safe', 'empty':''})
        self.assertTrue(self.scan()['ok'])
        self.commit('changed text', {'asset.bin':'syn-secret', 'new':'syn-other'})
        data = self.scan()
        self.assertFalse(data['ok'])
        self.assertEqual(data['coverage']['approvedUnscanned'], [])
        self.assertEqual({v['file'] for v in data['findings']}, {'asset.bin','new'})

    def test_missing_parent_blob_and_malformed_tree_fail_closed(self):
        from t3compose import privacy
        head = self.inventory()
        tree = self.git('rev-parse','HEAD^{tree}')
        root = self.git('commit-tree',tree,input=b'root\n')
        self.rejected([root], head=root)
        broken = ('tree '+tree+'\nparent '+'a'*40+'\nauthor Synthetic <synthetic@example.invalid> 1 +0000\n'
                  'committer Synthetic <synthetic@example.invalid> 1 +0000\n\nbroken\n')
        missing = self.git('hash-object','-t','commit','-w','--stdin',input=broken.encode())
        self.rejected([missing], head=missing)
        original = privacy._git
        def malformed(repo, *argv, **kw):
            if argv[:1] == ('ls-tree',) and head in argv:
                return b'invalid\0'
            return original(repo, *argv, **kw)
        with patch.object(privacy, '_git', side_effect=malformed): self.rejected()
        oid = self.manifest['entries'][0]['blobOid']
        (self.repo/'.git/objects'/oid[:2]/oid[2:]).unlink()
        self.rejected()

    def test_late_object_failure_has_no_completed_output(self):
        from t3compose import privacy
        self.inventory()
        head = self.commit('syn-secret', {'zz-last':'safe'})
        last = self.git('rev-parse','HEAD:zz-last')
        original = privacy._git
        def unreadable(repo, *argv, **kw):
            if argv[:2] == ('cat-file','blob') and argv[-1] == last:
                raise cli.CommandError('privacy Git read failed')
            return original(repo, *argv, **kw)
        with patch.object(privacy, '_git', side_effect=unreadable):
            code, out, err = self.invoke('--privacy-policy',str(self.temp/'policy'),entry=True)
        self.assertEqual(code, 2)
        self.assertEqual(out, '')
        self.assertNotIn('syn-secret', err)
        self.assertNotIn(str(self.temp), err)


class OfflineLiteralProofs(OfflineTestCase):
    """Synthetic literal evidence only; inherits the offline repository fixture."""

    git = OfflinePrivacy.git
    commit = OfflinePrivacy.commit

    def proof(self, patterns, text, snapshot=None):
        from t3compose import privacy
        return privacy._literal_proofs(self.repo, patterns, text,
                                       public_commit=snapshot or self.public)

    def setUp(self):
        OfflinePrivacy.setUp(self)
        self.public = self.commit('synthetic snapshot', {'product': 'token beta café\n'})

    def test_literal_schema_offsets_alternatives_and_unknown(self):
        from t3compose import privacy
        proof = self.proof(['token|gamma', 'café'], 'café\ntoken token gamma')
        self.assertFalse(proof['complete'])
        self.assertEqual(proof['reason'], 'missing-witness')
        self.assertEqual(set(proof), set('proofVersion publicCommit listDigest inputDigest '
                                       'localeDigest complete occurrences reason'.split()))
        self.assertEqual(proof['proofVersion'], 'literal-proof/v1')
        self.assertEqual(proof['inputDigest'], privacy._digest('café\ntoken token gamma'.encode()))
        self.assertEqual(proof['listDigest'], privacy._canonical_digest(['token|gamma', 'café']))
        rows = proof['occurrences']
        self.assertEqual([(r['patternLine'], r['byteOffset'], r['byteLength']) for r in rows],
                         [(1, 6, 5), (1, 12, 5), (1, 18, 5), (2, 0, 5)])
        self.assertIsNone(rows[2]['publicBlobOid'])
        for i in (0, 1, 3):
            self.assertEqual(rows[i]['publicBlobOid'], self.git('rev-parse', self.public+':product'))
            self.assertEqual(rows[i]['publicContentDigest'], privacy._digest(b'token beta caf\xc3\xa9\n'))
        self.assertNotIn('token', json.dumps(proof))

    def test_anchors_lf_and_final_lf(self):
        for text in ('token', 'token\n', 'beta\ntoken', 'beta\ntoken\n'):
            proof = self.proof(['^token$'], text)
            self.assertTrue(proof['complete'])
            self.assertEqual(proof['occurrences'][0]['byteOffset'], text.index('token'))
        self.assertEqual(self.proof(['^token$'], 'xtoken')['occurrences'], [])

    def test_unsupported_empty_contextual_and_overlap_refused(self):
        for patterns in ([''], ['a*'], ['^$'], ['a|'], ['(token)'], ['[ab]'],
                         [r'\btoken'], ['a.b'], ['token', 'token'], ['aba'],
                         ['token|tokenbet'], ['ab', 'bc'], ['token\r']):
            with self.subTest(patterns=patterns):
                proof = self.proof(patterns, 'token aba tokenbet abc')
                self.assertFalse(proof['complete'])
                self.assertEqual(proof['occurrences'], [])

    def test_immutable_snapshot_and_no_list_reload(self):
        from t3compose import identifier_list
        with patch.object(identifier_list, 'load_identifiers', side_effect=AssertionError('reload')):
            self.assertTrue(self.proof(('token',), 'token')['complete'])
            changed = self.commit('changed', {'product': 'beta'})
            self.assertFalse(self.proof(['token'], 'token', changed)['complete'])
            self.assertTrue(self.proof(['token'], 'token')['complete'])
        for snapshot in ('HEAD', self.public[:8], '0'*40, self.git('rev-parse', self.public+':product')):
            with self.subTest(snapshot=snapshot):
                with self.assertRaises(cli.CommandError):
                    self.proof(['token'], 'token', snapshot)

    def test_opaque_excluded_symlink_and_oversized_not_witnesses(self):
        (self.repo/'product').unlink()
        (self.repo/'link').symlink_to('token')
        snapshot = self.commit('unsupported candidates', {'opaque': b'token\0',
            'invalid': b'token\xff', 'data.lock': 'token', 'large': b'token'+b'x'*1048576})
        from t3compose import privacy
        original = privacy._object
        def bounded(repo, kind, oid):
            self.assertNotEqual(oid, self.git('rev-parse', snapshot+':large'))
            return original(repo, kind, oid)
        with patch.object(privacy, '_object', side_effect=bounded):
            self.assertFalse(self.proof(['token'], 'token', snapshot)['complete'])

    def test_private_scratch_argv_modes_cleanup_and_exact_witness(self):
        from t3compose import privacy
        import stat
        original = process.run
        directories = set()
        def inspect(argv, **kwargs):
            if argv[0] == 'grep' and ('-b' in argv or '-F' in argv):
                self.assertNotIn('token', ' '.join(argv))
                path = Path(argv[argv.index('-f')+1])
                directories.add(path.parent)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                for child in path.parent.iterdir():
                    self.assertEqual(stat.S_IMODE(child.stat().st_mode), 0o600)
                self.assertEqual(path.read_bytes(), b'token\n')
            return original(argv, **kwargs)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(process, 'run', side_effect=inspect), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            proof = self.proof(['token'], 'token token')
        self.assertTrue(proof['complete'])
        self.assertEqual(out.getvalue()+err.getvalue(), '')
        self.assertTrue(directories)
        self.assertTrue(all(not path.exists() for path in directories))
        # A case-folded witness, even if a backend claims success, is not exact.
        snapshot = self.commit('case differs', {'product': 'TOKEN'})
        def hostile(argv, **kwargs):
            if argv[0] == 'grep' and '-F' in argv:
                return subprocess.CompletedProcess(argv, 0)
            return original(argv, **kwargs)
        with patch.object(process, 'run', side_effect=hostile):
            proof = self.proof(['token'], 'token', snapshot)
        self.assertFalse(proof['complete'])
        self.assertEqual(proof['occurrences'], [])

    def test_malformed_grep_output_status_timeout_and_no_partial_proof(self):
        from t3compose import privacy
        original = process.run
        for status, output, diagnostic in ((0, b'', b''), (1, b'0:token\n', b''),
                (True, b'0:token\n', b''), (2, b'', b'synthetic diagnostic'),
                (0, b'1:token\n', b''), (0, b'0:token', b''),
                (0, b'00:token\n', b''), (0, b'0:wrong\n', b''),
                (0, b'0:token\n0:token\n', b''), (0, b'x'*1048577, b'')):
            def malformed(argv, **kwargs):
                if argv[0] == 'grep' and '-b' in argv:
                    kwargs['stdout'].write(output)
                    kwargs['stderr'].write(diagnostic)
                    return subprocess.CompletedProcess(argv, status)
                return original(argv, **kwargs)
            with self.subTest(status=status, length=len(output)), patch.object(process, 'run', side_effect=malformed):
                proof = self.proof(['token'], 'token')
                self.assertFalse(proof['complete'])
                self.assertEqual(proof['occurrences'], [])
        def timeout(argv, **kwargs):
            if argv[0] == 'grep' and '-F' in argv:
                raise process.ProcessError('timeout')
            return original(argv, **kwargs)
        with patch.object(process, 'run', side_effect=timeout):
            proof = self.proof(['token'], 'token')
        self.assertEqual(proof['reason'], 'proof-read-failed')
        self.assertEqual(proof['occurrences'], [])

    def test_raw_match_coverage_environment_and_bounds(self):
        from t3compose import privacy, identifier_list
        with patch.object(identifier_list, 'match_identifiers', return_value=[1, 2]):
            self.assertEqual(self.proof(['token'], 'token')['reason'], 'unaccounted-match')
        with patch.dict(os.environ, {'GREP_OPTIONS': '-i'}):
            self.assertEqual(self.proof(['token'], 'token')['reason'], 'unsupported-environment')
        with patch.object(privacy, '_literal_environment', side_effect=[{}, {'LANG': 'changed'}]):
            self.assertEqual(self.proof(['token'], 'token')['reason'], 'environment-changed')
        for patterns, text in (([], ''), ('token', 'token'), (['token\n'], ''),
                                (['token'], b'token'), (['token'], 'x'*1048577)):
            with self.subTest(kind=type(text)), self.assertRaises(cli.CommandError):
                self.proof(patterns, text)
        with patch.object(privacy, '_LITERAL_OCCURRENCES', 1):
            self.assertEqual(self.proof(['token'], 'token token')['reason'], 'proof-limit')

    def test_snapshot_format_type_and_read_failure(self):
        from t3compose import privacy
        for repo in (None, '', object(), 'bad\0path'):
            with self.assertRaises(cli.CommandError):
                privacy._literal_proofs(repo, ['token'], 'token', public_commit=self.public)
        original = privacy._git
        def wrong_format(repo, *args, **kwargs):
            if args == ('rev-parse', '--show-object-format'):
                return b'sha256\n'
            return original(repo, *args, **kwargs)
        with patch.object(privacy, '_git', side_effect=wrong_format), self.assertRaises(cli.CommandError):
            self.proof(['token'], 'token')
        original_object = privacy._object
        def failed_blob(repo, kind, oid):
            if kind == 'blob':
                raise cli.CommandError('synthetic read failure')
            return original_object(repo, kind, oid)
        with patch.object(privacy, '_object', side_effect=failed_blob):
            proof = self.proof(['token'], 'token')
        self.assertFalse(proof['complete'])
        self.assertEqual(proof['occurrences'], [])

    def test_unmatched_supported_rows_and_executable_witness(self):
        (self.repo/'product').chmod(0o755)
        snapshot = self.commit('executable text')
        proof = self.proof(['token', 'delta'], 'token', snapshot)
        self.assertTrue(proof['complete'])
        self.assertEqual([r['patternLine'] for r in proof['occurrences']], [1])
        self.assertTrue(self.proof(['token'], '')['complete'])

    def test_omitted_occurrence_and_literal_limits_refuse(self):
        from t3compose import privacy
        original = process.run
        calls = 0
        def omitted(argv, **kwargs):
            nonlocal calls
            if argv[0] == 'grep' and '-b' in argv:
                calls += 1
                if calls == 1:
                    kwargs['stdout'].write(b'0:token\n')
                    return subprocess.CompletedProcess(argv, 0)
            return original(argv, **kwargs)
        with patch.object(process, 'run', side_effect=omitted):
            proof = self.proof(['token'], 'token token')
        self.assertEqual(proof['reason'], 'unaccounted-match')
        self.assertEqual(proof['occurrences'], [])
        for patterns in (['|'.join('q'+str(i)+'z' for i in range(65))],
                         ['x'*1024]*9):
            self.assertFalse(self.proof(patterns, 'token')['complete'])


    def test_corrections_completed_witness_search_stops_before_unrelated_limits(self):
        from t3compose import privacy
        files = {'000-witness': 'token'}
        files.update({f'z-{i:03}': f'unrelated-{i}' for i in range(129)})
        snapshot = self.commit('wide synthetic snapshot', files)
        with patch.object(privacy, '_artifact_blob_size', wraps=privacy._artifact_blob_size) as sizes:
            proof = self.proof(['token'], 'token', snapshot)
        self.assertTrue(proof['complete'], proof['reason'])
        self.assertEqual(sizes.call_count, 1)
        self.assertEqual(proof['occurrences'][0]['publicBlobOid'],
                         self.git('rev-parse', snapshot+':000-witness'))
        unknown = self.proof(['delta'], 'delta', snapshot)
        self.assertFalse(unknown['complete'])
        self.assertEqual(unknown['reason'], 'proof-limit')
        self.assertEqual(unknown['occurrences'], [])
        with patch.object(privacy, '_artifact_blob_size', side_effect=AssertionError('unneeded body fact')):
            self.assertTrue(self.proof(['token'], '', snapshot)['complete'])

    def test_corrections_non_utf8_locale_is_a_sanitized_refusal(self):
        with patch.dict(os.environ, {'LC_SYNTHETIC': 'synthetic-private-\udcff'}):
            with self.assertRaises(cli.CommandError) as caught:
                self.proof(['token'], 'token')
        self.assertNotIn('synthetic-private', str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(self.proof(['token'], 'token')['complete'])
