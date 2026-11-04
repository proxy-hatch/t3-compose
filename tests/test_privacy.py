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
