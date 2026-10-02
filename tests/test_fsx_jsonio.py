"""File-system and JSON helpers: long paths, accented names, LF, canonical form, confirmed writes."""
import hashlib
import os
import unittest
from unittest import mock

from lab import fsx, jsonio, timeutil
from tests import _util as U


class LongPaths(U.TempCase):
    REL = U.LONG_DIR + "/" + U.ACCENTED[0]

    def test_a_path_longer_than_260_characters_is_written_read_listed_copied_moved_and_removed(self):
        root = self.path("tree")
        full = fsx.join(root, self.REL)
        self.assertGreater(len(self.REL), 260)
        self.assertGreater(len(os.path.abspath(full)), 300)
        fsx.write_bytes(full, b"contenuto")
        self.assertTrue(fsx.isfile(full))
        self.assertEqual(fsx.read_bytes(full), b"contenuto")
        self.assertEqual(fsx.size(full), 9)
        self.assertEqual(fsx.walk_files(root), [self.REL])
        self.assertEqual(jsonio.sha256_file(full), hashlib.sha256(b"contenuto").hexdigest())
        fsx.copytree(root, self.path("copy"))
        self.assertEqual(fsx.read_bytes(fsx.join(self.path("copy"), self.REL)), b"contenuto")
        fsx.move(fsx.join(self.path("copy"), self.REL), fsx.join(self.path("moved"), self.REL + ".spostato"))
        self.assertEqual(fsx.walk_files(self.path("moved")), [self.REL + ".spostato"])
        self.assertEqual(fsx.walk_files(self.path("copy")), [])
        fsx.append_bytes(full, b" e altro")
        self.assertEqual(fsx.read_bytes(full), b"contenuto e altro")
        fsx.rmtree(root)
        self.assertFalse(fsx.exists(root))

    def test_ext_is_absolute_and_idempotent(self):
        once = fsx.ext(self.path("a", "b"))
        self.assertEqual(fsx.ext(once), once)
        self.assertTrue(os.path.isabs(once))


class Listing(U.TempCase):
    def test_relative_paths_are_posix_and_sorted_as_strings(self):
        for rel in ("b/z.txt", "a.txt", "b/a.txt", "B/a.txt" if os.name != "nt" else "c/A.txt", "à/è.txt", "b/c/d/e.txt"):
            fsx.write_bytes(fsx.join(self.tmp, rel), b"x")
        listed = fsx.walk_files(self.tmp)
        self.assertEqual(listed, sorted(listed))
        self.assertEqual(len(listed), 6)
        self.assertTrue(all(chr(92) not in rel and not rel.startswith("/") for rel in listed))
        self.assertIn("à/è.txt", listed)

    def test_a_missing_folder_is_an_empty_listing(self):
        self.assertEqual(fsx.walk_files(self.path("nothing")), [])
        fsx.rmtree(self.path("nothing"))          # removing what is not there is not an error

    def test_join_with_an_empty_relative_path_is_the_root(self):
        self.assertEqual(fsx.join(self.tmp, ""), self.tmp)
        self.assertEqual(fsx.join(self.tmp, "a/b"), os.path.join(self.tmp, "a", "b"))


class ConfirmedWrites(U.TempCase):
    def test_a_write_whose_size_cannot_be_confirmed_is_an_error(self):
        with mock.patch("os.path.getsize", return_value=3):
            with self.assertRaises(OSError):
                fsx.write_bytes(self.path("f.txt"), b"four")
            with self.assertRaises(OSError):
                fsx.append_bytes(self.path("g.txt"), b"four!")

    def test_write_creates_the_parent_folders(self):
        fsx.write_bytes(self.path("a", "b", "c", "f.txt"), b"")
        self.assertEqual(fsx.size(self.path("a", "b", "c", "f.txt")), 0)


class Json(U.TempCase):
    def test_canonical_form(self):
        doc = {"b": 1, "a": {"z": "città", "y": [1, 2]}, "c": None}
        text = jsonio.dumps(doc)
        self.assertEqual(text, '{\n  "a": {\n    "y": [\n      1,\n      2\n    ],\n    "z": "città"\n  },\n  "b": 1,\n  "c": null\n}\n')
        self.assertEqual(jsonio.line(doc), '{"a":{"y":[1,2],"z":"città"},"b":1,"c":null}')

    def test_files_are_written_with_lf_whatever_the_system(self):
        jsonio.write(self.path("a.json"), {"k": ["v", "w"]})
        jsonio.write_jsonl(self.path("a.jsonl"), [{"n": 1}, {"n": 2}])
        jsonio.write_text(self.path("a.md"), "one\r\ntwo\n")
        for name in ("a.json", "a.jsonl", "a.md"):
            self.assertNotIn(b"\r", fsx.read_bytes(self.path(name)), name)
        self.assertEqual(fsx.read_bytes(self.path("a.md")), b"one\ntwo\n")
        self.assertEqual(jsonio.read_jsonl(self.path("a.jsonl")), [{"n": 1}, {"n": 2}])
        self.assertEqual(jsonio.read(self.path("a.json")), {"k": ["v", "w"]})

    def test_tree_digest_does_not_depend_on_the_order_of_the_entries(self):
        entries = [("b", "2" * 64), ("a", "1" * 64), ("c/d", "3" * 64)]
        self.assertEqual(jsonio.tree_digest(entries), jsonio.tree_digest(reversed(entries)))
        self.assertNotEqual(jsonio.tree_digest(entries), jsonio.tree_digest(entries[:2]))
        body = "".join(f"{digest}  {rel}\n" for rel, digest in sorted(entries)).encode("utf-8")
        self.assertEqual(jsonio.tree_digest(entries), hashlib.sha256(body).hexdigest())

    def test_sha256_of_a_file_is_the_sha256_of_its_bytes(self):
        data = bytes(range(256)) * 700        # larger than one read chunk
        fsx.write_bytes(self.path("big.bin"), data)
        self.assertEqual(jsonio.sha256_file(self.path("big.bin")), hashlib.sha256(data).hexdigest())
        self.assertEqual(jsonio.sha256_bytes(data), hashlib.sha256(data).hexdigest())


class Time(unittest.TestCase):
    def test_classification_names_what_a_timestamp_is(self):
        cases = {"2026-09-14T06:00:00Z": "utc", "2026-09-14T08:00:00+02:00": "offset", "2026-09-14T01:00:00-05:00": "offset",
                 "2026-09-14T06:00:00": "naive", "2026-09-14 06:00:00": "naive", "2026-09-14": "invalid", "2026-13-40T06:00:00Z": "invalid",
                 "2026-09-14T06:00:00z": "invalid", "2026-09-14T06:00:00.5Z": "invalid", "": "invalid", 20260914: "invalid", None: "invalid"}
        self.assertEqual({value: timeutil.classify(value)[0] for value in cases}, cases)

    def test_an_offset_is_converted_and_a_naive_value_is_never_given_an_instant(self):
        self.assertEqual(timeutil.fmt(timeutil.classify("2026-09-14T08:00:00+02:00")[1]), "2026-09-14T06:00:00Z")
        self.assertEqual(timeutil.fmt(timeutil.classify("2026-09-14T01:00:00-05:00")[1]), "2026-09-14T06:00:00Z")
        self.assertIsNone(timeutil.classify("2026-09-14T06:00:00")[1])
        with self.assertRaises(ValueError):
            timeutil.parse_utc("2026-09-14T08:00:00+02:00")

    def test_schedule_time(self):
        self.assertEqual(timeutil.parse_at("02:30"), (2, 30))
        self.assertEqual(timeutil.parse_at("23:59"), (23, 59))
        for value in ("24:00", "2:30", "02:60", "02:30:00", 750, None, ""):
            self.assertIsNone(timeutil.parse_at(value), value)

    def test_snapshot_ids_sort_like_time(self):
        a = timeutil.snapshot_id(timeutil.parse_utc("2026-09-09T23:59:59Z"))
        b = timeutil.snapshot_id(timeutil.parse_utc("2026-09-10T00:00:00Z"))
        self.assertEqual([a, b], ["20260909T235959Z", "20260910T000000Z"])
        self.assertLess(a, b)


if __name__ == "__main__":
    unittest.main()
