"""The ten scenarios: structure, results, and the way an external executor runs them.

An executor reads ``scenario.json``, runs the command from the scenario folder with no
argument and no environment variable, and compares the exit code. The checkers are also run
against a deliberately broken lab, to show that they can fail.
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

from lab import findings, fsx, jsonio
from tests import _util as U

run_all = U.load_module("scenarios/run_all.py")
IDS = [f"S{n:02d}" for n in range(1, 11)]
REQUIRED = {"id": str, "title": str, "company": str, "synthetic": bool, "negative": bool, "run": list, "timeout_s": int,
            "expect_exit": int, "description": str, "pass_criterion": str, "lesson": str}


def folder(sid: str) -> str:
    return os.path.join(U.SCENARIOS, sid)


class Structure(unittest.TestCase):
    def test_there_are_exactly_ten_scenarios(self):
        found = sorted(name for name in os.listdir(U.SCENARIOS) if os.path.isdir(folder(name)) and name.startswith("S"))
        self.assertEqual(found, IDS)
        self.assertEqual(run_all.IDS, IDS)

    def test_each_scenario_has_its_five_parts(self):
        for sid in IDS:
            with self.subTest(scenario=sid):
                self.assertTrue(os.path.isfile(os.path.join(folder(sid), "scenario.json")))
                self.assertTrue(os.path.isfile(os.path.join(folder(sid), "check.py")))
                self.assertTrue(os.path.isfile(os.path.join(folder(sid), "run.md")))
                self.assertTrue(fsx.walk_files(os.path.join(folder(sid), "input")))
                self.assertTrue(os.path.isfile(os.path.join(folder(sid), "expected", "expected.json")))

    def test_scenario_json_is_what_an_executor_expects(self):
        for sid in IDS:
            with self.subTest(scenario=sid):
                spec = jsonio.read(os.path.join(folder(sid), "scenario.json"))
                self.assertEqual({key: type(spec.get(key)) for key in REQUIRED}, REQUIRED)
                self.assertEqual([spec["id"], spec["run"], spec["expect_exit"], spec["synthetic"]], [sid, ["python", "check.py"], 0, True])
                self.assertTrue(0 < spec["timeout_s"] <= 300)
                self.assertTrue(os.path.isfile(os.path.join(folder(sid), spec["run"][1])))       # the script is a file of the repository

    def test_run_md_is_one_paragraph_under_a_title(self):
        for sid in IDS:
            with self.subTest(scenario=sid):
                text = fsx.read_bytes(os.path.join(folder(sid), "run.md")).decode("utf-8")
                blocks = [b for b in text.strip().split("\n\n") if b.strip()]
                self.assertEqual(len(blocks), 2)
                self.assertTrue(blocks[0].startswith(f"# {sid} - "))
                self.assertNotIn("\n", blocks[1])
                self.assertIn("python check.py", blocks[1])
                self.assertIn("synthetic", blocks[1])

    def test_at_least_two_scenarios_are_negative_controls(self):
        negative = [sid for sid in IDS if jsonio.read(os.path.join(folder(sid), "scenario.json"))["negative"]]
        self.assertEqual(negative, ["S03", "S10"])

    def test_inputs_are_synthetic_and_small(self):
        companies = set()
        for sid in IDS:
            companies.add(jsonio.read(os.path.join(folder(sid), "scenario.json"))["company"])
            files = fsx.walk_files(os.path.join(folder(sid), "input"))
            self.assertLess(sum(fsx.size(fsx.join(os.path.join(folder(sid), "input"), rel)) for rel in files), 200_000, sid)
            for rel in files:
                if rel.endswith((".yaml", ".yml")):
                    text = fsx.read_bytes(fsx.join(os.path.join(folder(sid), "input"), rel)).decode("utf-8")
                    for line in text.splitlines():
                        if "host:" in line or "domain:" in line:
                            self.assertIn(".example", line, f"{sid}/{rel}")
        self.assertEqual(len(companies), 3)

    def test_expected_files_are_canonical_json(self):
        for sid in IDS:
            path = os.path.join(folder(sid), "expected", "expected.json")
            self.assertEqual(fsx.read_bytes(path), jsonio.dumps(jsonio.read(path)).encode("utf-8"), sid)


class Results(U.TempCase):
    def run_into_scratch(self, ids=IDS) -> list:
        previous = U.SC.BUILD
        U.SC.BUILD = self.path("scenarios")
        try:
            return run_all.run(ids, quiet=True)
        finally:
            U.SC.BUILD = previous

    def test_all_ten_pass(self):
        results = self.run_into_scratch()
        self.assertEqual([(r["scenario"], r["pass"]) for r in results], [(sid, True) for sid in IDS])
        summary = run_all.summary(results)
        self.assertEqual([summary["passed"], summary["total"]], [10, 10])
        self.assertTrue(all(row["checks"] >= 5 and row["checks_failed"] == [] for row in summary["scenarios"]))
        for sid in IDS:
            self.assertEqual(jsonio.read(self.path("scenarios", sid, "result.json"))["pass"], True)

    def test_the_summary_has_no_duration_and_no_path(self):
        text = jsonio.dumps(run_all.summary(self.run_into_scratch(["S01", "S05"])))
        self.assertNotIn(self.tmp.replace(chr(92), "/"), text.replace(chr(92) * 2, "/"))
        self.assertNotIn("seconds", text)
        self.assertNotIn("elapsed", text)

    def test_two_runs_give_the_same_summary(self):
        first = jsonio.dumps(run_all.summary(self.run_into_scratch()))
        self.assertEqual(jsonio.dumps(run_all.summary(self.run_into_scratch())), first)

    def test_the_checkers_fail_when_the_lab_calls_everything_ok(self):
        with mock.patch.object(findings, "overall", return_value="OK"):
            results = {r["scenario"]: r["pass"] for r in self.run_into_scratch()}
        for sid in ("S02", "S03", "S04", "S06", "S07", "S08", "S09", "S10"):
            self.assertFalse(results[sid], sid)

    def test_the_checkers_fail_when_the_lab_refuses_everything(self):
        with mock.patch.object(findings, "overall", return_value="FAILED"):
            results = {r["scenario"]: r["pass"] for r in self.run_into_scratch()}
        for sid in ("S01", "S05"):
            self.assertFalse(results[sid], sid)

    def test_a_checker_that_crashes_is_a_failed_scenario_not_a_skipped_one(self):
        with mock.patch.object(U.SC, "copy_input", side_effect=OSError("input missing")):
            results = self.run_into_scratch(["S01"])
        self.assertEqual([results[0]["pass"], results[0]["scenario"]], [False, "S01"])
        self.assertIn("checker raised OSError", results[0]["summary"])

    def test_a_check_list_without_checks_does_not_pass(self):
        self.assertFalse(U.SC.Checks().ok)
        checks = U.SC.Checks()
        checks.eq("values", 1, 2)
        self.assertEqual([checks.ok, checks.failed(), checks.rows[0]["got"], checks.rows[0]["want"]], [False, ["values"], 1, 2])


class AsAnExecutorRunsThem(unittest.TestCase):
    def test_each_scenario_from_its_own_folder_with_an_empty_environment(self):
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "SystemRoot") if key in os.environ}   # what the OS needs to start a process
        for sid in IDS:
            with self.subTest(scenario=sid):
                spec = jsonio.read(os.path.join(folder(sid), "scenario.json"))
                command = [sys.executable if part == "python" else part for part in spec["run"]]
                done = subprocess.run(command, cwd=folder(sid), env=env, capture_output=True, timeout=spec["timeout_s"])
                out = done.stdout.decode("utf-8", "replace")
                self.assertEqual(done.returncode, spec["expect_exit"], out + done.stderr.decode("utf-8", "replace"))
                self.assertTrue(out.startswith(f"{sid} PASS - "), out)
                self.assertEqual(len(out.strip().splitlines()), 1)

    def test_run_all_as_a_command(self):
        done = subprocess.run([sys.executable, os.path.join(U.SCENARIOS, "run_all.py")], cwd=U.ROOT, capture_output=True, timeout=300)
        lines = done.stdout.decode("utf-8", "replace").strip().splitlines()
        self.assertEqual(done.returncode, 0)
        self.assertEqual(lines[-1], "Scenarios: 10/10 PASS")
        self.assertEqual(len(lines), 11)


if __name__ == "__main__":
    unittest.main()
