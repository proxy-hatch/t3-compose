from tests.support import OfflineTestCase


class OfflineJSON(OfflineTestCase):
    def test_canonical_json_and_digest(self):
        from t3compose.jsonio import canonical_json, file_digest
        expected = b'{"a":[1,true,null],"z":"\xc3\xa9"}'
        self.assertEqual(canonical_json({"z": "é", "a": [1, True, None]}), expected)
        self.assertEqual(canonical_json({"a": [1, True, None], "z": "é"}), expected)
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
        for data in (b"{", b'"\xff"', b'{"a":1,"a":2}', b"NaN", b"Infinity"):
            path.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_json(path)
        with self.assertRaises(OSError):
            read_json(self.temp / "absent")
