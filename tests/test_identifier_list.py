"""Offline tests for private identifier loading and grep -E matching."""
import contextlib
import importlib
import io
from pathlib import Path
import subprocess
import traceback
from unittest.mock import patch

from t3compose import cli, process
from tests.support import FakeRunner, OfflineTestCase


def _write_list(local_dir, content):
    target = Path(local_dir) / "state" / "private-identifiers.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    return target


def _split_lf(text):
    return [] if not text else (text[:-1] if text.endswith("\n") else text).split("\n")


class OfflineIdentifierList(OfflineTestCase):
    def test_import_has_no_io_or_process_side_effects(self):
        import t3compose.identifier_list as module
        with patch.object(process, "run", side_effect=AssertionError("no process at import")), \
                patch("os.open", side_effect=AssertionError("no os.open at import")):
            importlib.reload(module)

    def test_load_missing_unreadable_empty_and_edited_between_calls(self):
        from t3compose.identifier_list import load_identifiers, match_identifiers

        for bad_dir in (None, "", 123, "bad\x00dir"):
            with self.assertRaises(cli.CommandError) as bad_dir_err:
                load_identifiers(bad_dir)
            self.assertEqual(bad_dir_err.exception.code, cli.ExitCode.INVALID_INPUT)

        local_dir = self.temp / "synthetic-local"
        with self.assertRaises(cli.CommandError) as missing_err:
            load_identifiers(local_dir)
        self.assertEqual(missing_err.exception.code, cli.ExitCode.INVALID_INPUT)
        self.assertNotIn(str(local_dir), str(missing_err.exception))

        list_path = _write_list(local_dir, b"")
        with self.assertRaises(cli.CommandError) as empty_err:
            load_identifiers(local_dir)
        self.assertEqual(empty_err.exception.code, cli.ExitCode.INVALID_INPUT)
        self.assertIn("empty", str(empty_err.exception))

        self.allow_program("grep")
        _write_list(local_dir, "syn-alpha\nsyn-beta\n")
        list_path.chmod(0)
        self.addCleanup(lambda: list_path.exists() and list_path.chmod(0o600))
        with self.assertRaises(cli.CommandError) as unread_err:
            load_identifiers(local_dir)
        self.assertEqual(unread_err.exception.code, cli.ExitCode.INVALID_INPUT)
        self.assertNotIn(str(list_path), str(unread_err.exception))
        list_path.chmod(0o600)

        dir_local = self.temp / "dir-local"
        (dir_local / "state" / "private-identifiers.txt").mkdir(parents=True)
        with self.assertRaises(cli.CommandError) as dir_err:
            load_identifiers(dir_local)
        self.assertEqual(dir_err.exception.code, cli.ExitCode.INVALID_INPUT)

        first = load_identifiers(local_dir)
        self.assertEqual(first, ["syn-alpha", "syn-beta"])
        self.assertEqual(match_identifiers(first, "contains syn-beta here"), [2])
        first.append("mutated-in-place")
        self.assertEqual(load_identifiers(local_dir), ["syn-alpha", "syn-beta"])

        _write_list(local_dir, "syn-gamma\nsyn-alpha\n")
        second = load_identifiers(local_dir)
        self.assertEqual(second, ["syn-gamma", "syn-alpha"])
        self.assertEqual(match_identifiers(second, "contains syn-alpha and syn-beta"), [2])

        _write_list(local_dir, "syn-gamma\n[unclosed\n")
        with self.assertRaises(cli.CommandError) as edited_bad:
            load_identifiers(local_dir)
        self.assertEqual(edited_bad.exception.code, cli.ExitCode.INVALID_INPUT)
        self.assertIn("line 2", str(edited_bad.exception))
        self.assertNotIn("unclosed", str(edited_bad.exception))

    def test_invalid_patterns_and_bounds_never_expose_private_values(self):
        from t3compose import identifier_list
        from t3compose.identifier_list import load_identifiers, match_identifiers

        self.allow_program("grep")
        local_dir = self.temp / "invalid-local"
        sentinel_pat = " [syn-bad-regex-901"
        sentinel_text = "syn-secret-payload-902"

        # Line 1 valid and matches text, line 2 invalid: must not conceal line 2.
        _write_list(local_dir, "syn-secret\n" + sentinel_pat + "\n")
        for fn in (
            lambda: load_identifiers(local_dir),
            lambda: match_identifiers(["syn-secret", sentinel_pat], sentinel_text),
        ):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(cli.CommandError) as ctx:
                    fn()
            self.assertEqual(ctx.exception.code, cli.ExitCode.INVALID_INPUT)
            self.assertIn("line 2", str(ctx.exception))
            for leaked in (sentinel_pat, "syn-bad-regex-901", sentinel_text, "brackets", str(local_dir)):
                self.assertNotIn(leaked, str(ctx.exception))
                self.assertNotIn(leaked, repr(ctx.exception))
                self.assertNotIn(leaked, "".join(traceback.format_exception(
                    type(ctx.exception), ctx.exception, ctx.exception.__traceback__)))
            self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

        for bad_bytes in (
            b"\xff\xfe\n",
            b"syn-ok\x00hidden\n",
            b"a" * (identifier_list.MAX_LIST_BYTES + 1),
            b"x\n" * (identifier_list.MAX_PATTERNS + 1),
            b"p" * (identifier_list.MAX_PATTERN_BYTES + 1) + b"\n",
        ):
            _write_list(local_dir, bad_bytes)
            with self.subTest(size=len(bad_bytes)), self.assertRaises(cli.CommandError) as err_ctx:
                load_identifiers(local_dir)
            self.assertEqual(err_ctx.exception.code, cli.ExitCode.INVALID_INPUT)

        valid_ids = ["syn-token"]
        for bad_ids, bad_text in (
            (None, "clean"),
            ("syn-token", "clean"),
            ([], "clean"),
            ([123], "clean"),
            (["line1\nline2"], "clean"),
            (["nul\x00pat"], "clean"),
            (["\ud800"], "clean"),
            (["p" * (identifier_list.MAX_PATTERN_BYTES + 1)], "clean"),
            (["p" * identifier_list.MAX_PATTERN_BYTES] * 65, "clean"),
            (["x"] * (identifier_list.MAX_PATTERNS + 1), "clean"),
            (valid_ids, b"bytes-not-str"),
            (valid_ids, "nul\x00text"),
            (valid_ids, "\ud800"),
            (valid_ids, "t" * (identifier_list.MAX_TEXT_BYTES + 1)),
        ):
            with self.assertRaises(cli.CommandError) as call_err:
                match_identifiers(bad_ids, bad_text)
            self.assertEqual(call_err.exception.code, cli.ExitCode.INVALID_INPUT)

    def test_fake_runner_argv_privacy_and_process_failures(self):
        from t3compose.identifier_list import load_identifiers, match_identifiers

        local_dir = self.temp / "fake-local"
        sentinel = "syn-private-pat-777"
        text_sentinel = "syn-private-text-888"
        _write_list(local_dir, "syn-first\n" + sentinel + "\n")
        seen_files = []

        def fake_grep(argv, **kwargs):
            self.assertEqual(argv[:3], ["grep", "-E", "-f"])
            self.assertEqual(len(argv), 4)
            for token in argv:
                self.assertNotIn(sentinel, token)
                self.assertNotIn("syn-first", token)
            self.assertTrue(kwargs.get("capture_output"))
            self.assertIs(kwargs.get("check"), False)
            self.assertIsInstance(kwargs.get("timeout"), (int, float))
            pat_path = Path(argv[3])
            self.assertTrue(pat_path.exists())
            seen_files.append(pat_path)
            pat_bytes = pat_path.read_bytes()
            rc = 0 if (kwargs.get("input") and b"777" in pat_bytes) else 1
            return subprocess.CompletedProcess(argv, rc, b"matched: " + pat_bytes, b"")

        runner = FakeRunner({"grep": fake_grep})
        with patch.object(process, "run", runner):
            loaded = load_identifiers(local_dir)
            matched = match_identifiers(loaded, text_sentinel)
        self.assertEqual(loaded, ["syn-first", sentinel])
        self.assertEqual(matched, [2])
        self.assertEqual(len(runner.calls), 4)
        for temp_path in seen_files:
            self.assertFalse(temp_path.exists())

        for failure in (
            process.ProcessError("timeout"),
            process.ProcessError("not-found"),
            process.ProcessError("os-error"),
            process.ProcessError("exit", 2),
            subprocess.CompletedProcess(["grep"], 2, b"secret-out", b"secret-err"),
            subprocess.CompletedProcess(["grep"], 3, b"secret-out", b"secret-err"),
            subprocess.CompletedProcess(["grep"], False, b"secret-out", b"secret-err"),
        ):
            def fail_grep(argv, _failure=failure, **kwargs):
                if isinstance(_failure, Exception):
                    raise _failure
                return _failure

            with patch.object(process, "run", FakeRunner({"grep": fail_grep})):
                with self.assertRaises(cli.CommandError) as err_ctx:
                    match_identifiers(["syn-first"], text_sentinel)
                self.assertEqual(err_ctx.exception.code, cli.ExitCode.INVALID_INPUT)
                self.assertIn("line 1", str(err_ctx.exception))
                for secret in ("secret-out", "secret-err", text_sentinel, "syn-first"):
                    self.assertNotIn(secret, str(err_ctx.exception))

    def test_equivalence_with_system_grep_including_ere_duplicates_lf_and_blank_line(self):
        from t3compose.identifier_list import load_identifiers, match_identifiers

        self.allow_program("grep")
        local_dir = self.temp / "equiv-local"
        raw_patterns = (
            "syn-alt-one|syn-alt-two\n"
            "^syn-anchored-line$\n"
            "syn-class-[0-9]{3}-[a-z]+\n"
            "syn.literal.token\n"
            "  syn spaced  \n"
            "syn-alt-one|syn-alt-two\n"
            "syn-tail-end$"
        )
        list_path = _write_list(local_dir, raw_patterns)
        identifiers = load_identifiers(local_dir)
        self.assertEqual(len(identifiers), 7)

        sample_text = (
            "clean opening line\n"
            "has syn-alt-two in middle\n"
            "syn-anchored-line\n"
            "not syn-anchored-line here\n"
            "id syn-class-404-abc and syn.literal.token together\n"
            "keep   syn spaced   exact\n"
            "unicode\u0085same-line\u2028with syn-alt-one\n"
            "final unterminated syn-tail-end"
        )

        manual = process.run(
            ["grep", "-nE", "-f", str(list_path)],
            input=sample_text.encode("utf-8"), capture_output=True, check=False,
        )
        self.assertEqual(manual.returncode, 0)
        grep_text_lines = [int(line.split(b":", 1)[0]) for line in manual.stdout.splitlines()]
        text_lines = _split_lf(sample_text)
        api_text_lines = [
            idx for idx, line in enumerate(text_lines, start=1)
            if match_identifiers(identifiers, line)
        ]
        self.assertEqual(api_text_lines, grep_text_lines)
        self.assertEqual(grep_text_lines, [2, 3, 5, 6, 7, 8])

        expected_per_line = [[], [1, 6], [2], [], [3, 4], [5], [1, 6], [7]]
        for line, expected in zip(text_lines, expected_per_line, strict=True):
            self.assertEqual(match_identifiers(identifiers, line), expected)
        full_matches = match_identifiers(identifiers, sample_text)
        self.assertEqual(full_matches, [1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(all(type(num) is int for num in full_matches))

        # Empty text: both system grep -nE -f and match_identifiers report no match.
        empty_manual = process.run(
            ["grep", "-nE", "-f", str(list_path)],
            input=b"", capture_output=True, check=False,
        )
        self.assertEqual((empty_manual.returncode, empty_manual.stdout), (1, b""))
        self.assertEqual(match_identifiers(identifiers, ""), [])

        # Blank regex line in pattern file: grep -E -f treats an empty pattern line
        # as matching every line of non-empty text and no line of empty text.
        blank_local = self.temp / "blank-line-local"
        blank_list_path = _write_list(blank_local, "syn-only-line-2\n\nsyn-never\n")
        blank_ids = load_identifiers(blank_local)
        self.assertEqual(blank_ids, ["syn-only-line-2", "", "syn-never"])
        blank_text = "unmatched first\nsyn-only-line-2\nunmatched third"
        blank_manual = process.run(
            ["grep", "-nE", "-f", str(blank_list_path)],
            input=blank_text.encode("utf-8"), capture_output=True, check=False,
        )
        self.assertEqual(blank_manual.returncode, 0)
        blank_grep_lines = [int(line.split(b":", 1)[0]) for line in blank_manual.stdout.splitlines()]
        blank_api_lines = [
            idx for idx, line in enumerate(_split_lf(blank_text), start=1)
            if match_identifiers(blank_ids, line)
        ]
        self.assertEqual(blank_api_lines, blank_grep_lines)
        self.assertEqual(blank_grep_lines, [1, 2, 3])
        self.assertEqual(match_identifiers(blank_ids, "unmatched first"), [2])
        self.assertEqual(match_identifiers(blank_ids, "syn-only-line-2"), [1, 2])
        self.assertEqual(match_identifiers(blank_ids, ""), [])
