"""The three references, and the scoring that uses them as judges.

The references share no code with the lab (proved in ``test_offline``); here they are checked
against the fault plans of a development subset, and against damage done by hand.
"""
import os
import random
import unittest

from corpus import generate as G
from corpus import reference_hashes, reference_reach, reference_routes
from lab import backup, fsx, jsonio
from tests import _util as U

score = U.load_module("eval/score.py")
SUBSET = 60


class OnTheDevelopmentSubset(U.TempCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.root = tempfile.mkdtemp(prefix="av2-ref-")
        cls.addClassCleanup(fsx.rmtree, cls.root)
        cls.corpus = os.path.join(cls.root, "corpus")
        cls.golds = G.generate(G.SUITES["dev"]["seed"], "dev", SUBSET, out=cls.corpus)
        cls.metrics = score.score(cls.golds, cls.corpus, os.path.join(cls.root, "work"), os.path.join(cls.root, "details.jsonl"))

    def test_the_subset_covers_every_class_and_clean_instances(self):
        planted = {f["class"] for g in self.golds for f in g["faults"]}
        self.assertEqual(planted, set(G.CLASSES))
        self.assertGreater(sum(g["clean"] for g in self.golds), 10)

    def test_references_agree_with_the_fault_plan_on_every_instance(self):
        for gold in self.golds:
            with self.subTest(instance=gold["instance"]):
                view = score.reference_view(os.path.join(self.corpus, gold["instance"]))
                self.assertEqual(score.gold_agrees_with_references(gold, view), [])

    def test_a_clean_instance_is_clean_for_every_reference(self):
        for gold in (g for g in self.golds if g["clean"]):
            view = score.reference_view(os.path.join(self.corpus, gold["instance"]))
            self.assertEqual([view["findings"], view["edges"], view["a_differing"], view["b_differing"], bool(view["secondary_stale"])],
                             [[], [], [], [], False], gold["instance"])

    def test_the_score_of_the_subset(self):
        m = self.metrics
        self.assertEqual([m["instances"], m["never_event_restore"], m["never_event_admin_public"], m["gold_reference_disagreements"]],
                         [SUBSET, 0, 0, 0])
        self.assertEqual([m["detected"], m["localised"]], [m["planted_faults"], m["planted_faults"]])
        self.assertEqual([m["false_alarms_on_clean"], m["spurious_findings"], m["verdict_correct"]], [0, 0, SUBSET])
        self.assertEqual(m["restore_lists_exact"], m["restore_lists_planted"])
        self.assertGreater(m["restore_lists_planted"], 5)

    def test_details_say_nothing_is_wrong_and_carry_no_path(self):
        rows = jsonio.read_jsonl(os.path.join(self.root, "details.jsonl"))
        self.assertEqual(len(rows), SUBSET)
        self.assertEqual([r["instance"] for r in rows if r["notes"]], [])
        self.assertNotIn(self.root.encode("utf-8"), fsx.read_bytes(os.path.join(self.root, "details.jsonl")))

    def test_the_scorer_counts_a_never_event_when_the_lab_lies(self):
        """The judge is the reference: a lab report that says ok on a damaged repository is counted."""
        gold = next(g for g in self.golds if any(f["class"] == "block_corrupt" for f in g["faults"]))
        honest = score.lab_run.audit
        def lying_audit(inst, work, keep_restore=False):
            report = honest(inst, work, keep_restore)
            if report.get("drill"):
                report["drill"]["result"] = "ok"
            return report
        score.lab_run.audit = lying_audit
        try:
            m = score.score([gold], self.corpus, os.path.join(self.root, "work-lie"), None)
        finally:
            score.lab_run.audit = honest
        self.assertEqual(m["never_event_restore"], 1)

    def test_the_scorer_counts_an_admin_public_never_event_when_the_lab_accepts_the_table(self):
        gold = next(g for g in self.golds if any(f["class"] == "admin_on_public" for f in g["faults"]))
        honest = score.lab_run.audit
        def lying_audit(inst, work, keep_restore=False):
            report = honest(inst, work, keep_restore)
            report["routes"]["accepted"] = True
            return report
        score.lab_run.audit = lying_audit
        try:
            m = score.score([gold], self.corpus, os.path.join(self.root, "work-lie2"), None)
        finally:
            score.lab_run.audit = honest
        self.assertEqual(m["never_event_admin_public"], 1)

    def test_a_missed_fault_and_a_spurious_finding_are_counted(self):
        gold = next(g for g in self.golds if any(f["class"] == "restart_loop" for f in g["faults"]))
        clean = next(g for g in self.golds if g["clean"])
        swapped = [dict(gold, instance=clean["instance"]), dict(clean, instance=gold["instance"])]   # labels on the wrong instances
        m = score.score(swapped, self.corpus, os.path.join(self.root, "work-swap"), None)
        self.assertEqual([m["detected"], m["localised"], m["spurious_findings"], m["false_alarms_on_clean"], m["verdict_correct"]],
                         [0, 0, 1, 1, 0])
        self.assertEqual(m["gold_reference_disagreements"], 2)


class MatchingRule(unittest.TestCase):
    FAULT = {"class": "route_duplicate_shadow", "file": "routes/shop.yaml", "also": ["routes/old/shop.yaml"], "items": ["shop-main", "shop-copy"]}

    def finding(self, **kw):
        return dict({"class": "route_duplicate_shadow", "file": "routes/old/shop.yaml", "also": ["routes/shop.yaml"], "item": "shop-copy"}, **kw)

    def test_class_files_and_item_must_all_match(self):
        self.assertTrue(score.matches(self.finding(), self.FAULT))                       # file order does not matter
        self.assertFalse(score.matches(self.finding(**{"class": "route_missing_service"}), self.FAULT))
        self.assertFalse(score.matches(self.finding(also=[]), self.FAULT))                # only one of the two files named
        self.assertFalse(score.matches(self.finding(item="other"), self.FAULT))
        self.assertTrue(score.matches(self.finding(item="anything"), dict(self.FAULT, items=None)))


class ByHand(U.TempCase):
    def test_hash_reference_names_the_files_that_differ_and_only_those(self):
        U.backup_instance(self.path("inst"), "ref")
        self.assertEqual(reference_hashes.check(self.path("inst"))["repos"]["a"]["differing"], [])
        victim = "dati/02/" + U.ACCENTED[2]
        fsx.append_bytes(fsx.join(self.path("inst", "source"), victim), b"x")
        check = reference_hashes.check(self.path("inst"))
        self.assertEqual([check["repos"]["a"]["differing"], check["repos"]["b"]["differing"]], [[victim], [victim]])

    def test_hash_reference_sees_a_flipped_byte_without_trusting_the_block_hashes(self):
        U.backup_instance(self.path("inst"), "ref2")
        repo = backup.Repo(self.path("inst", "repo_a"))
        snap = repo.read_snapshot(repo.snapshot_ids()[-1])
        entry = random.Random("ref2").choice([e for e in snap["files"] if e["blocks"]])
        offset, _ = repo.load_index()["blocks"][entry["blocks"][0]]
        pack = bytearray(fsx.read_bytes(repo.pack_path))
        pack[offset] ^= 1
        fsx.write_bytes(repo.pack_path, bytes(pack))
        check = reference_hashes.check(self.path("inst"))
        self.assertIn(entry["path"], check["repos"]["a"]["differing"])
        self.assertEqual(check["repos"]["b"]["differing"], [])

    def test_route_reference_finds_a_duplicate_an_admin_route_and_a_ghost(self):
        routes = {"routes/shop.yaml": U.route_doc("shop", 8080),
                  "routes/old/shop.yaml": U.route_doc("shop", 8080, name="shop-copy"),
                  "routes/console.yaml": U.route_doc("console", 9000),
                  "routes/ghost.yaml": U.route_doc("shop-v2", 8080, host="new.valdora.example")}
        root = U.routing_instance(self.path("inst"), U.topology_doc(), routes)
        found = sorted((r["class"], tuple(sorted(r["files"]))) for r in reference_routes.check(root))
        self.assertEqual(found, [("admin_on_public", ("routes/console.yaml",)),
                                 ("route_duplicate_shadow", ("routes/old/shop.yaml", "routes/shop.yaml")),
                                 ("route_missing_service", ("routes/ghost.yaml",))])
        self.assertTrue(reference_reach.matrix(root)["admin_public_edges"])

    def test_reach_reference_replays_a_silent_failure(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:41:30Z", "service": "shop", "map": {"/healthz": 200}}]
        root = U.routing_instance(self.path("inst"), U.topology_doc(health={"shop": {"kind": "process"}}),
                                  {"routes/shop.yaml": U.route_doc("shop", 8080)}, scope=("routing", "health"),
                                  as_of="2026-09-14T07:00:00Z", trace=events)
        self.assertEqual(reference_reach.check(root), [{"class": "health_probes_process", "files": ["topology.yaml"], "item": "shop"}])
        self.assertEqual(reference_reach.matrix(root)["admin_public_edges"], [])


if __name__ == "__main__":
    unittest.main()
