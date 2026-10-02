"""Virtual-clock world and the end-to-end probe (facts by the code, decisions by rules/health.json)."""
import datetime as dt
import os
import unittest

from lab import jsonio, probe, routes, sim, timeutil, topology
from lab import run as lab_run
from tests import _util as U

AS_OF = "2026-09-14T07:00:00Z"


def at(text: str) -> dt.datetime:
    return timeutil.parse_utc(text)


class World(U.TempCase):
    def world(self, events):
        jsonio.write_jsonl(os.path.join(self.tmp, "trace.jsonl"), events)
        trace, problems = sim.load_trace(self.tmp)
        self.assertEqual(problems, [])
        return sim.World(trace["sim"])

    def test_no_statement_means_not_up(self):
        world = self.world([{"kind": "proc", "t": "2026-09-14T06:00:00Z", "service": "shop", "state": "up"}])
        self.assertFalse(world.proc_up("shop", at("2026-09-14T05:59:59Z")))
        self.assertTrue(world.proc_up("shop", at("2026-09-14T06:00:00Z")))
        self.assertFalse(world.proc_up("unknown", at("2026-09-14T06:30:00Z")))

    def test_status_codes(self):
        world = self.world(U.up_events(["shop"], serve={"shop": {"/": 200, "/api/": 500}})
                           + [{"kind": "restart", "t": "2026-09-14T06:10:00Z", "service": "shop", "down_s": 30}])
        t = at("2026-09-14T06:00:00Z")
        self.assertEqual(world.status("shop", "/", t), 200)
        self.assertEqual(world.status("shop", "/api", t), 500)          # serve-map keys are normalised like prefixes
        self.assertEqual(world.status("shop", "/nowhere", t), 404)
        self.assertEqual(world.status(None, "/", t), 502)
        self.assertEqual(world.status("ghost", "/", t), 502)
        self.assertEqual(world.status("shop", "/", at("2026-09-14T06:10:29Z")), 503)
        self.assertEqual(world.status("shop", "/", at("2026-09-14T06:10:30Z")), 200)

    def test_trace_lines_that_cannot_be_read_are_problems(self):
        lines = ['{"kind":"proc","t":"2026-09-14 06:00:00","service":"shop","state":"up"}',     # no zone
                 '{"kind":"restart","t":"2026-09-14T06:00:00Z","service":"shop"}',               # no down_s
                 '{"kind":"serve","t":"2026-09-14T06:00:00Z","service":"shop","map":{"/":"ok"}}',
                 '{"kind":"teleport","t":"2026-09-14T06:00:00Z"}', "not json", "[1]"]
        with open(os.path.join(self.tmp, "trace.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
        trace, problems = sim.load_trace(self.tmp)
        self.assertEqual(len(problems), 6)
        self.assertEqual(trace["sim"], [])

    def test_job_timestamps_are_classified_not_rejected(self):
        jsonio.write_jsonl(os.path.join(self.tmp, "trace.jsonl"), [
            {"kind": "job", "run": "2026-09-14", "step": "export", "start": "2026-09-14T02:30:00Z",
             "end": "2026-09-14 04:31:00", "status": "ok"},
            {"kind": "job", "run": "2026-09-14", "step": "backup_a", "start": "2026-09-14T04:31:00+02:00",
             "end": "soon", "status": "ok"}])
        trace, problems = sim.load_trace(self.tmp)
        self.assertEqual(problems, [])
        self.assertEqual([(j["_start_kind"], j["_end_kind"]) for j in trace["jobs"]], [("utc", "naive"), ("offset", "invalid")])
        self.assertEqual(timeutil.fmt(trace["jobs"][1]["_start"]), "2026-09-14T02:31:00Z")
        self.assertIsNone(trace["jobs"][0]["_end"])


class Probe(U.TempCase):
    def evaluate(self, events, health=None, routes_=None, public=("shop",)):
        root = U.routing_instance(self.tmp, U.topology_doc(public=public, health=health),
                                  routes_ or {"routes/shop.yaml": U.route_doc("shop", 8080)},
                                  scope=("routing", "health"), as_of=AS_OF, trace=events)
        topo, _ = topology.load(root)
        loaded, _, _ = routes.load(root)
        trace, problems = sim.load_trace(root)
        self.assertEqual(problems, [])
        return probe.evaluate(topo, routes.table(loaded), sim.World(trace["sim"]), at(AS_OF))

    def found(self, result):
        return [[f["class"], f["file"], f["item"], f["rule"]] for f in result["findings"]]

    def test_thirty_beats_ending_at_as_of(self):
        result = self.evaluate(U.up_events(["shop", "console"]))
        self.assertEqual(result["beats"], 30)
        self.assertEqual(result["window"], ["2026-09-14T06:31:00Z", AS_OF])
        self.assertEqual(result["findings"], [])

    def test_process_health_says_healthy_while_the_route_answers_404(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:41:30Z", "service": "shop", "map": {"/healthz": 200}}]
        result = self.evaluate(events, health={"shop": {"kind": "process"}})
        self.assertEqual(self.found(result), [["health_probes_process", "topology.yaml", "shop", "H-020"]])
        detail = result["findings"][0]["detail"]
        self.assertEqual([detail["first_failing_beat"], detail["declared_at"], detail["beats_to_declare"]],
                         ["2026-09-14T06:42:00Z", "2026-09-14T06:43:00Z", 2])

    def test_http_health_on_another_path_is_the_same_blindness(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:50:00Z", "service": "shop", "map": {"/": 500, "/healthz": 200}}]
        self.assertEqual(self.found(self.evaluate(events))[0][:3], ["health_probes_process", "topology.yaml", "shop"])

    def test_when_the_health_check_also_fails_it_is_a_service_down_not_a_blind_check(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:50:00Z", "service": "shop", "map": {"/": 500, "/healthz": 500}}]
        self.assertEqual(self.found(self.evaluate(events)), [["service_down", "trace.jsonl", "shop", "H-030"]])

    def test_one_failing_beat_is_not_declared(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:49:30Z", "service": "shop", "map": {"/healthz": 200}},
            {"kind": "serve", "t": "2026-09-14T06:50:30Z", "service": "shop", "map": {"/": 200, "/healthz": 200}}]
        result = self.evaluate(events, health={"shop": {"kind": "process"}})
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["services"]["shop"]["probe_consecutive_failures"], 1)

    def test_two_failing_beats_are_declared(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:49:30Z", "service": "shop", "map": {"/healthz": 200}},
            {"kind": "serve", "t": "2026-09-14T06:51:30Z", "service": "shop", "map": {"/": 200, "/healthz": 200}}]
        result = self.evaluate(events, health={"shop": {"kind": "process"}})
        self.assertEqual(self.found(result)[0][0], "health_probes_process")
        self.assertEqual(result["findings"][0]["detail"]["failing_beats"], 2)

    def test_a_restart_inside_a_silent_failure_does_not_hide_it(self):
        # regression of the stress suite (rules/health.json 2026.09.30-2): during the restart the
        # process check says unhealthy for one beat; the silent run before and after it still counts.
        events = U.up_events(["shop", "console"]) + [
            {"kind": "serve", "t": "2026-09-14T06:40:30Z", "service": "shop", "map": {"/healthz": 200}},
            {"kind": "restart", "t": "2026-09-14T06:49:50Z", "service": "shop", "down_s": 20}]
        result = self.evaluate(events, health={"shop": {"kind": "process"}})
        self.assertEqual(self.found(result), [["health_probes_process", "topology.yaml", "shop", "H-020"]])
        facts = result["findings"][0]["detail"]["facts"]
        self.assertEqual(facts["probe_consecutive_failures"], 20)
        self.assertEqual(facts["silent_consecutive_failures"], 10)

    def test_restart_loop(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "restart", "t": f"2026-09-14T06:{minute}:10Z", "service": "shop", "down_s": 5} for minute in (40, 43, 46, 49)]
        result = self.evaluate(events)
        self.assertEqual(self.found(result), [["restart_loop", "trace.jsonl", "shop", "H-010"]])
        self.assertEqual(len(result["findings"][0]["detail"]["restarts"]), 4)

    def test_two_restarts_are_not_a_loop_and_old_restarts_do_not_count(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "restart", "t": f"2026-09-14T0{hour}:{minute}:10Z", "service": "shop", "down_s": 5}
            for hour, minute in ((6, 40), (6, 45), (5, 10), (5, 12), (5, 14))]
        self.assertEqual(self.evaluate(events)["findings"], [])

    def test_three_restarts_spread_over_more_than_ten_minutes_are_not_a_loop(self):
        events = U.up_events(["shop", "console"]) + [
            {"kind": "restart", "t": f"2026-09-14T06:{minute}:10Z", "service": "shop", "down_s": 5} for minute in (32, 44, 56)]
        self.assertEqual(self.evaluate(events)["findings"], [])

    def test_a_service_without_routes_is_not_probed_and_not_reported(self):
        result = self.evaluate(U.up_events(["shop"]))       # console has no route and no process statement
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["services"]["console"]["routes_probed"], 0)


class HealthInTheAudit(U.TempCase):
    def test_health_is_not_asserted_from_a_trace_that_is_partly_unknown(self):
        events = U.up_events(["shop", "console"]) + [{"kind": "restart", "t": "yesterday", "service": "shop", "down_s": 5}]
        root = U.routing_instance(self.tmp, U.topology_doc(), {"routes/shop.yaml": U.route_doc("shop", 8080)},
                                  scope=("routing", "health"), as_of=AS_OF, trace=events)
        report = lab_run.audit(root, self.path("work"))
        self.assertEqual([[f["class"], f["file"]] for f in report["findings"]], [["trace_unreadable", "trace.jsonl"]])
        self.assertEqual(report["verdict"], "FAILED")
        self.assertNotIn("health", report)


if __name__ == "__main__":
    unittest.main()
