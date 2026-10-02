"""Property: backup -> restore -> verify gives back the source, byte for byte, for 200 seeded trees.

Sizes straddle the block size (0, 1, 1023, 1024, 1025, 2048, arbitrary), names are accented,
and one tree in ten has a path longer than 260 characters.
"""
import os
import random
import unittest

from lab import backup, drill, fsx, jsonio, restore, timeutil
from tests import _util as U

T0 = timeutil.parse_utc("2026-09-10T02:30:35Z")
T1 = timeutil.parse_utc("2026-09-11T02:30:35Z")
T2 = timeutil.parse_utc("2026-09-11T06:00:00Z")


class RoundTrip(U.TempCase):
    TREES = 200

    def test_two_hundred_seeded_trees_come_back_identical(self):
        longest = 0
        for n in range(self.TREES):
            rng = random.Random(f"av2:roundtrip:{n}")
            base = self.path(f"t{n:03d}")
            source, repo_dir = os.path.join(base, "source"), os.path.join(base, "repo")
            files = U.random_tree(source, rng, long_paths=(n % 10 == 0))
            longest = max([longest] + [len(rel) for rel in files])
            snap = backup.backup(source, repo_dir, T0)
            with self.subTest(tree=n):
                self.assertEqual(snap["source_file_count"], len(files))
                out = restore.restore(backup.Repo(repo_dir), snap["id"], os.path.join(base, "out"))
                self.assertEqual(out["problems"], [])
                self.assertEqual(out["written"], sorted(files))
                for rel, data in files.items():
                    self.assertEqual(fsx.read_bytes(fsx.join(os.path.join(base, "out"), rel)), data)
                result = drill.run(source, repo_dir, "a", os.path.join(base, "scratch"), T2)
                self.assertTrue(result.ok)
                self.assertEqual([result.source_count, result.declared_count, result.restored_count], [len(files)] * 3)
            fsx.rmtree(base)
        self.assertGreater(longest, 260)

    def test_an_older_snapshot_still_restores_the_older_tree_after_a_newer_backup(self):
        for n in range(25):
            rng = random.Random(f"av2:roundtrip:two-nights:{n}")
            base = self.path(f"n{n:02d}")
            source, repo_dir = os.path.join(base, "source"), os.path.join(base, "repo")
            first = U.random_tree(source, rng)
            old = backup.backup(source, repo_dir, T0)
            changed = sorted(first)[0]
            fsx.write_bytes(fsx.join(source, changed), rng.randbytes(1500))
            fsx.write_bytes(fsx.join(source, "nuovo/aggiunto-dopo.txt"), rng.randbytes(10))
            new = backup.backup(source, repo_dir, T1)
            with self.subTest(tree=n):
                self.assertNotEqual(old["id"], new["id"])
                out = restore.restore(backup.Repo(repo_dir), old["id"], os.path.join(base, "old"))
                self.assertEqual(out["problems"], [])
                self.assertEqual({rel: fsx.read_bytes(fsx.join(os.path.join(base, "old"), rel)) for rel in out["written"]}, first)
                self.assertTrue(drill.run(source, repo_dir, "a", os.path.join(base, "scratch"), T2).ok)       # newest = current tree
                stale = drill.run(source, repo_dir, "a", os.path.join(base, "scratch"), T2, snapshot=old["id"])
                self.assertFalse(stale.ok)                         # the old snapshot is not the current source, and says so
                self.assertEqual(stale.mismatched_files, sorted([changed, "nuovo/aggiunto-dopo.txt"]))
            fsx.rmtree(base)

    def test_identical_blocks_are_stored_once_and_the_pack_only_grows(self):
        source, repo_dir = self.path("source"), self.path("repo")
        block = bytes(range(256)) * 4
        fsx.write_bytes(os.path.join(source, "a.bin.txt"), block * 3)
        fsx.write_bytes(os.path.join(source, "b.bin.txt"), block * 2)
        backup.backup(source, repo_dir, T0)
        repo = backup.Repo(repo_dir)
        self.assertEqual(len(repo.load_index()["blocks"]), 1)
        self.assertEqual(fsx.size(repo.pack_path), 1024)
        before = fsx.read_bytes(repo.pack_path)
        fsx.write_bytes(os.path.join(source, "c.txt"), b"new content")
        backup.backup(source, repo_dir, T1)
        self.assertTrue(fsx.read_bytes(repo.pack_path).startswith(before))          # append only
        self.assertTrue(drill.run(source, repo_dir, "a", self.path("scratch"), T2).ok)

    def test_the_source_manifest_is_a_function_of_the_bytes_only(self):
        rng = random.Random("av2:roundtrip:manifest")
        files = U.random_tree(self.path("one"), rng, long_paths=True)
        for rel, data in reversed(list(files.items())):            # same content, written in another order, elsewhere
            fsx.write_bytes(fsx.join(self.path("two", "deeper"), rel), data)
        one, two = restore.source_manifest(self.path("one")), restore.source_manifest(self.path("two", "deeper"))
        self.assertEqual(one, two)
        self.assertEqual(sorted(one["files"]), sorted(files))
        self.assertEqual({rel: entry["sha256"] for rel, entry in one["files"].items()},
                         {rel: jsonio.sha256_bytes(data) for rel, data in files.items()})


if __name__ == "__main__":
    unittest.main()
