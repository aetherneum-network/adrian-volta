"""The small tools of the pack: content scanner, manifest, generated orchestrator profile, scorer CLI."""
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

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


if __name__ == "__main__":
    unittest.main()
