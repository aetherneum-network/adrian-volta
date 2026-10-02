"""Determinism: same seed, same bytes - in memory, on disk, and in two different folders."""
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from corpus import generate as G
from lab import fsx, jsonio
from tests import _util as U

rebuild = U.load_module("tools/rebuild.py")
SMALL = 24


class Corpus(U.TempCase):
    def test_the_committed_manifest_and_gold_are_what_the_generator_produces_today(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = G.main(["--check"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("240 instances regenerated - identical to MANIFEST.sha256 and gold", buf.getvalue())

    def test_the_manifest_has_one_line_per_instance_and_a_total(self):
        lines = fsx.read_bytes(os.path.join(U.ROOT, "corpus", "MANIFEST.sha256")).decode("utf-8").splitlines()
        self.assertEqual(len(lines), 241)
        self.assertTrue(lines[-1].endswith("  dev/TOTAL"))
        gold = jsonio.read_jsonl(os.path.join(U.ROOT, "corpus", "gold", "dev.labels.jsonl"))
        self.assertEqual([f"{g['sha256']}  dev/{g['instance']}" for g in gold], lines[:-1])

    def test_the_dev_suite_is_balanced_and_has_clean_instances(self):
        gold = jsonio.read_jsonl(os.path.join(U.ROOT, "corpus", "gold", "dev.labels.jsonl"))
        self.assertEqual([len(gold), sum(g["clean"] for g in gold)], [240, 72])
        per_class = {}
        for g in gold:
            self.assertLessEqual(len(g["faults"]), 1)
            self.assertEqual(g["as_of"][-1], "Z")
            for fault in g["faults"]:
                per_class[fault["class"]] = per_class.get(fault["class"], 0) + 1
        self.assertEqual(per_class, {cls: 14 for cls in G.CLASSES})
        self.assertTrue(all(g["decoys"] for g in gold if g["clean"]) or any(g["decoys"] for g in gold))

    def test_the_same_seed_gives_the_same_bytes_and_another_seed_does_not(self):
        one = G.generate(G.SUITES["dev"]["seed"], "dev", SMALL, out=self.path("one"))
        two = G.generate(G.SUITES["dev"]["seed"], "dev", SMALL, out=self.path("other place", "two"))
        self.assertEqual(G.gold_bytes(one), G.gold_bytes(two))
        first = {rel: jsonio.sha256_file(fsx.join(self.path("one"), rel)) for rel in fsx.walk_files(self.path("one"))}
        second = {rel: jsonio.sha256_file(fsx.join(self.path("other place", "two"), rel))
                  for rel in fsx.walk_files(self.path("other place", "two"))}
        self.assertEqual(first, second)
        self.assertGreater(len(first), SMALL * 5)
        other = G.generate(424242, "dev", SMALL)          # an arbitrary seed that belongs to no suite and is not a blind seed
        self.assertNotEqual(G.gold_bytes(other), G.gold_bytes(one))

    def test_the_digest_in_the_gold_is_the_digest_of_the_files_on_disk(self):
        golds = G.generate(G.SUITES["dev"]["seed"], "dev", 12, out=self.path("c"))
        for gold in golds:
            root = self.path("c", gold["instance"])
            rels = fsx.walk_files(root)
            self.assertEqual(len(rels), gold["files"])
            self.assertEqual(jsonio.tree_digest((rel, jsonio.sha256_file(fsx.join(root, rel))) for rel in rels), gold["sha256"])

    def test_generated_text_files_are_utf8_with_lf(self):
        G.generate(G.SUITES["dev"]["seed"], "dev", 12, out=self.path("c"))
        checked = 0
        for rel in fsx.walk_files(self.path("c")):
            if rel.endswith((".yaml", ".json", ".jsonl")):
                data = fsx.read_bytes(fsx.join(self.path("c"), rel))
                data.decode("utf-8")
                self.assertNotIn(b"\r", data, rel)
                checked += 1
        self.assertGreater(checked, 50)

    def test_named_suite_seeds_are_refused_for_ad_hoc_generation(self):
        for name in ("dev", "holdout", "stress"):
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
                G.main(["--seed", str(G.SUITES[name]["seed"])])

    def test_unknown_styles_are_refused(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            G.main(["--seed", "424242", "--styles", "flow,handwriting"])


class Rebuild(U.TempCase):
    def test_two_rebuilds_in_different_folders_are_byte_identical(self):
        first = rebuild.rebuild(self.path("one"), SMALL, quiet=True)
        second = rebuild.rebuild(self.path("somewhere else", "più giù", "two"), SMALL, quiet=True)
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual([first["files"], first["instances"], first["scenarios_passed"]], [second["files"], SMALL, 10])
        one, two = self.path("one"), self.path("somewhere else", "più giù", "two")
        self.assertEqual(fsx.walk_files(one), fsx.walk_files(two))
        self.assertEqual(fsx.read_bytes(os.path.join(one, "REBUILD.sha256")), fsx.read_bytes(os.path.join(two, "REBUILD.sha256")))
        self.assertEqual(rebuild.digest(one), (first["sha256"], first["files"]))
        # nothing that was written knows where it was written
        needles = [self.tmp.encode("utf-8"), self.tmp.replace(chr(92), "/").encode("utf-8"),
                   self.tmp.replace(chr(92), chr(92) * 2).encode("utf-8"), b"somewhere else"]
        for rel in fsx.walk_files(two):
            if rel.endswith((".json", ".jsonl", ".md", ".sha256")):
                data = fsx.read_bytes(fsx.join(two, rel))
                self.assertEqual([n for n in needles if n in data], [], rel)
                self.assertNotIn(b"\r\n", data, rel)

    def test_the_digest_changes_when_one_byte_changes(self):
        result = rebuild.rebuild(self.path("one"), 12, quiet=True)
        path = os.path.join(self.path("one"), "scenarios.json")
        fsx.write_bytes(path, fsx.read_bytes(path) + b" ")
        self.assertNotEqual(rebuild.digest(self.path("one"))[0], result["sha256"])

    def test_the_tool_refuses_to_rebuild_inside_the_repository_sources(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            rebuild.main(["--out", os.path.join(U.ROOT, "lab")])


if __name__ == "__main__":
    unittest.main()
