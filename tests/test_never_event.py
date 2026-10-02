"""The never-event: a restore declared successful while even one file's hash differs from the source manifest.

Every test here *tries to make it happen* - damaged blocks, forged manifests that are perfectly
consistent with themselves, a registry edited by hand, a restored file changed between the
restore and the check - and proves the pack refuses: result ``failed``, verdict not OK, exit
code not 0, and the words "RUN OK" absent from the console.

The judge of the property tests is ``corpus/reference_hashes.py``: a naive restore compared with
a direct SHA-256 of the source tree, written without any code of the lab.
"""
import os
import random
import unittest
from unittest import mock

from corpus import reference_hashes
from lab import backup, drill, fsx, jsonio, timeutil
from lab import restore as restore_module
from lab import run as lab_run
from tests import _util as U

AS_OF = timeutil.parse_utc("2026-09-12T06:10:00Z")


# ----------------------------------------------------------------------------- ways to damage a repository

def newest(repo_dir: str) -> tuple[backup.Repo, dict]:
    repo = backup.Repo(repo_dir)
    return repo, repo.read_snapshot(repo.snapshot_ids()[-1])


def flip_byte(repo_dir: str, rng: random.Random) -> None:
    repo, snap = newest(repo_dir)
    entry = rng.choice([e for e in snap["files"] if e["blocks"]])
    offset, length = repo.load_index()["blocks"][rng.choice(entry["blocks"])]
    pack = bytearray(fsx.read_bytes(repo.pack_path))
    pack[offset + rng.randrange(length)] ^= rng.randint(1, 255)
    fsx.write_bytes(repo.pack_path, bytes(pack))


def truncate_pack(repo_dir: str, rng: random.Random) -> None:
    repo, _ = newest(repo_dir)
    pack = fsx.read_bytes(repo.pack_path)
    fsx.write_bytes(repo.pack_path, pack[:len(pack) - rng.randint(1, min(3000, len(pack) - 1))])


def drop_from_listing(repo_dir: str, rng: random.Random) -> None:
    repo, snap = newest(repo_dir)
    snap["files"].pop(rng.randrange(len(snap["files"])))
    jsonio.write(repo.snapshot_path(snap["id"]), snap)


def drop_and_recount(repo_dir: str, rng: random.Random) -> None:
    """A listing that lost a file *and* had its declared count lowered to match: consistent with itself."""
    repo, snap = newest(repo_dir)
    snap["files"].pop(rng.randrange(len(snap["files"])))
    snap["source_file_count"] = len(snap["files"])
    jsonio.write(repo.snapshot_path(snap["id"]), snap)


def cut_blocks(repo_dir: str, rng: random.Random) -> None:
    repo, snap = newest(repo_dir)
    entry = rng.choice([e for e in snap["files"] if len(e["blocks"]) >= 2])
    entry["blocks"] = entry["blocks"][:-1]
    jsonio.write(repo.snapshot_path(snap["id"]), snap)


def forge_file(repo_dir: str, rng: random.Random) -> None:
    """Replace one file of the newest snapshot by other bytes, consistently: new blocks in the pack,
    index updated, size and SHA-256 of the entry rewritten. The snapshot verifies against itself."""
    repo, snap = newest(repo_dir)
    entry = rng.choice(snap["files"])
    data = rng.randbytes(rng.choice([entry["size"], entry["size"] + 1, max(0, entry["size"] - 1), 700]))
    if jsonio.sha256_bytes(data) == entry["sha256"]:
        data += b"x"
    index = repo.load_index()
    pack = bytearray(fsx.read_bytes(repo.pack_path))
    blocks = []
    for start in range(0, len(data), index["block_size"]):
        chunk = data[start:start + index["block_size"]]
        sha = jsonio.sha256_bytes(chunk)
        if sha not in index["blocks"]:
            index["blocks"][sha] = [len(pack), len(chunk)]
            pack += chunk
        blocks.append(sha)
    entry.update({"size": len(data), "sha256": jsonio.sha256_bytes(data), "blocks": blocks})
    snap["total_bytes"] = sum(e["size"] for e in snap["files"])
    fsx.write_bytes(repo.pack_path, bytes(pack))
    jsonio.write(repo.index_path, index)
    jsonio.write(repo.snapshot_path(snap["id"]), snap)


def swap_index(repo_dir: str, rng: random.Random) -> None:
    repo, snap = newest(repo_dir)
    index = repo.load_index()
    used = sorted({b for e in snap["files"] for b in e["blocks"]})
    a, b = rng.sample(used, 2)
    index["blocks"][a], index["blocks"][b] = index["blocks"][b], index["blocks"][a]
    jsonio.write(repo.index_path, index)


def add_extra_file(repo_dir: str, rng: random.Random) -> None:
    """The snapshot lists one file the source does not have (count raised to match)."""
    repo, snap = newest(repo_dir)
    twin = dict(rng.choice(snap["files"]), path="dati/file-che-non-esiste.txt")
    snap["files"] = sorted(snap["files"] + [twin], key=lambda e: e["path"])
    snap["source_file_count"] = len(snap["files"])
    jsonio.write(repo.snapshot_path(snap["id"]), snap)


def rename_in_listing(repo_dir: str, rng: random.Random) -> None:
    """One file of the newest snapshot is listed under another name: same bytes, same count, wrong place."""
    repo, snap = newest(repo_dir)
    entry = rng.choice(snap["files"])
    entry["path"] = "dati/rinominato-" + entry["path"].rsplit("/", 1)[-1]
    snap["files"] = sorted(snap["files"], key=lambda e: e["path"])
    jsonio.write(repo.snapshot_path(snap["id"]), snap)


def change_source(inst: str, rng: random.Random) -> None:
    rel = rng.choice(fsx.walk_files(os.path.join(inst, "source")))
    fsx.append_bytes(fsx.join(os.path.join(inst, "source"), rel), b"changed after the backup")


def add_source(inst: str, rng: random.Random) -> None:
    fsx.write_bytes(os.path.join(inst, "source", "dati", "nuovo-dopo-il-backup.txt"), rng.randbytes(40))


REPO_DAMAGE = [flip_byte, truncate_pack, drop_from_listing, drop_and_recount, cut_blocks, forge_file, swap_index, add_extra_file,
               rename_in_listing]


class Base(U.TempCase):
    def setUp(self):
        super().setUp()
        self.base = self.path("base")
        U.backup_instance(self.base, "ne")

    def copy(self, name: str) -> str:
        dest = self.path(name)
        fsx.copytree(self.base, dest)
        return dest

    def drill(self, inst: str, repo: str = "a") -> drill.DrillResult:
        return drill.run(os.path.join(inst, "source"), os.path.join(inst, f"repo_{repo}"), repo, self.path("scratch"), AS_OF)


class DrillRefuses(Base):
    def test_control_an_untouched_repository_restores_and_verifies(self):
        result = self.drill(self.base)
        self.assertTrue(result.ok)
        self.assertEqual([result.source_count, result.declared_count, result.restored_count], [7, 7, 7])
        self.assertEqual(reference_hashes.check(self.base)["repos"]["a"]["differing"], [])

    def test_each_kind_of_damage_is_refused_and_names_files(self):
        for damage in REPO_DAMAGE:
            with self.subTest(damage=damage.__name__):
                inst = self.copy(damage.__name__)
                damage(os.path.join(inst, "repo_a"), random.Random(f"ne:{damage.__name__}"))
                result = self.drill(inst)
                differing = reference_hashes.check(inst)["repos"]["a"]["differing"]
                self.assertTrue(differing, "the damage must be real for the test to mean something")
                self.assertFalse(result.ok)
                self.assertEqual(result.result, "failed")
                self.assertEqual(result.mismatched_files, differing)

    def test_forged_snapshot_consistent_with_itself_is_caught_by_the_source_manifest(self):
        inst = self.copy("forged")
        forge_file(os.path.join(inst, "repo_a"), random.Random("forged"))
        repo, snap = newest(os.path.join(inst, "repo_a"))
        rebuilt = restore_module.restore(repo, snap["id"], self.path("out"))
        self.assertEqual(rebuilt["problems"], [])                       # the restore step alone sees nothing wrong
        self.assertEqual(len(rebuilt["written"]), snap["source_file_count"])
        result = self.drill(inst)
        self.assertFalse(result.ok)                                     # the comparison with the source does
        self.assertEqual(len(result.mismatched_files), 1)

    def test_source_changed_after_the_backup(self):
        for change in (change_source, add_source):
            with self.subTest(change=change.__name__):
                inst = self.copy(change.__name__)
                change(inst, random.Random("src"))
                result = self.drill(inst)
                self.assertFalse(result.ok)
                self.assertEqual(result.mismatched_files, reference_hashes.check(inst)["repos"]["a"]["differing"])

    def test_source_file_removed_after_the_backup_is_an_extra_file_in_the_restore(self):
        inst = self.copy("removed")
        gone = "dati/03/" + U.ACCENTED[3]
        fsx.move(fsx.join(os.path.join(inst, "source"), gone), self.path("set-aside.txt"))
        result = self.drill(inst)
        self.assertFalse(result.ok)
        self.assertEqual(list(result.mismatched), [(gone, "extra")])

    def test_file_changed_between_restore_and_check_is_caught_because_the_check_re_reads_the_disk(self):
        real = restore_module.restore

        def restore_then_tamper(repo, sid, dest):
            out = real(repo, sid, dest)
            victim = out["written"][0]
            fsx.write_bytes(fsx.join(dest, victim), fsx.read_bytes(fsx.join(dest, victim)) + b"!")
            return out

        with mock.patch.object(restore_module, "restore", side_effect=restore_then_tamper):
            result = self.drill(self.base)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.mismatched_files), 1)

    def test_same_size_different_bytes_is_caught_by_the_hash(self):
        real = restore_module.restore

        def restore_then_flip(repo, sid, dest):
            out = real(repo, sid, dest)
            victim = next(rel for rel in out["written"] if fsx.size(fsx.join(dest, rel)) > 0)
            data = bytearray(fsx.read_bytes(fsx.join(dest, victim)))
            data[0] ^= 1
            fsx.write_bytes(fsx.join(dest, victim), bytes(data))
            return out

        with mock.patch.object(restore_module, "restore", side_effect=restore_then_flip):
            result = self.drill(self.base)
        self.assertFalse(result.ok)
        self.assertEqual([reason for _, reason in result.mismatched], ["hash"])

    def test_stale_files_in_the_scratch_folder_cannot_vouch_for_anything(self):
        inst = self.copy("stale")
        fsx.copytree(os.path.join(inst, "source"), self.path("scratch"))      # a perfect copy is already there
        flip_byte(os.path.join(inst, "repo_a"), random.Random("stale"))
        self.assertFalse(self.drill(inst).ok)                                # the scratch folder is emptied first

    def test_empty_source_is_never_a_successful_restore(self):
        inst = self.copy("empty")
        fsx.rmtree(os.path.join(inst, "source"))
        fsx.makedirs(os.path.join(inst, "source"))
        repo, snap = newest(os.path.join(inst, "repo_a"))
        snap.update({"files": [], "source_file_count": 0, "total_bytes": 0})   # and a snapshot that agrees: nothing, equal to nothing
        jsonio.write(repo.snapshot_path(snap["id"]), snap)
        result = self.drill(inst)
        self.assertEqual([result.source_count, result.declared_count, result.restored_count, list(result.mismatched)], [0, 0, 0, []])
        self.assertFalse(result.ok)

    def test_no_snapshot_and_unreadable_repository(self):
        inst = self.copy("nosnap")
        self.assertFalse(drill.run(os.path.join(inst, "source"), self.path("no-such-repo"), "a", self.path("scratch"), AS_OF).ok)
        fsx.write_bytes(os.path.join(inst, "repo_a", "index.json"), b"[]")
        result = self.drill(inst)
        self.assertFalse(result.ok)
        self.assertEqual([kind for kind, _ in result.problems], ["repo_unreadable"])

    def test_a_path_that_escapes_the_restore_folder_is_not_written(self):
        inst = self.copy("escape")
        repo, snap = newest(os.path.join(inst, "repo_a"))
        snap["files"][0]["path"] = "../outside.txt"
        jsonio.write(repo.snapshot_path(snap["id"]), snap)
        result = self.drill(inst)
        self.assertFalse(result.ok)
        self.assertIn("unsafe_path", [kind for kind, _ in result.problems])
        self.assertFalse(fsx.exists(self.path("outside.txt")))

    def test_ok_is_computed_from_the_evidence_and_cannot_be_passed_in(self):
        read_whole = {"verified": True, "problems": []}          # the complete read of the repository (backup.inspect)
        good = dict(repo="a", snapshot="20260912T023110Z", as_of="2026-09-12T06:10:00Z", source_count=3, declared_count=3,
                    restored_count=3, mismatched=(), problems=(), source_manifest_sha256="0" * 64, inspection=read_whole)
        self.assertTrue(drill.DrillResult(**good).ok)
        for label, change in {"no snapshot": {"snapshot": None}, "empty source": {"source_count": 0, "declared_count": 0, "restored_count": 0},
                              "declared unknown": {"declared_count": None}, "declared differs": {"declared_count": 2},
                              "restored differs": {"restored_count": 2}, "a problem": {"problems": (("block_corrupt", "x"),)},
                              "a mismatch": {"mismatched": (("x", "hash"),)},
                              "repository not read (T17)": {"inspection": None},
                              "repository not verified (T17)": {"inspection": {"verified": False, "problems": []}},
                              "a part unreadable (T17)": {"inspection": {"verified": True, "problems": [
                                  {"kind": "index_unreadable", "location": "index.json", "note": "cut short"}]}}}.items():
            with self.subTest(case=label):
                self.assertFalse(drill.DrillResult(**dict(good, **change)).ok)
                self.assertEqual(drill.DrillResult(**dict(good, **change)).result, "failed")
        with self.assertRaises(TypeError):
            drill.DrillResult(**dict(good, ok=True))
        with self.assertRaises(AttributeError):
            drill.DrillResult(**dict(good, mismatched=(("x", "hash"),))).ok = True


class RegistryRefuses(Base):
    def test_a_failed_drill_is_recorded_as_failed(self):
        inst = self.copy("reg")
        flip_byte(os.path.join(inst, "repo_a"), random.Random("reg"))
        entry = drill.append(os.path.join(inst, drill.FILE), self.drill(inst))
        self.assertEqual(entry["result"], "failed")
        state = drill.read_registry(os.path.join(inst, drill.FILE))
        self.assertTrue(state["chain_ok"])
        self.assertNotIn(entry["snapshot"], drill.verified(state, "a"))      # the latest drill of that snapshot failed

    def test_a_registry_line_edited_by_hand_breaks_the_chain_and_vouches_for_nothing(self):
        inst = self.copy("tamper")
        flip_byte(os.path.join(inst, "repo_a"), random.Random("tamper"))
        path = os.path.join(inst, drill.FILE)
        drill.append(path, self.drill(inst))
        text = fsx.read_bytes(path).decode("utf-8")
        self.assertIn('"result":"failed"', text)
        fsx.write_bytes(path, text.replace('"result":"failed"', '"result":"ok"').encode("utf-8"))
        state = drill.read_registry(path)
        self.assertFalse(state["chain_ok"])
        self.assertEqual(drill.verified(state, "a"), set())
        with self.assertRaises(drill.RegistryError):
            drill.append(path, self.drill(inst))

    def test_a_forged_line_with_a_recomputed_hash_still_breaks_the_next_link(self):
        path = os.path.join(self.copy("relink"), drill.FILE)
        lines = fsx.read_bytes(path).decode("utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        first = jsonio.read_jsonl(path)[0]
        first["snapshot"] = "20990101T000000Z"
        first["hash"] = drill._entry_hash(first)                 # the forger recomputes the hash of the edited line
        fsx.write_bytes(path, (jsonio.line(first) + "\n" + lines[1] + "\n").encode("utf-8"))
        self.assertFalse(drill.read_registry(path)["chain_ok"])  # ...but the next line still points at the old one

    def test_removing_a_line_or_reordering_breaks_the_chain(self):
        path = os.path.join(self.copy("drop"), drill.FILE)
        lines = fsx.read_bytes(path).decode("utf-8").splitlines()
        fsx.write_bytes(path, (lines[1] + "\n").encode("utf-8"))
        self.assertFalse(drill.read_registry(path)["chain_ok"])
        fsx.write_bytes(path, (lines[1] + "\n" + lines[0] + "\n").encode("utf-8"))
        self.assertFalse(drill.read_registry(path)["chain_ok"])

    def test_audit_with_a_tampered_registry_is_failed_and_retention_trusts_nothing(self):
        inst = self.path("prune")
        U.backup_instance(inst, "prune", days=("2026-09-10", "2026-09-11", "2026-09-12"),
                          policy=U.policy_doc(chain=["export", "backup_a", "backup_b", "prune", "verify"], keep_last=1))
        path = os.path.join(inst, drill.FILE)
        fsx.write_bytes(path, fsx.read_bytes(path).replace(b'"problems":0', b'"problems":1', 1))
        report = lab_run.audit(inst, self.path("work"))
        self.assertIn(["registry_tampered", "drills.jsonl"], [[f["class"], f["file"]] for f in report["findings"]])
        self.assertEqual(report["verdict"], "FAILED")
        self.assertEqual(report["retention"]["decision"], "block")
        self.assertEqual(report["retention"]["verified"], [])
        self.assertFalse(report["registry"]["chain_valid"])


class AuditRefuses(Base):
    def audit(self, inst: str, name: str) -> tuple[dict, str, int]:
        work = self.path("work-" + name)
        code, text = U.SC.cli(inst, work)
        return jsonio.read(os.path.join(work, "report.json")), text, code

    def assert_refused(self, report: dict, text: str, code: int) -> None:
        self.assertEqual(report["drill"]["result"], "failed")
        self.assertNotEqual(report["verdict"], "OK")
        self.assertEqual(report["verdict"], "FAILED")
        self.assertEqual(code, 3)
        self.assertNotIn("RUN OK", text)
        self.assertTrue(any(f["verdict"] == "FAILED" for f in report["findings"]))

    def test_control_the_clean_instance_is_ok(self):
        report, text, code = self.audit(self.base, "control")
        self.assertEqual([report["verdict"], code, report["drill"]["result"]], ["OK", 0, "ok"])
        self.assertIn("RUN OK", text)

    def test_every_kind_of_damage_gives_failed_exit_3_and_no_run_ok(self):
        for damage in REPO_DAMAGE:
            with self.subTest(damage=damage.__name__):
                inst = self.copy("a-" + damage.__name__)
                damage(os.path.join(inst, "repo_a"), random.Random(f"audit:{damage.__name__}"))
                self.assert_refused(*self.audit(inst, damage.__name__))

    def test_fallback_is_declared_and_is_itself_verified(self):
        inst = self.copy("fallback")
        flip_byte(os.path.join(inst, "repo_a"), random.Random("fa"))
        report, text, code = self.audit(inst, "fallback")
        self.assert_refused(report, text, code)
        self.assertEqual([report["fallback"]["declared"], report["fallback"]["repo"], report["fallback"]["result"]], [True, "b", "ok"])
        self.assertIn("fallback declared", text)

    def test_when_both_repositories_are_damaged_the_fallback_is_not_called_good(self):
        inst = self.copy("both")
        flip_byte(os.path.join(inst, "repo_a"), random.Random("ba"))
        forge_file(os.path.join(inst, "repo_b"), random.Random("bb"))
        report, text, code = self.audit(inst, "both")
        self.assert_refused(report, text, code)
        self.assertEqual(report["fallback"]["result"], "failed")
        self.assertTrue(reference_hashes.check(inst)["repos"]["b"]["differing"])

    def test_an_earlier_ok_on_record_does_not_make_todays_restore_ok(self):
        inst = self.copy("history")
        state = drill.read_registry(os.path.join(inst, drill.FILE))
        self.assertEqual([e["result"] for e in state["entries"]], ["ok", "ok"])
        truncate_pack(os.path.join(inst, "repo_a"), random.Random("hist"))
        report, text, code = self.audit(inst, "history")
        self.assert_refused(report, text, code)
        self.assertTrue(report["findings"][0]["detail"]["registry_claimed_ok"])

    def test_the_audit_never_writes_into_the_instance(self):
        inst = self.copy("readonly")
        flip_byte(os.path.join(inst, "repo_a"), random.Random("ro"))
        before = {rel: jsonio.sha256_file(fsx.join(inst, rel)) for rel in fsx.walk_files(inst)}
        self.audit(inst, "readonly")
        self.assertEqual({rel: jsonio.sha256_file(fsx.join(inst, rel)) for rel in fsx.walk_files(inst)}, before)

    def test_an_instance_that_cannot_be_read_is_failed_not_ok(self):
        inst = self.copy("noinst")
        fsx.write_bytes(os.path.join(inst, "instance.json"), b'{"instance_id": "x", "as_of": "2026-09-12 06:10:00"}')
        report, text, code = self.audit(inst, "noinst")
        self.assertEqual([report["verdict"], code], ["FAILED", 3])
        self.assertNotIn("RUN OK", text)


class Property(Base):
    """Seeded mutations: whenever the reference finds a differing file, the pack must not say ok."""

    MUTATIONS = 120

    def test_ok_implies_equal_to_the_source_over_seeded_mutations(self):
        rng = random.Random("av2:never-event:20260930")
        counts = {"refused": 0, "ok_and_equal": 0, "fallback_ok": 0, "fallback_failed": 0}
        for n in range(self.MUTATIONS):
            inst = self.copy(f"m{n:03d}")
            applied = []
            for _ in range(rng.choice([0, 1, 1, 1, 2])):
                kind = rng.choice(["repo_a", "repo_a", "repo_b", "source"])
                damage = rng.choice(REPO_DAMAGE) if kind != "source" else rng.choice([change_source, add_source])
                try:
                    damage(inst if kind == "source" else os.path.join(inst, kind), rng)
                    applied.append(f"{kind}:{damage.__name__}")
                except (backup.RepoError, KeyError, ValueError, IndexError):
                    applied.append(f"{kind}:{damage.__name__}:not-applicable")   # second damage on an already broken repository
            try:
                ref = reference_hashes.check(inst)
            except (KeyError, ValueError, OSError):
                ref = None       # the reference cannot read it: then the pack must not say ok either
            work = self.path(f"w{n:03d}")
            code, text = U.SC.cli(inst, work)
            report = jsonio.read(os.path.join(work, "report.json"))
            a_differs = ref is None or bool(ref["repos"]["a"]["differing"])
            with self.subTest(n=n, applied=applied):
                if report["drill"]["result"] == "ok":
                    self.assertFalse(a_differs, "NEVER-EVENT: restore declared ok while the reference finds differing files")
                    counts["ok_and_equal"] += 1
                if a_differs:
                    self.assertEqual(report["drill"]["result"], "failed")
                    self.assertEqual(report["verdict"], "FAILED")
                    self.assertEqual(code, 3)
                    self.assertNotIn("RUN OK", text)
                    counts["refused"] += 1
                fallback = report["fallback"]
                if fallback is not None:
                    self.assertTrue(fallback["declared"])
                    if fallback["result"] == "ok":
                        self.assertTrue(ref is not None and not ref["repos"]["b"]["differing"],
                                        "NEVER-EVENT: fallback declared ok while the reference finds differing files")
                        counts["fallback_ok"] += 1
                    else:
                        counts["fallback_failed"] += 1
                if "RUN OK" in text:
                    self.assertEqual([report["verdict"], code], ["OK", 0])
            fsx.rmtree(inst)
            fsx.rmtree(work)
        # the test is not vacuous: both outcomes and both fallback outcomes were exercised
        self.assertGreater(counts["refused"], 40)
        self.assertGreater(counts["ok_and_equal"], 15)
        self.assertGreater(counts["fallback_ok"], 10)
        self.assertGreater(counts["fallback_failed"], 3)


if __name__ == "__main__":
    unittest.main()
