"""Finding T17: an OK must say something about the secondary repository.

Source: the evaluator's blind run of 2026-09-30 (seed 20261011) on ``v2.0.0-freeze``, recorded in
``eval/history.json``. Its only failing row was the hand-written instance hand-0007, copied here
byte for byte under ``tests/data/hand-0007`` (synthetic content, relative paths only; the two
packs are stored with a ``.txt`` suffix so that the scanner reads them): the index of
the secondary repository is cut halfway through (804 of about 1600 bytes). The lab answered OK,
exit 0, "RUN OK": it judged the secondary by the ``created`` field of its newest manifest and
never read its index or its pack. That one row is the missed fault (342/343), the wrong verdict
(249/250) and the ``never_event_restore`` of that run.

Now the drill result is ``ok`` only when every backup part in scope - both repositories, every
index, every block of every pack, every snapshot manifest - was read completely and verified.
Anything cut short, empty, unreadable or unparseable gives a result that is not ok, with its
location, and the report says per repository what was verified. The siblings of the defect are
here too: a data part cut short, an empty index, an index with a repeated key, an index that lists
parts the pack does not hold, a manifest that needs parts the index does not list, an older
manifest cut short, a snapshots folder that is not a folder, and a secondary missing altogether.
"""
import os
import unittest
from unittest import mock

from lab import backup, drill, fsx, jsonio, routes
from lab import restore as restore_module
from lab import run as lab_run
from tests import _util as U

HAND_0007 = os.path.join(U.ROOT, "tests", "data", "hand-0007")
# The two packs are stored as ``pack.bin.txt`` so that tools/scan.py reads them (it skips ``*.bin``);
# they are UTF-8 text blocks. ``copy_hand_0007`` puts them back under their name.
STORED_AS = {"repo_a/pack.bin.txt": "repo_a/pack.bin", "repo_b/pack.bin.txt": "repo_b/pack.bin"}
# tree digest (lab/jsonio.py tree_digest) of the evaluator's hand-0007 folder, 19 files: the copy is byte for byte
HAND_0007_TREE_SHA256 = "414dacb6f7450ea81b34622b4ea59feacab3bbbd4158f7f49f07e842c95f290a"
# the evaluator's label of hand-0007 (labels.jsonl of the hand-written set), as recorded
HAND_0007_LABEL = {"instance": "hand-0007", "clean": False, "verdict": "ALERT",
                   "faults": [{"class": "repo_b_unreadable", "verdict": "ALERT", "file": "repo_b", "items": None}]}
DAYS = ("2026-09-10", "2026-09-11", "2026-09-12")
score = U.load_module("eval/score.py")


def copy_hand_0007(dest: str) -> None:
    for rel in fsx.walk_files(HAND_0007):
        fsx.write_bytes(fsx.join(dest, STORED_AS.get(rel, rel)), fsx.read_bytes(fsx.join(HAND_0007, rel)))


def audit(inst: str, work: str) -> tuple[dict, str, int]:
    code, text = U.SC.cli(inst, work)
    return jsonio.read(os.path.join(work, "report.json")), text, code


def brief(report: dict) -> list:
    return [[f["class"], f["file"], f["item"], f["rule"]] for f in report["findings"]]


# ----------------------------------------------------------------------------- the evaluator's instance

class Hand0007(U.TempCase):
    def setUp(self):
        super().setUp()
        self.inst = self.path("hand-0007")
        copy_hand_0007(self.inst)
        self.report, self.text, self.code = audit(self.inst, self.path("work"))

    def test_the_fixture_is_the_evaluator_instance(self):
        rels = fsx.walk_files(self.inst)
        self.assertEqual(len(rels), 19)
        self.assertEqual(jsonio.tree_digest((rel, jsonio.sha256_file(fsx.join(self.inst, rel))) for rel in rels),
                         HAND_0007_TREE_SHA256)
        self.assertEqual(fsx.size(os.path.join(self.inst, "repo_b", "index.json")), 804)
        self.assertEqual(jsonio.read(os.path.join(self.inst, "instance.json")),
                         {"instance_id": "hand-0007", "as_of": "2026-09-18T07:00:00Z", "scope": ["backup"]})
        with self.assertRaises(ValueError):
            jsonio.read(os.path.join(self.inst, "repo_b", "index.json"))

    def test_the_index_cut_in_half_is_an_alert_on_the_secondary_not_ok(self):
        label = HAND_0007_LABEL["faults"][0]
        self.assertEqual([self.report["verdict"], self.code], [HAND_0007_LABEL["verdict"], 1])
        self.assertNotIn("RUN OK", self.text)
        self.assertEqual([[f["class"], f["verdict"], f["file"]] for f in self.report["findings"]],
                         [[label["class"], label["verdict"], label["file"]]])
        self.assertEqual(self.report["findings"][0]["rule"], "A-035")

    def test_the_drill_is_not_ok_while_the_primary_restore_held(self):
        d = self.report["drill"]
        self.assertEqual([d["result"], d["primary_result"], d["repo"]], ["failed", "ok", "a"])
        self.assertEqual([d["repositories"]["a"]["verified"], d["repositories"]["b"]["verified"]], [True, False])

    def test_the_report_says_per_repository_what_was_verified_with_the_location(self):
        d = self.report["drill"]
        a, b = d["repositories"]["a"], d["repositories"]["b"]
        self.assertTrue(a["statement"].startswith("verified: index read in full"), a["statement"])
        self.assertEqual([a["index"]["read"], a["pack"]["read"], a["snapshots"]["read"]],
                         [True, True, ["20260916T023310Z", "20260917T023310Z", "20260918T023310Z"]])
        self.assertEqual([[p["kind"], p["location"]] for p in b["problems"]], [["index_unreadable", "repo_b/index.json"]])
        self.assertIn("file of 804 bytes", b["problems"][0]["note"])
        self.assertTrue(b["statement"].startswith("NOT verified: repo_b/index.json: index_unreadable"), b["statement"])
        self.assertFalse(b["index"]["read"])
        self.assertEqual([[u["repo"], u["kind"], u["location"]] for u in d["unverified"]],
                         [["b", "index_unreadable", "repo_b/index.json"]])
        self.assertIn("repo    b (secondary, repo_b): NOT verified: repo_b/index.json", self.text)
        self.assertIn("backup  drill result for the whole backup part (every repository read and verified): failed", self.text)

    def test_the_secondary_age_is_not_guessed_from_a_manifest(self):
        second = self.report["secondary"]
        self.assertEqual(second["facts"], {"secondary_readable": False, "secondary_has_snapshots": True})
        self.assertNotIn("lag_hours", second)
        self.assertEqual([[u["kind"], u["location"]] for u in second["unreadable"]], [["index_unreadable", "repo_b/index.json"]])

    def test_the_scorer_rule_of_the_never_event_does_not_fire(self):
        ref = score.reference_view(self.inst)
        self.assertIn("backup", ref["unread"])          # the reference cannot read it either: nobody vouches for it
        drill_ok = self.report["drill"]["result"] == "ok"
        fallback_ok = bool(self.report["fallback"] and self.report["fallback"]["result"] == "ok")
        self.assertEqual([drill_ok, fallback_ok], [False, False])


# ----------------------------------------------------------------------------- siblings on the secondary

def cut_half(path: str) -> None:
    data = fsx.read_bytes(path)
    fsx.write_bytes(path, data[:len(data) // 2])


def b_index_cut_in_half(repo: backup.Repo) -> None:
    cut_half(repo.index_path)


def b_index_empty(repo: backup.Repo) -> None:
    fsx.write_bytes(repo.index_path, b"")


def b_index_repeated_key(repo: backup.Repo) -> None:
    """Two 'blocks' in one index: the first is empty. Last-one-wins would read the full one and say nothing."""
    raw = fsx.read_bytes(repo.index_path)
    fsx.write_bytes(repo.index_path, b'{"blocks": {}, ' + raw.lstrip()[1:])


def b_index_not_json(repo: backup.Repo) -> None:
    fsx.write_bytes(repo.index_path, b"\xff\xfe not an index")


def b_pack_cut_short(repo: backup.Repo) -> None:
    """The data part cut short: the index lists blocks the pack no longer holds."""
    data = fsx.read_bytes(repo.pack_path)
    fsx.write_bytes(repo.pack_path, data[:len(data) - 700])


def b_pack_missing(repo: backup.Repo) -> None:
    os.remove(repo.pack_path)


def b_index_lists_parts_beyond_the_pack(repo: backup.Repo) -> None:
    index = repo.load_index()
    sha = sorted(index["blocks"])[0]
    index["blocks"][sha] = [len(fsx.read_bytes(repo.pack_path)) + 10, 64]
    jsonio.write(repo.index_path, index)


def b_index_misses_a_part(repo: backup.Repo) -> None:
    """The index lost an entry that an older manifest still needs."""
    index = repo.load_index()
    oldest = repo.read_snapshot(repo.snapshot_ids()[0])
    del index["blocks"][oldest["files"][0]["blocks"][0]]
    jsonio.write(repo.index_path, index)


def b_older_manifest_cut(repo: backup.Repo) -> None:
    cut_half(repo.snapshot_path(repo.snapshot_ids()[0]))


def b_block_corrupt(repo: backup.Repo) -> None:
    index = repo.load_index()
    off, length = index["blocks"][sorted(index["blocks"])[0]]
    pack = bytearray(fsx.read_bytes(repo.pack_path))
    pack[off + length // 2] ^= 0x5A
    fsx.write_bytes(repo.pack_path, bytes(pack))


def b_snapshots_not_a_folder(repo: backup.Repo) -> None:
    fsx.rmtree(os.path.join(repo.path, "snapshots"))
    fsx.write_bytes(os.path.join(repo.path, "snapshots"), b"")


# damage -> (kind, location inside the instance) of the first problem named
SECONDARY_DAMAGE = {
    b_index_cut_in_half: ("index_unreadable", "repo_b/index.json"),
    b_index_empty: ("index_unreadable", "repo_b/index.json"),
    b_index_repeated_key: ("index_unreadable", "repo_b/index.json"),
    b_index_not_json: ("index_unreadable", "repo_b/index.json"),
    b_pack_cut_short: ("block_missing", "repo_b/pack.bin"),
    b_pack_missing: ("pack_unreadable", "repo_b/pack.bin"),
    b_index_lists_parts_beyond_the_pack: ("block_missing", "repo_b/pack.bin"),
    b_index_misses_a_part: ("block_missing", "repo_b/snapshots/{oldest}.json"),
    b_older_manifest_cut: ("snapshot_unreadable", "repo_b/snapshots/{oldest}.json"),
    b_block_corrupt: ("block_corrupt", "repo_b/pack.bin"),
    b_snapshots_not_a_folder: ("snapshots_unlisted", "repo_b/snapshots/"),
}


class SecondarySiblings(U.TempCase):
    def build(self, name: str) -> str:
        inst = self.path(name)
        U.backup_instance(inst, "t17", days=DAYS)
        return inst

    def test_control_both_repositories_read_completely_is_ok_and_says_so(self):
        report, text, code = audit(self.build("control"), self.path("work"))
        self.assertEqual([report["verdict"], code, report["drill"]["result"], report["drill"]["primary_result"]], ["OK", 0, "ok", "ok"])
        for name in ("a", "b"):
            view = report["drill"]["repositories"][name]
            self.assertTrue(view["verified"], name)
            self.assertEqual(view["problems"], [])
            self.assertEqual(view["snapshots"]["read"], view["snapshots"]["listed"])
            self.assertEqual(len(view["snapshots"]["read"]), 3)
            self.assertEqual(view["pack"]["blocks_verified"], view["index"]["blocks"])
            self.assertIn(f"repo    {name} (", text)
        self.assertEqual(report["drill"]["unverified"], [])
        self.assertIn("RUN OK", text)

    def test_every_damage_to_the_secondary_is_named_and_never_ok(self):
        for damage, (kind, location) in SECONDARY_DAMAGE.items():
            with self.subTest(damage=damage.__name__):
                inst = self.build(damage.__name__)
                repo = backup.Repo(os.path.join(inst, "repo_b"))
                location = location.format(oldest=repo.snapshot_ids()[0])
                damage(repo)
                report, text, code = audit(inst, self.path("work-" + damage.__name__))
                self.assertNotEqual(report["verdict"], "OK")
                self.assertNotEqual(code, 0)
                self.assertNotIn("RUN OK", text)
                self.assertEqual(brief(report)[0][:2], ["repo_b_unreadable", "repo_b"])
                self.assertEqual(report["findings"][0]["rule"], "A-035")
                self.assertEqual([report["drill"]["result"], report["drill"]["primary_result"]], ["failed", "ok"])
                b = report["drill"]["repositories"]["b"]
                self.assertFalse(b["verified"])
                self.assertEqual([b["problems"][0]["kind"], b["problems"][0]["location"]], [kind, location])
                self.assertEqual(report["secondary"]["unreadable"][0]["location"], location)
                self.assertIn(f"NOT verified: {location}: {kind}", text)

    def test_a_secondary_missing_altogether_is_stale_and_the_drill_is_not_ok(self):
        inst = self.build("missing")
        fsx.rmtree(os.path.join(inst, "repo_b"))
        report, text, code = audit(inst, self.path("work"))
        self.assertEqual(brief(report), [["repo_b_stale", "repo_b", None, "A-040"]])
        self.assertEqual([report["verdict"], code], ["ALERT", 1])
        self.assertEqual([report["drill"]["result"], report["drill"]["primary_result"]], ["failed", "ok"])
        b = report["drill"]["repositories"]["b"]
        self.assertEqual([b["present"], b["verified"]], [False, False])
        self.assertEqual(b["statement"], "NOT verified: the repository holds nothing (no index, no pack, no snapshot manifest)")
        self.assertEqual([[u["repo"], u["kind"]] for u in report["drill"]["unverified"]], [["b", "not_verified"]])
        self.assertNotIn("RUN OK", text)

    def test_an_empty_secondary_is_stale_and_the_drill_is_not_ok(self):
        inst = self.build("empty")
        repo = backup.Repo(os.path.join(inst, "repo_b"))
        fsx.rmtree(os.path.join(inst, "repo_b"))
        jsonio.write(repo.index_path, {"format": 1, "block_size": 1024, "blocks": {}})
        fsx.write_bytes(repo.pack_path, b"")
        report, text, _ = audit(inst, self.path("work"))
        self.assertEqual(brief(report), [["repo_b_stale", "repo_b", None, "A-040"]])
        self.assertEqual(report["drill"]["result"], "failed")
        self.assertTrue(report["drill"]["repositories"]["b"]["statement"].startswith("NOT verified: no snapshot to restore"))


# ----------------------------------------------------------------------------- the same complete read on the primary

class PrimaryCompleteRead(U.TempCase):
    def build(self) -> str:
        inst = self.path("inst")
        U.backup_instance(inst, "t17a", days=DAYS)
        return inst

    def test_an_older_primary_manifest_cut_short_fails_the_drill(self):
        inst = self.build()
        repo = backup.Repo(os.path.join(inst, "repo_a"))
        oldest = repo.snapshot_ids()[0]
        cut_half(repo.snapshot_path(oldest))                          # the newest one still restores
        report, text, code = audit(inst, self.path("work"))
        self.assertEqual([report["verdict"], code, report["drill"]["result"]], ["FAILED", 3, "failed"])
        self.assertEqual(brief(report)[0][:2], ["repo_unreadable", "repo_a"])
        self.assertEqual(report["drill"]["rule"], "B-000")
        a = report["drill"]["repositories"]["a"]
        self.assertEqual([a["problems"][0]["kind"], a["problems"][0]["location"]],
                         ["snapshot_unreadable", f"repo_a/snapshots/{oldest}.json"])
        self.assertFalse(a["verified"])
        self.assertNotIn("RUN OK", text)

    def test_a_corrupt_block_used_only_by_an_older_snapshot_fails_the_drill(self):
        inst = self.build()
        repo = backup.Repo(os.path.join(inst, "repo_a"))
        ids = repo.snapshot_ids()
        newest = {b for e in repo.read_snapshot(ids[-1])["files"] for b in e["blocks"]}
        older = sorted({b for e in repo.read_snapshot(ids[0])["files"] for b in e["blocks"]} - newest)
        self.assertTrue(older)
        off, length = repo.load_index()["blocks"][older[0]]
        pack = bytearray(fsx.read_bytes(repo.pack_path))
        pack[off] ^= 0x01
        fsx.write_bytes(repo.pack_path, bytes(pack))
        report, text, code = audit(inst, self.path("work"))
        self.assertEqual([report["verdict"], report["drill"]["result"]], ["FAILED", "failed"])
        self.assertEqual(report["drill"]["mismatched_files"], [])                 # the newest snapshot restored well...
        self.assertEqual(brief(report)[0][:2], ["block_corrupt", "repo_a"])       # ...and the drill still does not hold
        self.assertIn("block_corrupt", [p["kind"] for p in report["drill"]["parts"]])

    def test_a_primary_pack_missing_is_said_not_read_as_empty(self):
        inst = self.build()
        repo = backup.Repo(os.path.join(inst, "repo_a"))
        os.remove(repo.pack_path)
        rebuilt = restore_module.restore(repo, repo.snapshot_ids()[-1], self.path("scratch"))
        self.assertEqual([p["kind"] for p in rebuilt["problems"]], ["pack_unreadable"])
        report, _, code = audit(inst, self.path("work"))
        self.assertEqual([report["verdict"], code, brief(report)[0][:2]], ["FAILED", 3, ["repo_unreadable", "repo_a"]])


# ----------------------------------------------------------------------------- no silent fallback underneath

class NoSilentFallback(U.TempCase):
    def test_a_repeated_key_is_an_error_not_last_one_wins(self):
        path = self.path("dup.json")
        fsx.write_bytes(path, b'{"a": 1, "a": 2}')
        with self.assertRaises(ValueError):
            backup.read_json_strict(path)

    def test_a_truncated_or_empty_file_says_where_it_stopped(self):
        path = self.path("cut.json")
        fsx.write_bytes(path, b'{"blocks": {"ab')
        with self.assertRaises(ValueError) as caught:
            backup.read_json_strict(path)
        self.assertIn("file of 15 bytes", str(caught.exception))

    def test_a_folder_that_cannot_be_listed_raises_instead_of_being_skipped(self):
        folder = self.path("tree")
        fsx.write_bytes(os.path.join(folder, "x.txt"), b"x")
        with mock.patch("os.scandir", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(OSError):
                fsx.walk_files(folder)
        self.assertEqual(fsx.walk_files(self.path("no-such-folder")), [])

    def test_a_snapshots_folder_that_cannot_be_listed_is_not_no_snapshot(self):
        inst = self.path("inst")
        U.backup_instance(inst, "list", days=DAYS)
        repo = backup.Repo(os.path.join(inst, "repo_b"))
        with mock.patch("os.scandir", side_effect=PermissionError(13, "denied")):
            with self.assertRaises(backup.RepoError):
                repo.snapshot_ids()
            found = backup.inspect(repo)
        self.assertEqual([[p["kind"], p["location"]] for p in found["problems"]][:1], [["snapshots_unlisted", "snapshots/"]])
        self.assertFalse(found["verified"])

    def test_a_routes_folder_that_cannot_be_listed_is_a_problem_not_an_empty_table(self):
        with mock.patch.object(routes, "route_files", side_effect=PermissionError(13, "denied")):
            loaded, problems, files = routes.load(self.path("inst"))
        self.assertEqual([loaded, files, [p[0] for p in problems]], [[], [], ["routes"]])

    def test_a_drill_without_the_complete_read_is_not_ok(self):
        good = dict(repo="a", snapshot="20260912T023110Z", as_of="2026-09-12T06:10:00Z", source_count=3, declared_count=3,
                    restored_count=3, mismatched=(), problems=(), source_manifest_sha256="0" * 64)
        self.assertFalse(drill.DrillResult(**good).ok)                              # nothing read: not ok
        self.assertEqual(drill.DrillResult(**good).parts, (("repo_unread", None),))
        facts = drill.facts(drill.DrillResult(**good), False)
        self.assertFalse(facts["repo_readable"])


if __name__ == "__main__":
    unittest.main()
