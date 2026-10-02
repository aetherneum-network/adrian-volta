"""The postmortem is rendered from the report: same report, same text; facts and identifiers only."""
import os
import random
import re
import unittest

from lab import backup, fsx, jsonio, postmortem
from lab import run as lab_run
from tests import _util as U

SECTIONS = ["## Summary", "## Timeline (UTC)", "## Evidence", "## What the checks did not see", "## Follow-ups"]


class FromAFailedBackup(U.TempCase):
    def setUp(self):
        super().setUp()
        U.backup_instance(self.path("inst"), "pm")
        repo = backup.Repo(self.path("inst", "repo_a"))
        snap = repo.read_snapshot(repo.snapshot_ids()[-1])
        entry = next(e for e in snap["files"] if e["blocks"])
        offset, _ = repo.load_index()["blocks"][entry["blocks"][0]]
        pack = bytearray(fsx.read_bytes(repo.pack_path))
        pack[offset] ^= 0xFF
        fsx.write_bytes(repo.pack_path, bytes(pack))
        self.report = lab_run.audit(self.path("inst"), self.path("work"))
        self.text = fsx.read_bytes(self.path("work", "postmortem.md")).decode("utf-8")

    def test_it_is_written_next_to_the_report_only_when_the_verdict_is_failed(self):
        self.assertEqual(self.report["verdict"], "FAILED")
        self.assertEqual(self.text, postmortem.render(self.report))
        U.backup_instance(self.path("clean"), "pm-clean")
        clean = lab_run.audit(self.path("clean"), self.path("clean-work"))
        self.assertEqual(clean["verdict"], "OK")
        self.assertFalse(fsx.exists(self.path("clean-work", "postmortem.md")))

    def test_the_five_sections_are_present_in_order(self):
        positions = [self.text.index(section) for section in SECTIONS]
        self.assertEqual(positions, sorted(positions))

    def test_it_says_what_failed_what_said_fine_and_which_rule_decided(self):
        self.assertIn("- Verdict: **FAILED** (1 failing finding, 1 in total).", self.text)
        self.assertIn("did not reproduce the source", self.text)
        self.assertIn("an earlier registry entry recorded snapshot `20260912T023110Z` as restored successfully", self.text)
        self.assertIn("rule `B-010` -> class `block_corrupt` (severity rule `S-030`)", self.text)
        self.assertIn("- Fallback declared: repository `b`, snapshot `20260912T023145Z`, result `ok`.", self.text)

    def test_every_time_in_the_timeline_is_explicit_utc_and_sorted(self):
        rows = re.findall(r"^\| (\S+) \| \S+ \| .* \|$", self.text, flags=re.M)
        times = [t for t in rows if t != "time" and not t.startswith("---")]
        self.assertGreaterEqual(len(times), 2)
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", t) for t in times))
        self.assertEqual(times, sorted(times))
        self.assertIn(f"| {self.report['as_of']} | audit | audit run, verdict FAILED |", self.text)

    def test_the_same_report_gives_the_same_bytes_wherever_it_is_rendered(self):
        again = lab_run.audit(self.path("inst"), self.path("elsewhere", "deeper"))
        self.assertEqual(postmortem.render(again), self.text)
        self.assertEqual(postmortem.render(jsonio.read(self.path("work", "report.json"))), self.text)

    def test_it_names_no_person_no_machine_and_no_path_outside_the_instance(self):
        self.assertNotIn(self.tmp, self.text)
        self.assertNotIn(os.path.basename(self.tmp), self.text)
        self.assertNotIn(chr(92), self.text)
        people = re.findall(r"\b(?:he|she|his|her|operator|engineer|team|colleague|blame|negligence|mistake)\b", self.text.lower())
        self.assertEqual(people, [])
        self.assertIn("(synthetic)", self.text.splitlines()[0])
        self.assertIn("[TO CONFIRM]", self.text)         # owners and dates of the follow-ups are not invented

    def test_line_endings_are_lf(self):
        self.assertNotIn(b"\r", fsx.read_bytes(self.path("work", "postmortem.md")))


class OtherFailures(U.TempCase):
    def test_a_restart_loop_and_a_blocked_route_in_the_same_report(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "restart", "t": f"2026-09-14T06:{minute}:10Z", "service": "shop", "down_s": 5} for minute in (40, 43, 46)]
        routes = {"routes/shop.yaml": U.route_doc("shop", 8080), "routes/console.yaml": U.route_doc("console", 9000)}
        root = U.routing_instance(self.path("inst"), U.topology_doc(), routes, scope=("routing", "health"),
                                  as_of="2026-09-14T07:00:00Z", trace=events)
        report = lab_run.audit(root, self.path("work"))
        self.assertEqual(sorted(f["class"] for f in report["findings"]), ["admin_on_public", "restart_loop"])
        text = postmortem.render(report)
        self.assertIn("- Verdict: **FAILED** (1 failing finding, 2 in total).", text)
        self.assertIn("service `shop` restarted 3 times inside the probe window", text)
        self.assertIn("- `admin_on_public` [BLOCKED] at `routes/console.yaml`", text)     # evidence lists every finding
        self.assertIn("- Nothing reported as healthy contradicted the findings.", text)

    def test_a_chain_in_the_wrong_order(self):
        policy = U.policy_doc(chain=["backup_a", "export", "backup_b", "verify"])
        U.backup_instance(self.path("inst"), "order", policy=policy)
        report = lab_run.audit(self.path("inst"), self.path("work"))
        self.assertEqual(report["verdict"], "FAILED")
        text = postmortem.render(report)
        self.assertIn("the nightly chain ran the backup before the export (`policy/backup.yaml`, chain)", text)

    def test_a_report_of_an_unreadable_instance_has_no_as_of_and_no_postmortem(self):
        fsx.write_bytes(self.path("inst", "instance.json"), b"{}")
        report = lab_run.audit(self.path("inst"), self.path("work"))
        self.assertEqual([report["verdict"], report["as_of"]], ["FAILED", None])
        self.assertFalse(fsx.exists(self.path("work", "postmortem.md")))
        self.assertIn("[unknown]", lab_run.render(report))

    def test_rendering_never_raises_on_any_failed_report_of_the_seeded_mutations(self):
        rng = random.Random("av2:postmortem")
        U.backup_instance(self.path("base"), "pmx")
        for n in range(12):
            inst = self.path(f"m{n}")
            fsx.copytree(self.path("base"), inst)
            repo = backup.Repo(os.path.join(inst, rng.choice(["repo_a", "repo_b"])))
            pack = fsx.read_bytes(repo.pack_path)
            fsx.write_bytes(repo.pack_path, pack[:rng.randrange(len(pack))])
            report = lab_run.audit(inst, self.path(f"w{n}"))
            if report["verdict"] == "FAILED":
                self.assertTrue(postmortem.render(report).startswith("# Postmortem (synthetic) - test-pmx"))


if __name__ == "__main__":
    unittest.main()
