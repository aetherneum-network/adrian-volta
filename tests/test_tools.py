"""The small tools of the pack: content scanner, manifest, generated orchestrator profile, scorer CLI."""
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import yaml

from corpus import generate as G
from lab import fsx, jsonio, topology
from tests import _util as U

scan = U.load_module("tools/scan.py")
manifest = U.load_module("tools/manifest.py")
compose_profile = U.load_module("tools/compose_profile.py")
score = U.load_module("eval/score.py")

BS = chr(92)


class Scanner(U.TempCase):
    def rules(self, text: str) -> list:
        return sorted({f["rule"] for f in scan.scan_text("sample.md", text)})

    def test_the_repository_is_clean(self):
        report = scan.scan()
        self.assertEqual(report["findings"], [])
        self.assertEqual(report["result"], "clean")
        self.assertGreater(report["files_scanned"], 150)
        self.assertEqual(report["binary_files_not_scanned"], ["avatar.jpg"])

    def test_the_committed_report_is_the_report_of_this_tree(self):
        path = os.path.join(U.ROOT, "reports", "scan.json")
        if not os.path.isfile(path):
            self.skipTest("reports/scan.json not written yet")
        self.assertEqual(jsonio.read(path), scan.scan())

    def test_it_finds_what_it_claims_to_find(self):
        # the samples are assembled here so that this file does not contain them
        samples = {
            "PATH-WIN": "see C:" + BS + "Users" + BS + "someone" + BS + "notes.txt",
            "PATH-UNIX": "mounted at /" + "opt/service/config",
            "SECRET-KEY": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "SECRET-TOKEN": "token gh" + "p_" + "a" * 36,
            "SECRET-ASSIGN": "pass" + "word = " + "Zx81QwErTy12AsDf",
            "DOMAIN": "served from intranet.some-real-company" + ".com today",
            "IPV4": "host at 8.8" + ".4.4",
        }
        for rule, text in samples.items():
            with self.subTest(rule=rule):
                self.assertEqual(self.rules(text), [rule])

    def test_home_directory_and_forward_slash_drive_paths_are_found_too(self):
        self.assertEqual(self.rules("cd /" + "home/someone/project"), ["PATH-UNIX"])
        self.assertEqual(self.rules("open D:" + "/work/report.docx"), ["PATH-WIN"])
        self.assertEqual(self.rules("mail admin@" + "some-real-company" + ".io"), ["DOMAIN"])
        self.assertEqual(self.rules("fetch https://" + "downloads.some-real-company" + ".net/x"), ["DOMAIN"])

    def test_reserved_names_documentation_addresses_and_ordinary_prose_are_not_findings(self):
        clean = ["host: shop.valdora.example", "mail ops@serrabruna.example", "https://docs.example.com/page", "192.0.2.10 and 203.0.113.7",
                 "http://127.0.0.1:8080/healthz", "version 2026.09.30-2", "ratio 3:4", "path routes/shop.yaml and lab/run.py",
                 "time 06:42:00Z", "and/or", "a/b/c", "sha256:[TO CONFIRM]", "adrian.volta@aetherneum.com", "1.2.3", "10.5"]
        for text in clean:
            with self.subTest(text=text):
                self.assertEqual(self.rules(text), [])

    def test_a_planted_file_is_reported_with_file_and_line(self):
        fsx.write_bytes(self.path("docs", "note.md"), ("line one\nkept in /" + "srv/data/x\n").encode("utf-8"))
        fsx.write_bytes(self.path("build", "ignored.md"), ("/" + "srv/data/x\n").encode("utf-8"))
        fsx.write_bytes(self.path("latin1.txt"), "citt\xe0".encode("latin-1"))
        report = scan.scan(self.tmp)
        self.assertEqual(report["findings"], [{"file": "docs/note.md", "line": 2, "rule": "PATH-UNIX"},
                                              {"file": "latin1.txt", "line": 0, "rule": "NOT-UTF8"}])
        self.assertEqual(report["result"], "findings")

    def test_the_declaration_is_not_signed_by_the_tool(self):
        self.assertEqual(scan.scan(self.tmp)["declaration_signed_by"], "[TO CONFIRM]")


class Manifest(unittest.TestCase):
    def test_parse_and_total(self):
        entries = [("b.txt", "2" * 64), ("a.txt", "1" * 64)]
        text = "# MANIFEST\n# commit abc123\n# files 2\n# total " + jsonio.tree_digest(entries) + "  (note)\n" + \
               "".join(f"{digest}  {rel}\n" for rel, digest in sorted(entries))
        header, parsed = manifest.parse(text)
        self.assertEqual(header, {"commit": "abc123", "files": "2", "total": jsonio.tree_digest(entries)})
        self.assertEqual(parsed, dict(entries))

    def test_the_committed_manifest_matches_the_tree(self):
        if not os.path.isfile(os.path.join(U.ROOT, manifest.NAME)):
            self.skipTest("MANIFEST.sha256 is written by the last commit")
        ok, lines = manifest.check()
        self.assertTrue(ok, "\n".join(lines))

    def test_a_path_with_spaces_survives_the_two_space_separator(self):
        _, parsed = manifest.parse("1" * 64 + "  dir with spaces/file  name.txt\n")
        self.assertEqual(parsed, {"dir with spaces/file  name.txt": "1" * 64})


class ComposeProfile(unittest.TestCase):
    INSTANCE = os.path.join(U.ROOT, "scenarios", "S01", "input", "instance")
    FILE = os.path.join(U.ROOT, "compose", "S01-valdora.compose.yaml")

    def test_the_committed_profile_is_what_the_tool_generates(self):
        topo, problems = topology.load(self.INSTANCE)
        self.assertEqual(problems, [])
        instance_id = jsonio.read(os.path.join(self.INSTANCE, "instance.json"))["instance_id"]
        self.assertEqual(fsx.read_bytes(self.FILE).decode("utf-8"), compose_profile.render(topo, instance_id))

    def test_it_is_valid_yaml_with_placeholder_images_and_nothing_dangerous(self):
        text = fsx.read_bytes(self.FILE).decode("utf-8")
        doc = yaml.safe_load(text)
        topo, _ = topology.load(self.INSTANCE)
        self.assertEqual(sorted(doc["services"]), sorted(topo.services))
        self.assertEqual(sorted(doc["networks"]), sorted(topo.networks))
        for name, svc in doc["services"].items():
            self.assertEqual(svc["image"], f"registry.example/{name}@sha256:[TO CONFIRM]")
            self.assertEqual(sorted(svc), sorted(set(svc) & {"image", "restart", "expose", "networks", "healthcheck"}))
            self.assertNotIn("ports", svc)            # nothing is published on the host
        for word in ("labels", "docker.sock", "privileged", "volumes", "env_file", "network_mode"):
            self.assertNotIn(word, text)
        self.assertIn("never executed", text.splitlines()[0])

    def test_no_code_path_starts_a_container(self):
        for rel in U.python_files("lab", "corpus", "eval", "scenarios", "tools", "tests"):
            source = fsx.read_bytes(os.path.join(U.ROOT, *rel.split("/"))).decode("utf-8")
            for command in ('"docker"', "'docker'", "docker compose", "docker-compose", "podman"):
                if rel == "tests/test_tools.py":
                    continue
                self.assertNotIn(command, source, rel)


class HandWrittenLabels(U.TempCase):
    """The ten instances a different hand writes for the blind run: their labels are checked, never completed."""

    FAULT = {"class": "route_wrong_port", "variant": "hand", "verdict": "BLOCKED", "file": "routes/shop.yaml", "also": [],
             "items": ["shop-main"], "mismatched_files": []}

    def folder(self, labels: list, make=("hand-0001",)) -> str:
        root = self.path("hand")
        for name in make:
            U.routing_instance(os.path.join(root, name), U.topology_doc(), {"routes/shop.yaml": U.route_doc("shop", 8081)})
        jsonio.write_jsonl(os.path.join(root, "labels.jsonl"), labels)
        return root

    def label(self, **changes) -> dict:
        return dict({"instance": "hand-0001", "clean": False, "verdict": "BLOCKED", "faults": [dict(self.FAULT)]}, **changes)

    def test_a_good_label_is_read_and_a_class_the_generator_does_not_plant_is_scored(self):
        root = self.folder([self.label()])
        golds = score.load_handwritten(root, "blind-")
        self.assertEqual([g["instance"] for g in golds], ["hand-0001"])
        self.assertNotIn("route_wrong_port", G.CLASSES)
        metrics = score.score(golds, root, self.path("work"), None)
        self.assertEqual(metrics["classes"]["route_wrong_port"], {"planted": 1, "detected": 1, "localised": 1})
        self.assertEqual([metrics["verdict_correct"], metrics["spurious_findings"], metrics["planted_faults"]], [1, 0, 1])
        # a routing-only instance: the references judge the part that exists and the instance is not counted as fully covered
        self.assertEqual(score.reference_view(os.path.join(root, "hand-0001"))["judged"], ["routing"])
        self.assertEqual([metrics["reference_covered_instances"], metrics["never_event_admin_public"], metrics["never_event_restore"]], [0, 0, 0])

    def test_a_part_the_reference_cannot_read_is_not_vouched_for(self):
        root = self.folder([self.label(verdict="OK", clean=True, faults=[])])
        inst = os.path.join(root, "hand-0001")
        fsx.write_bytes(os.path.join(inst, "instance.json"), b'{"instance_id": "hand-0001", "as_of": "2026-09-14T06:00:00Z", "scope": ["routing", "backup"]}')
        view = score.reference_view(inst)
        self.assertEqual([view["judged"], view["unread"], view["a_differing"]], [["routing"], ["backup"], None])
        fsx.write_bytes(os.path.join(inst, "instance.json"), b"{")
        self.assertEqual(score.reference_view(inst)["judged"], [])
        self.assertIsNone(score.declared_scope(inst))

    def test_a_label_that_is_wrong_about_the_instance_is_counted_against_the_lab_not_repaired(self):
        root = self.folder([self.label(faults=[dict(self.FAULT, items=["another-router"])])])
        metrics = score.score(score.load_handwritten(root, "blind-"), root, self.path("work"), None)
        self.assertEqual(metrics["classes"]["route_wrong_port"], {"planted": 1, "detected": 1, "localised": 0})
        self.assertEqual(metrics["spurious_findings"], 1)

    def test_a_restore_called_ok_that_no_reference_vouches_for_counts_as_a_never_event(self):
        U.backup_instance(self.path("corpus", "one"), "unvouched")
        gold = {"instance": "one", "clean": True, "verdict": "OK", "faults": []}
        control = score.score([gold], self.path("corpus"), self.path("work-control"), None)
        self.assertEqual([control["never_event_restore"], control["verdict_correct"]], [0, 1])
        with mock.patch.object(score.reference_hashes, "check", side_effect=ValueError("cannot be read")):
            metrics = score.score([gold], self.path("corpus"), self.path("work"), None)
        self.assertEqual(metrics["never_event_restore"], 1)
        self.assertEqual(metrics["reference_covered_instances"], 0)

    def test_labels_that_cannot_be_used_are_refused(self):
        fault = dict(self.FAULT)
        cases = {
            "is needed": [{"instance": "hand-0001", "clean": True}],
            "an object with the keys": [["hand-0001"]],
            "sub-folder": [self.label(instance="a/b")],
            "repeated": [self.label(), self.label()],
            "starts with": [self.label(instance="blind-0001")],
            "not found": [self.label(instance="hand-0002")],
            "must be one of": [self.label(verdict="GREEN")],
            "a clean instance has no fault": [self.label(clean=True)],
            "any other has at least one fault": [self.label(faults=[])],
            "verdict OK": [self.label(clean=True, faults=[], verdict="ALERT")],
            "every fault needs": [self.label(faults=[{"class": "route_wrong_port"}])],
            "'items' a list or null": [self.label(faults=[dict(fault, items="shop-main")])],
        }
        for n, (expected, labels) in enumerate(cases.items()):
            with self.subTest(case=expected):
                root = self.folder(labels, make=("hand-0001", "blind-0001"))
                with self.assertRaises(ValueError) as caught:
                    score.load_handwritten(root, "blind-")
                self.assertIn(expected, str(caught.exception))

    def test_a_missing_or_broken_labels_file_is_refused(self):
        with self.assertRaises(ValueError):
            score.load_handwritten(self.path("nothing"), "blind-")
        root = self.folder([self.label()])
        fsx.write_bytes(os.path.join(root, "labels.jsonl"), b'{"instance": "hand-0001", ')
        with self.assertRaises(ValueError):
            score.load_handwritten(root, "blind-")


class ScorerCommandLine(unittest.TestCase):
    """Argument checks only: no suite is generated or scored here (and never a blind one)."""

    def fails(self, *argv) -> str:
        err = io.StringIO()
        with self.assertRaises(SystemExit) as caught, redirect_stderr(err), redirect_stdout(io.StringIO()):
            score.main(list(argv))
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def test_a_blind_run_needs_seed_runner_and_date(self):
        self.assertIn("needs --seed, --runner and --date", self.fails("--suite", "blind"))
        self.assertIn("needs --seed, --runner and --date", self.fails("--suite", "blind", "--seed", "5"))

    def test_a_blind_run_refuses_the_seed_of_a_named_suite(self):
        for name in ("dev", "holdout", "stress"):
            message = self.fails("--suite", "blind", "--seed", str(G.SUITES[name]["seed"]), "--runner", "x", "--date", "2026-10-01")
            self.assertIn("already been used", message)

    def test_a_blind_run_refuses_an_unknown_style(self):
        message = self.fails("--suite", "blind", "--seed", "5", "--runner", "x", "--date", "2026-10-01", "--styles", "bom,handwriting")
        self.assertIn("unknown style", message)

    def test_a_blind_run_refuses_a_date_that_is_not_a_date(self):
        message = self.fails("--suite", "blind", "--seed", "5", "--runner", "x", "--date", "tomorrow")
        self.assertIn("YYYY-MM-DD", message)

    def test_hand_written_instances_go_with_a_blind_run_only(self):
        self.assertIn("goes with --suite blind", self.fails("--suite", "dev", "--handwritten", "somewhere"))

    def test_a_blind_run_with_unreadable_hand_written_labels_stops_before_anything_is_generated(self):
        message = self.fails("--suite", "blind", "--seed", "5", "--runner", "x", "--date", "2026-10-01",
                             "--handwritten", os.path.join(U.ROOT, "docs"))
        self.assertIn("hand-written instances: labels.jsonl not found", message)
        self.assertFalse(os.path.exists(os.path.join(U.ROOT, "build", "eval", "blind")))

    def test_the_history_holds_the_development_runs_and_no_blind_run_by_the_author(self):
        path = os.path.join(U.ROOT, "eval", "history.json")
        if not os.path.isfile(path):
            self.skipTest("eval/history.json not written yet")
        history = jsonio.read(path)
        suites = [run["suite"] for run in history["runs"]]
        self.assertIn("dev", suites)
        self.assertIn("stress", suites)
        self.assertIn("holdout", suites)
        for run in history["runs"]:
            self.assertTrue({"suite", "seed", "when", "metrics"} <= set(run), run.get("suite"))
            if run["suite"] == "blind":
                self.assertNotIn("synthetic alumnus", run["runner"])

    def test_the_seed_of_the_recorded_blind_run_cannot_be_used_again(self):
        history = jsonio.read(os.path.join(U.ROOT, "eval", "history.json"))
        for seed in sorted({run["seed"] for run in history["runs"] if run["suite"] == "blind"}):
            message = self.fails("--suite", "blind", "--seed", str(seed), "--runner", "x", "--date", "2026-10-01")
            self.assertIn("already been used", message)


class BlindRunOncePerCommit(U.TempCase):
    """The gate of the blind run: once per measured commit. Argument checks only - nothing is generated."""

    ARGS = ("--suite", "blind", "--seed", "5", "--runner", "x", "--date", "2026-10-01", "--styles", "bom,handwriting")

    def fails_with(self, recorded_commit: str, current_commit: str) -> str:
        path = self.path("history.json")
        jsonio.write(path, {"runs": [{"suite": "blind", "seed": 77, "commit": recorded_commit, "when": "2026-09-30",
                                      "runner": "someone else", "metrics": {}}]})
        err = io.StringIO()
        with mock.patch.object(score, "HISTORY", path), mock.patch.object(score, "_commit", return_value=current_commit), \
                self.assertRaises(SystemExit) as caught, redirect_stderr(err), redirect_stdout(io.StringIO()):
            score.main(list(self.ARGS))
        self.assertEqual(caught.exception.code, 2)
        return err.getvalue()

    def test_a_second_blind_run_on_the_same_commit_is_refused(self):
        self.assertIn("already recorded", self.fails_with("abc123", "abc123"))

    def test_a_new_frozen_commit_passes_the_gate_and_stops_at_the_next_check(self):
        message = self.fails_with("abc123", "def456")
        self.assertNotIn("already recorded", message)
        self.assertIn("unknown style", message)

    def test_when_the_commit_cannot_be_read_any_recorded_blind_run_refuses(self):
        self.assertIn("already recorded", self.fails_with("abc123", "[TO CONFIRM]"))


if __name__ == "__main__":
    unittest.main()
