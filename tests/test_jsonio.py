import builtins
from unittest.mock import patch

from tests.support import OfflineTestCase


class OfflineJSON(OfflineTestCase):
    def test_canonical_json_and_digest(self):
        from t3compose.jsonio import canonical_json, file_digest
        expected = b'{"a":[1,true,null],"z":"\xc3\xa9"}'
        self.assertEqual(canonical_json({"z": "\u00e9", "a": [1, True, None]}), expected)
        self.assertEqual(canonical_json({"a": [1, True, None], "z": "\u00e9"}), expected)
        path = self.temp / "data.json"
        path.write_bytes(b"abc")
        self.assertEqual(file_digest(path),
                         "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        with self.assertRaises(ValueError):
            canonical_json({"bad": float("nan")})

    def test_read_limits_and_invalid_data(self):
        from t3compose.jsonio import read_json
        path = self.temp / "data.json"
        path.write_bytes(b'{"a":1}')
        self.assertEqual(read_json(path, max_bytes=7), {"a": 1})
        for limit in (6, 0, -1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                read_json(path, max_bytes=limit)
        for data in (b"{", b'"\xff"', b'{"a":1,"a":2}', b"NaN", b"Infinity",
                     b'{"x":1e400}', b'{"x":-1e400}', b'"\\ud800"'):
            path.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_json(path)
        with self.assertRaises(OSError):
            read_json(self.temp / "absent")

    def test_read_json_with_digest_single_open_and_raw_bytes(self):
        from t3compose.jsonio import read_json, read_json_with_digest
        path = self.temp / "config.json"
        compact = b'{"a":1}'
        spaced = b'{\n  "a": 1\n}\n'
        compact_sha = "sha256:015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
        spaced_sha = "sha256:5e36b6560513586ced65eca0e8b025fe88edbcab82ba2cb67a5fcb2d7b202383"

        path.write_bytes(compact)
        self.assertEqual(read_json_with_digest(path, max_bytes=7), ({"a": 1}, compact_sha))

        path.write_bytes(spaced)
        self.assertEqual(read_json_with_digest(path), ({"a": 1}, spaced_sha))
        self.assertNotEqual(compact_sha, spaced_sha)

        # Verify single open: unlinking and replacing the path right after open
        # keeps the first open's inode bytes for both value and digest, and never
        # opens the path a second time.
        real_open = builtins.open
        opens = []

        def open_once_then_replace(target, *args, **kwargs):
            opens.append(target)
            handle = real_open(target, *args, **kwargs)
            replacement = self.temp / "replacement.json"
            replacement.write_bytes(b'{"a":2}')
            replacement.replace(path)
            return handle

        path.write_bytes(compact)
        with patch("builtins.open", side_effect=open_once_then_replace):
            value, digest = read_json_with_digest(path)
        self.assertEqual(len(opens), 1)
        self.assertEqual((value, digest), ({"a": 1}, compact_sha))
        self.assertEqual(read_json(path), {"a": 2})

        # Strict rejection paths also apply to read_json_with_digest.
        for limit, msg in ((-1, "invalid JSON read limit"), (3, "JSON read limit exceeded")):
            with self.subTest(limit=limit), self.assertRaisesRegex(ValueError, msg):
                read_json_with_digest(path, max_bytes=limit)
        for bad in (b"", b"{", b'"\xff"', b'{"k":1,"k":2}', b"NaN", b"Infinity",
                    b"-Infinity", b'{"x":1e400}', b'{"x":-1e400}', b'"\\ud800"',
                    b'{"\\ud800":1}'):
            path.write_bytes(bad)
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "invalid JSON data"):
                read_json_with_digest(path)

    def test_integer_token_digit_boundary(self):
        from t3compose.jsonio import read_json, read_json_with_digest
        path = self.temp / "int.json"
        expected_max = int("9" * 4300)

        for sign, factor in ((b"", 1), (b"-", -1)):
            raw_ok = b'{"n":' + sign + (b"9" * 4300) + b"}"
            path.write_bytes(raw_ok)
            parsed = read_json(path)
            self.assertEqual(parsed["n"], factor * expected_max)
            parsed_with_digest, digest = read_json_with_digest(path)
            self.assertEqual(parsed_with_digest["n"], factor * expected_max)
            self.assertTrue(digest.startswith("sha256:"))

            raw_too_long = b'{"n":' + sign + (b"1" * 4301) + b"}"
            path.write_bytes(raw_too_long)
            for fn in (read_json, read_json_with_digest):
                with self.subTest(sign=sign, fn=fn.__name__), \
                        self.assertRaisesRegex(ValueError, "invalid JSON data"):
                    fn(path)


class OfflineAtomicJSON(OfflineTestCase):
    def test_publication_order_private_mode_and_collision(self):
        import fcntl
        import os
        import stat
        from t3compose import jsonio
        self.assertTrue(hasattr(jsonio, "atomic_create"), "exclusive JSON API missing")
        path = self.temp / "record.json"
        for method in (jsonio.atomic_create, jsonio.atomic_replace):
            path.unlink(missing_ok=True)
            order, real_sync, real_link, real_replace = [], os.fsync, os.link, os.replace
            real_lock = fcntl.flock
            def lock(fd, flags):
                if isinstance(fd, int) and flags == fcntl.LOCK_EX:
                    self.assertEqual(stat.S_IMODE(os.fstat(fd).st_mode), 0o600)
                return real_lock(fd, flags)
            def sync(fd):
                order.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
                return real_sync(fd)
            def publish(src, dst, **kw):
                self.assertEqual(order, ["file"])
                temp = self.temp / src
                self.assertEqual(stat.S_IMODE(temp.stat().st_mode), 0o600)
                self.assertRegex(temp.name, r"^\.t3c-tmp-[1-9][0-9]*-[0-9a-f]{32}\.tmp$")
                with temp.open("rb") as stream:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                order.append("publish")
                return (real_link if method == jsonio.atomic_create else real_replace)(src, dst, **kw)
            previous = os.umask(0o277)
            try:
                with patch.object(os, "fsync", sync), patch.object(os, "link", publish), \
                        patch.object(os, "replace", publish), patch.object(fcntl, "flock", lock):
                    method(path, {"z": "é", "a": 1})
            finally:
                self.assertEqual(os.umask(previous), 0o277)
            self.assertEqual(order, ["file", "publish", "directory", "directory"])
            self.assertEqual(path.read_bytes(), b'{"a":1,"z":"\xc3\xa9"}')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(list(self.temp.glob(".t3c-tmp-*")), [])
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            jsonio.atomic_create(path, {"loser": True})
        self.assertEqual(path.read_bytes(), before)
        jsonio.atomic_replace(path, {"new": True})
        self.assertEqual(path.read_bytes(), b'{"new":true}')

    def test_each_failed_atomic_step_never_exposes_partial_json(self):
        import os
        from t3compose import jsonio
        self.assertTrue(hasattr(jsonio, "atomic_replace"), "atomic JSON API missing")
        for exclusive in (False, True):
            for step in ("open", "fchmod", "write", "fsync-file", "publish", "fsync-dir", "unlink", "fsync-cleanup"):
                with self.subTest(exclusive=exclusive, step=step):
                    folder = self.temp / (str(exclusive) + step)
                    folder.mkdir()
                    target = folder / "value.json"
                    if not exclusive:
                        target.write_bytes(b'{"old":true}')
                    symbol = {"fsync-file": "fsync", "fsync-dir": "fsync", "fsync-cleanup": "fsync",
                              "publish": "link" if exclusive else "replace"}.get(step, step)
                    original, calls = getattr(os, symbol), 0
                    def fail(*args, **kwargs):
                        nonlocal calls
                        calls += 1
                        which = {"fsync-dir": 2, "fsync-cleanup": 3}.get(step, 1)
                        # Parent directory opens precede the temporary-file open.
                        if (step == "open" and str(args[0]).startswith(".t3c-tmp-")) or (step != "open" and calls == which):
                            raise OSError("synthetic injected failure")
                        return original(*args, **kwargs)
                    previous = os.umask(0o277)
                    try:
                        with patch.object(os, symbol, fail), self.assertRaises(OSError):
                            (jsonio.atomic_create if exclusive else jsonio.atomic_replace)(target, {"new": True})
                    finally:
                        self.assertEqual(os.umask(previous), 0o277)
                    if target.exists():
                        self.assertIn(target.read_bytes(), (b'{"old":true}', b'{"new":true}'))
                    elif not exclusive:
                        self.fail("replacement lost the old file")
                    for temp in folder.glob(".t3c-tmp-*"):
                        self.assertEqual(temp.stat().st_mode & 0o777, 0o600)

    def test_partial_writes_and_descriptor_basename_validation(self):
        import os
        from t3compose import jsonio
        self.assertTrue(hasattr(jsonio, "atomic_create"), "exclusive JSON API missing")
        original = os.write
        with patch.object(os, "write", lambda fd, data: original(fd, data[:2])):
            jsonio.atomic_create(self.temp / "short.json", {"complete": True})
        self.assertEqual((self.temp / "short.json").read_bytes(), b'{"complete":true}')
        with patch.object(os, "write", return_value=0), self.assertRaises(OSError):
            jsonio.atomic_create(self.temp / "zero.json", {})
        self.assertFalse((self.temp / "zero.json").exists())
        with patch.object(jsonio.fcntl, "flock", side_effect=OSError("synthetic lock failure")), self.assertRaises(OSError):
            jsonio.atomic_create(self.temp / "unlocked.json", {})
        self.assertFalse((self.temp / "unlocked.json").exists())
        self.assertEqual(list(self.temp.glob(".t3c-tmp-*")), [])
        fd = os.open(self.temp, os.O_RDONLY)
        try:
            for name in ("../outside.json", "/outside.json", "a/b.json", ".", ".."):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    jsonio.atomic_create(name, {}, dir_fd=fd)
        finally:
            os.close(fd)
