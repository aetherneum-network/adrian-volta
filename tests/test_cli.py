"""The command line of the lab: exit codes, the words "RUN OK", and what happens when the input cannot be read."""
import io
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

from lab import backup, fsx, jsonio
from lab import run as lab_run
from tests import _util as U


class ExitCodes(U.TempCase):
    def cli(self, inst: str, name: str = "work") -> tuple[int, str, dict]:
        code, text = U.SC.cli(inst, self.path(name))
        return code, text, jsonio.read(self.path(name, "report.json"))

    def routing(self, name: str, routes: dict) -> str:
        return U.routing_instance(self.path(name), U.topology_doc(), routes)

    def test_ok_is_0_and_says_run_ok(self):
        code, text, report = self.cli(self.routing("ok", {"routes/shop.yaml": U.route_doc("shop", 8080)}))
        self.assertEqual([code, report["verdict"], report["exit_code"]], [0, "OK", 0])
        self.assertEqual(text.strip().splitlines()[-1], "RUN OK - data as of 2026-09-14T06:00:00Z")

    def test_alert_is_1(self):
        U.backup_instance(self.path("alert"), "cli", days=("2026-09-10", "2026-09-11", "2026-09-12"),
                          per_day={"2026-09-11": {"skip": ["backup_b"]}, "2026-09-12": {"skip": ["backup_b"]}})
        code, text, report = self.cli(self.path("alert"))
        self.assertEqual([code, report["verdict"]], [1, "ALERT"])
        self.assertEqual(text.strip().splitlines()[-1], "RUN ALERT - data as of 2026-09-12T06:10:00Z")

    def test_blocked_is_2(self):
        code, text, report = self.cli(self.routing("blocked", {"routes/console.yaml": U.route_doc("console", 9000)}))
        self.assertEqual([code, report["verdict"]], [2, "BLOCKED"])
        self.assertFalse(report["routes"]["accepted"])
        self.assertIsNone(report["routes"]["accepted_sha256"])

    def test_failed_is_3(self):
        U.backup_instance(self.path("failed"), "cli3")
        repo = backup.Repo(self.path("failed", "repo_a"))
        fsx.write_bytes(repo.pack_path, fsx.read_bytes(repo.pack_path)[:100])
        code, text, report = self.cli(self.path("failed"))
        self.assertEqual([code, report["verdict"]], [3, "FAILED"])

    def test_run_ok_is_printed_for_ok_and_for_nothing_else(self):
        instances = {"OK": self.routing("a", {"routes/shop.yaml": U.route_doc("shop", 8080)}),
                     "BLOCKED": self.routing("b", {"routes/console.yaml": U.route_doc("console", 9000)}),
                     "FAILED": self.path("missing-instance")}
        for verdict, inst in instances.items():
            with self.subTest(verdict=verdict):
                code, text, report = self.cli(inst, "w-" + verdict)
                self.assertEqual(report["verdict"], verdict)
                self.assertEqual("RUN OK" in text, verdict == "OK")
                self.assertEqual(code == 0, verdict == "OK")

    def test_the_worst_finding_decides(self):
        routes = {"routes/console.yaml": U.route_doc("console", 9000), "routes/shop.yaml": U.route_doc("shop", 8080)}
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T05:00:00Z", "service": "shop", "map": {"/healthz": 200}}]
        root = U.routing_instance(self.path("both"), U.topology_doc(), routes, scope=("routing", "health"),
                                  as_of="2026-09-14T06:00:00Z", trace=events)
        code, text, report = self.cli(root)
        self.assertEqual(sorted(f["verdict"] for f in report["findings"]), ["BLOCKED", "FAILED"])
        self.assertEqual([code, report["verdict"]], [3, "FAILED"])
        self.assertEqual([f["file"] for f in report["findings"]], ["routes/console.yaml", "topology.yaml"])   # listed by address
        self.assertEqual(text.strip().splitlines()[-1], "RUN FAILED - data as of 2026-09-14T06:00:00Z")

    def test_json_option_writes_the_same_report(self):
        inst = self.routing("json", {"routes/shop.yaml": U.route_doc("shop", 8080)})
        with redirect_stdout(io.StringIO()):
            code = lab_run.main(["--instance", inst, "--work", self.path("w"), "--json", self.path("out", "report.json")])
        self.assertEqual(code, 0)
        self.assertEqual(fsx.read_bytes(self.path("out", "report.json")), fsx.read_bytes(self.path("w", "report.json")))

    def test_as_a_module_the_exit_code_reaches_the_shell(self):
        inst = self.routing("sub", {"routes/console.yaml": U.route_doc("console", 9000)})
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "SystemRoot") if key in os.environ}
        done = subprocess.run([sys.executable, "-m", "lab.run", "--instance", inst, "--work", self.path("w")],
                              cwd=U.ROOT, env=env, capture_output=True, timeout=120)
        out = done.stdout.decode("utf-8")
        self.assertEqual(done.returncode, 2, out + done.stderr.decode("utf-8", "replace"))
        self.assertIn("BLOCKED admin_on_public  routes/console.yaml  console-main  [RL-050]", out)
        self.assertNotIn("RUN OK", out)


class UnreadableInput(U.TempCase):
    def verdict(self, inst: str, name: str) -> list:
        report = lab_run.audit(inst, self.path(name))
        return [report["verdict"], report["exit_code"]] + [[f["class"], f["rule"]] for f in report["findings"]]

    def base(self, name: str) -> str:
        return U.routing_instance(self.path(name), U.topology_doc(), {"routes/shop.yaml": U.route_doc("shop", 8080)})

    def test_instance_file_missing_or_wrong(self):
        self.assertEqual(self.verdict(self.path("nothing-here"), "w0"), ["FAILED", 3, ["instance_unreadable", "RUN-000"]])
        cases = {"not json": b"{", "no as_of": b'{"instance_id": "x"}', "as_of without zone": b'{"instance_id": "x", "as_of": "2026-09-14T06:00:00"}',
                 "as_of with offset": b'{"instance_id": "x", "as_of": "2026-09-14T08:00:00+02:00"}',
                 "unknown scope": b'{"instance_id": "x", "as_of": "2026-09-14T06:00:00Z", "scope": ["everything"]}',
                 "empty scope": b'{"instance_id": "x", "as_of": "2026-09-14T06:00:00Z", "scope": []}', "a list": b"[]"}
        for n, (label, data) in enumerate(cases.items()):
            with self.subTest(case=label):
                inst = self.base(f"i{n}")
                fsx.write_bytes(os.path.join(inst, "instance.json"), data)
                self.assertEqual(self.verdict(inst, f"w{n + 1}"), ["FAILED", 3, ["instance_unreadable", "RUN-000"]])

    def test_topology_missing_is_blocked_and_no_table_is_accepted(self):
        inst = self.base("topo")
        fsx.move(os.path.join(inst, "topology.yaml"), self.path("set-aside.yaml"))
        report = lab_run.audit(inst, self.path("w"))
        self.assertEqual([report["verdict"], [f["class"] for f in report["findings"]]], ["BLOCKED", ["topology_unreadable"]])
        self.assertNotIn("routes", report)

    def test_a_route_file_that_cannot_be_parsed_blocks_the_table(self):
        inst = self.base("route")
        fsx.write_bytes(os.path.join(inst, "routes", "broken.yaml"), b"service: [unclosed\n")
        report = lab_run.audit(inst, self.path("w"))
        self.assertEqual([[f["class"], f["file"], f["rule"]] for f in report["findings"]], [["route_unreadable", "routes/broken.yaml", "RL-000"]])
        self.assertEqual([report["verdict"], report["routes"]["accepted"]], ["BLOCKED", False])

    def test_trace_missing_when_the_scope_needs_it(self):
        inst = U.routing_instance(self.path("trace"), U.topology_doc(), {"routes/shop.yaml": U.route_doc("shop", 8080)},
                                  scope=("routing", "health"))
        self.assertEqual(self.verdict(inst, "w"), ["FAILED", 3, ["trace_unreadable", "RUN-020"]])

    def test_a_work_directory_that_is_reused_keeps_the_previous_registry_copy(self):
        U.backup_instance(self.path("inst"), "reuse")
        lab_run.audit(self.path("inst"), self.path("w"))
        first = fsx.read_bytes(self.path("w", "drills.jsonl"))
        lab_run.audit(self.path("inst"), self.path("w"))
        self.assertEqual(fsx.read_bytes(self.path("w", "drills.jsonl.previous")), first)
        self.assertEqual(fsx.read_bytes(self.path("w", "drills.jsonl")), first)     # same input, same registry: nothing accumulates

    def test_the_report_is_canonical_json_and_names_lab_and_rule_versions(self):
        inst = self.base("canon")
        report = lab_run.audit(inst, self.path("w"))
        data = fsx.read_bytes(self.path("w", "report.json"))
        self.assertEqual(data, jsonio.dumps(report).encode("utf-8"))
        self.assertEqual(report["lab"], "2.0.0")
        self.assertEqual(sorted(report["rules"]), ["alerts", "backup_policy", "health", "route_lint", "severity"])
        self.assertNotIn(self.tmp.replace(chr(92), chr(92) * 2).encode("utf-8"), data)


if __name__ == "__main__":
    unittest.main()
