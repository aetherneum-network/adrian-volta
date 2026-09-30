"""Content-addressed block store, policy reading, chain order and retention."""
import os
import unittest

from lab import backup, drill, fsx, jsonio, timeutil
from tests import _util as U

T1 = timeutil.parse_utc("2026-09-11T02:31:00Z")
T2 = timeutil.parse_utc("2026-09-12T02:31:00Z")


class Store(U.TempCase):
    def setUp(self):
        super().setUp()
        self.source, self.repo = self.path("source"), self.path("repo")
        U.SC.write_tree(self.source, U.tree_spec("store"))

    def test_snapshot_lists_every_file_with_size_hash_and_blocks(self):
        snap = backup.backup(self.source, self.repo, T1)
        self.assertEqual(snap["id"], "20260911T023100Z")
        self.assertEqual(snap["source_file_count"], len(fsx.walk_files(self.source)))
        self.assertEqual([f["path"] for f in snap["files"]], fsx.walk_files(self.source))
        for entry in snap["files"]:
            self.assertEqual(entry["sha256"], jsonio.sha256_file(fsx.join(self.source, entry["path"])))
            self.assertEqual(len(entry["blocks"]), -(-entry["size"] // 1024))

    def test_blocks_are_addressed_by_their_content(self):
        backup.backup(self.source, self.repo, T1)
        repo = backup.Repo(self.repo)
        pack = fsx.read_bytes(repo.pack_path)
        index = repo.load_index()
        self.assertTrue(index["blocks"])
        for sha, (offset, length) in index["blocks"].items():
            self.assertEqual(jsonio.sha256_bytes(pack[offset:offset + length]), sha)

    def test_an_unchanged_tree_adds_no_bytes_to_the_pack(self):
        backup.backup(self.source, self.repo, T1)
        size = fsx.size(backup.Repo(self.repo).pack_path)
        backup.backup(self.source, self.repo, T2)
        self.assertEqual(fsx.size(backup.Repo(self.repo).pack_path), size)
        self.assertEqual(backup.Repo(self.repo).snapshot_ids(), ["20260911T023100Z", "20260912T023100Z"])

    def test_identical_files_share_their_blocks(self):
        data = U.SC.stream("twin", 3000)
        fsx.write_bytes(os.path.join(self.source, "twin-a.txt"), data)
        fsx.write_bytes(os.path.join(self.source, "twin-b.txt"), data)
        snap = backup.backup(self.source, self.repo, T1)
        by_path = {f["path"]: f["blocks"] for f in snap["files"]}
        self.assertEqual(by_path["twin-a.txt"], by_path["twin-b.txt"])

    def test_a_snapshot_is_never_overwritten(self):
        backup.backup(self.source, self.repo, T1)
        before = fsx.read_bytes(backup.Repo(self.repo).snapshot_path("20260911T023100Z"))
        with self.assertRaises(backup.BackupError):
            backup.backup(self.source, self.repo, T1)
        self.assertEqual(fsx.read_bytes(backup.Repo(self.repo).snapshot_path("20260911T023100Z")), before)

    def test_an_empty_or_missing_source_is_not_backed_up(self):
        with self.assertRaises(backup.BackupError):
            backup.backup(self.path("nowhere"), self.repo, T1)
        fsx.makedirs(self.path("empty"))
        with self.assertRaises(backup.BackupError):
            backup.backup(self.path("empty"), self.repo, T1)
        self.assertFalse(backup.Repo(self.repo).exists())

    def test_refuses_to_append_to_a_repository_whose_pack_is_shorter_than_its_index(self):
        backup.backup(self.source, self.repo, T1)
        repo = backup.Repo(self.repo)
        fsx.write_bytes(repo.pack_path, fsx.read_bytes(repo.pack_path)[:-10])
        with self.assertRaises(backup.BackupError):
            backup.backup(self.source, self.repo, T2)

    def test_a_malformed_index_or_snapshot_is_a_repo_error(self):
        backup.backup(self.source, self.repo, T1)
        repo = backup.Repo(self.repo)
        good = fsx.read_bytes(repo.index_path)
        fsx.write_bytes(repo.index_path, b"{not json")
        with self.assertRaises(backup.RepoError):
            repo.load_index()
        fsx.write_bytes(repo.index_path, good)
        snap = jsonio.read(repo.snapshot_path("20260911T023100Z"))
        for label, change in {"naive created": {"created": "2026-09-11 02:31:00"}, "no count": {"source_file_count": None},
                              "other id": {"id": "20260101T000000Z"}, "files not a list": {"files": {}}}.items():
            with self.subTest(case=label):
                jsonio.write(repo.snapshot_path("20260911T023100Z"), dict(snap, **change))
                with self.assertRaises(backup.RepoError):
                    repo.read_snapshot("20260911T023100Z")

    def test_files_that_are_not_snapshot_manifests_are_not_listed(self):
        backup.backup(self.source, self.repo, T1)
        fsx.write_bytes(os.path.join(self.repo, "snapshots", "notes.json"), b"{}")
        fsx.write_bytes(os.path.join(self.repo, "snapshots", "old", "20250101T000000Z.json"), b"{}")
        self.assertEqual(backup.Repo(self.repo).snapshot_ids(), ["20260911T023100Z"])


class Policy(unittest.TestCase):
    def test_valid_policy(self):
        policy, problems = backup.parse_policy(U.policy_doc(max_lag="30"))
        self.assertEqual(problems, [])
        self.assertEqual(policy["repos"]["b"], {"path": "repo_b", "max_lag_hours": 30})
        self.assertEqual(policy["repos"]["a"], {"path": "repo_a", "max_lag_hours": None})

    def test_malformed_policies_are_refused_with_a_reason(self):
        cases = {
            "not a mapping": [1],
            "unknown key": dict(U.policy_doc(), compress=True),
            "no schedule tz": dict(U.policy_doc(), schedule={"at": "02:30"}),
            "unknown step": U.policy_doc(chain=["export", "backup_a", "upload"]),
            "repeated step": U.policy_doc(chain=["export", "backup_a", "backup_a"]),
            "no export": U.policy_doc(chain=["backup_a", "verify"]),
            "absolute source": dict(U.policy_doc(), source="/data"),
            "source escapes": dict(U.policy_doc(), source="../data"),
            "drive in repo path": dict(U.policy_doc(), repos={"a": {"path": "c:repo"}, "b": {"path": "repo_b"}}),
            "no secondary": dict(U.policy_doc(), repos={"a": {"path": "repo_a"}}),
            "negative lag": U.policy_doc(max_lag=-1),
            "no retention": {k: v for k, v in U.policy_doc().items() if k != "retention"},
        }
        for label, doc in cases.items():
            with self.subTest(case=label):
                policy, problems = backup.parse_policy(doc)
                self.assertIsNone(policy)
                self.assertTrue(problems)

    def test_safe_rel(self):
        for good in ("source", "a/b/c.txt", "città/è.txt"):
            self.assertTrue(backup.safe_rel(good), good)
        for bad in ("", "/etc", "a/../b", "./a", "a//b", "a" + chr(92) + "b", "c:x", None, 3):
            self.assertFalse(backup.safe_rel(bad), bad)


class ChainOrder(unittest.TestCase):
    def jobs(self, export, backup_a, run="2026-09-12"):
        def job(step, start, end):
            out = {"run": run, "step": step, "start": start, "end": end, "status": "ok"}
            for field in ("start", "end"):
                out[f"_{field}_kind"], out[f"_{field}"] = timeutil.classify(out[field])
            return out
        return [job("export", *export), job("backup_a", *backup_a)]

    def test_order_as_written_and_as_it_happened(self):
        policy, _ = backup.parse_policy(U.policy_doc())
        jobs = self.jobs(("2026-09-12T02:30:05Z", "2026-09-12T02:31:05Z"), ("2026-09-12T02:31:10Z", "2026-09-12T02:31:40Z"))
        self.assertEqual(backup.chain_facts(policy, jobs)["facts"],
                         {"policy_export_before_backup": True, "policy_verify_before_prune": True, "trace_export_before_backup": True})

    def test_backup_started_while_the_export_was_still_running(self):
        policy, _ = backup.parse_policy(U.policy_doc())
        jobs = self.jobs(("2026-09-12T02:30:05Z", "2026-09-12T02:31:05Z"), ("2026-09-12T02:31:00Z", "2026-09-12T02:31:40Z"))
        self.assertFalse(backup.chain_facts(policy, jobs)["facts"]["trace_export_before_backup"])

    def test_order_in_the_trace_is_unknown_when_a_timestamp_has_no_zone(self):
        policy, _ = backup.parse_policy(U.policy_doc())
        jobs = self.jobs(("2026-09-12T02:30:05Z", "2026-09-12 04:31:05"), ("2026-09-12T02:31:10Z", "2026-09-12T02:31:40Z"))
        result = backup.chain_facts(policy, jobs)
        self.assertNotIn("trace_export_before_backup", result["facts"])       # unknown is not "in order"
        self.assertIn("trace_order", result["evidence"])

    def test_only_the_latest_run_is_judged(self):
        policy, _ = backup.parse_policy(U.policy_doc())
        old = self.jobs(("2026-09-11T02:35:00Z", "2026-09-11T02:36:00Z"), ("2026-09-11T02:30:00Z", "2026-09-11T02:31:00Z"), run="2026-09-11")
        new = self.jobs(("2026-09-12T02:30:05Z", "2026-09-12T02:31:05Z"), ("2026-09-12T02:31:10Z", "2026-09-12T02:31:40Z"))
        self.assertTrue(backup.chain_facts(policy, old + new)["facts"]["trace_export_before_backup"])


class Retention(U.TempCase):
    def setUp(self):
        super().setUp()
        built = U.backup_instance(self.tmp, "ret", days=("2026-09-10", "2026-09-11", "2026-09-12"))
        self.repo = backup.Repo(self.path("repo_a"))
        self.ids = [n["backup_a"] for n in built["nights"]]

    def decide(self, keep_last, verified):
        policy, _ = backup.parse_policy(U.policy_doc(keep_last=keep_last))
        return backup.retention(policy, self.repo, set(verified))

    def test_nothing_to_supersede(self):
        self.assertEqual(self.decide(3, [])["decision"], "nothing")
        self.assertEqual(self.decide(10, [])["decision"], "nothing")

    def test_supersede_when_a_kept_snapshot_is_verified(self):
        decision = self.decide(1, [self.ids[2]])
        self.assertEqual([decision["decision"], decision["rule"], decision["keep"], decision["supersede"]],
                         ["supersede", "RT-020", [self.ids[2]], self.ids[:2]])

    def test_blocked_when_no_kept_snapshot_is_verified(self):
        for verified in ([], [self.ids[0]], [self.ids[0], self.ids[1]]):
            with self.subTest(verified=verified):
                decision = self.decide(1, verified)
                self.assertEqual([decision["decision"], decision["class"]], ["block", "retention_deletes_only_valid"])
                self.assertEqual(backup.apply_retention(self.repo, decision), [])
        self.assertEqual(self.repo.snapshot_ids(), self.ids)

    def test_keep_last_zero_is_blocked(self):
        decision = self.decide(0, self.ids)
        self.assertEqual([decision["decision"], decision["class"], decision["supersede"]],
                         ["block", "retention_deletes_only_valid", self.ids])

    def test_keep_last_that_is_not_a_count_is_blocked(self):
        for value in (-1, "many", None, True, 1.5):
            with self.subTest(keep_last=value):
                decision = self.decide(value, self.ids)
                self.assertEqual([decision["decision"], decision["class"], decision["supersede"]],
                                 ["block", "retention_unreadable", []])

    def test_apply_moves_manifests_and_deletes_nothing(self):
        pack = jsonio.sha256_file(self.repo.pack_path)
        index = jsonio.sha256_file(self.repo.index_path)
        before = {sid: fsx.read_bytes(self.repo.snapshot_path(sid)) for sid in self.ids}
        moved = backup.apply_retention(self.repo, self.decide(1, [self.ids[2]]))
        self.assertEqual(moved, self.ids[:2])
        self.assertEqual(self.repo.snapshot_ids(), [self.ids[2]])
        for sid in moved:
            self.assertEqual(fsx.read_bytes(os.path.join(self.repo.path, "superseded", f"{sid}.json")), before[sid])
        self.assertEqual([jsonio.sha256_file(self.repo.pack_path), jsonio.sha256_file(self.repo.index_path)], [pack, index])

    def test_a_superseded_snapshot_can_still_be_restored_by_hand(self):
        backup.apply_retention(self.repo, self.decide(1, [self.ids[2]]))
        fsx.move(os.path.join(self.repo.path, "superseded", f"{self.ids[1]}.json"), self.repo.snapshot_path(self.ids[1]))
        result = drill.run(self.path("source"), self.repo.path, "a", self.path("scratch"), T2, snapshot=self.ids[1])
        self.assertEqual(result.mismatched_files, ["dati/00/" + U.ACCENTED[0], "export/orders-export.csv"])  # the two files that changed since


if __name__ == "__main__":
    unittest.main()
