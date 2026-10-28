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
