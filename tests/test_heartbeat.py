"""Nightly windows, catch-up, time zones and the freshness of the secondary repository.

Everything is anchored to UTC. A schedule in local time or a timestamp without a zone is
reported and the window evaluation abstains: nothing is converted by guesswork.
"""
import datetime as dt
import os
import unittest

from lab import backup, fsx, heartbeat, jsonio, sim, timeutil
from lab import run as lab_run
from tests import _util as U

AS_OF = timeutil.parse_utc("2026-09-14T06:10:00Z")


class Windows(U.TempCase):
    def jobs(self, starts: dict, step: str = "export") -> list:
        """``{run id: start text}`` -> job events as the trace loader returns them."""
        rows = [{"kind": "job", "run": run, "step": step, "start": start, "end": start, "status": "ok"}
                for run, start in starts.items()]
        jsonio.write_jsonl(self.path("trace.jsonl"), rows)
        trace, problems = sim.load_trace(self.tmp)
        self.assertEqual(problems, [])
        return trace["jobs"]

    def policy(self, **kw) -> dict:
        parsed, problems = backup.parse_policy(U.policy_doc(**kw))
        self.assertEqual(problems, [])
        return parsed

    def evaluate(self, starts: dict, as_of=AS_OF, **kw) -> dict:
        return heartbeat.evaluate(self.policy(**kw), self.jobs(starts), as_of)

    def found(self, result: dict) -> list:
        return [[f["class"], f["file"], f["item"], f["rule"]] for f in result["findings"]]

    def test_a_window_is_due_only_when_its_grace_period_has_fully_elapsed(self):
        first = dt.date(2026, 9, 12)
        early = heartbeat.due_windows((2, 30), first, timeutil.parse_utc("2026-09-14T03:59:59Z"), 5400)
        exact = heartbeat.due_windows((2, 30), first, timeutil.parse_utc("2026-09-14T04:00:00Z"), 5400)
        self.assertEqual([timeutil.fmt(w) for w in early], ["2026-09-12T02:30:00Z", "2026-09-13T02:30:00Z"])
        self.assertEqual(timeutil.fmt(exact[-1]), "2026-09-14T02:30:00Z")

    def test_every_window_covered(self):
        result = self.evaluate({f"2026-09-{d}": f"2026-09-{d}T02:30:05Z" for d in (12, 13, 14)})
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["windows"]["due"], result["windows"]["covered"])
        self.assertEqual(len(result["windows"]["due"]), 3)
        self.assertIsNone(result["catch_up"])

    def test_a_late_run_inside_the_grace_period_covers_its_window(self):
        late = {"2026-09-12": "2026-09-12T02:30:05Z", "2026-09-13": "2026-09-13T04:00:00Z", "2026-09-14": "2026-09-14T02:30:05Z"}
        self.assertEqual(self.evaluate(late)["findings"], [])
        late["2026-09-13"] = "2026-09-13T04:00:01Z"       # one second after the grace period
        self.assertEqual(self.found(self.evaluate(late)), [["window_missed", "trace.jsonl", "2026-09-13", "A-030"]])

    def test_one_missed_window_gives_one_declared_catch_up(self):
        result = self.evaluate({"2026-09-12": "2026-09-12T02:30:05Z", "2026-09-14": "2026-09-14T02:30:05Z"})
        self.assertEqual(self.found(result), [["window_missed", "trace.jsonl", "2026-09-13", "A-030"]])
        self.assertEqual(result["windows"]["missed"], ["2026-09-13T02:30:00Z"])
        self.assertEqual(result["catch_up"], dict(result["catch_up"], declared=True, runs=1, as_of="2026-09-14T06:10:00Z",
                                                  recover_window="2026-09-13T02:30:00Z", recorded_as_missed=[]))

    def test_several_missed_windows_are_not_back_filled_one_run_recovers_the_latest(self):
        result = self.evaluate({"2026-09-12": "2026-09-12T02:30:05Z"})
        self.assertEqual(result["windows"]["missed"], ["2026-09-13T02:30:00Z", "2026-09-14T02:30:00Z"])
        self.assertEqual(len(result["findings"]), 1)                 # one finding for the episode, not one per night
        self.assertEqual([result["catch_up"]["runs"], result["catch_up"]["recover_window"], result["catch_up"]["recorded_as_missed"]],
                         [1, "2026-09-14T02:30:00Z", ["2026-09-13T02:30:00Z"]])
        self.assertEqual(result["findings"][0]["detail"]["as_of"], "2026-09-14T06:10:00Z")

    def test_the_window_of_today_is_not_missed_before_its_grace_period_ends(self):
        result = self.evaluate({"2026-09-12": "2026-09-12T02:30:05Z", "2026-09-13": "2026-09-13T02:30:05Z"},
                               as_of=timeutil.parse_utc("2026-09-14T03:00:00Z"))
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["windows"]["due"]), 2)

    def test_windows_before_the_first_run_on_record_are_not_invented(self):
        result = self.evaluate({"2026-09-14": "2026-09-14T02:30:05Z"})
        self.assertEqual(result["windows"]["due"], ["2026-09-14T02:30:00Z"])
        self.assertEqual(result["findings"], [])

    def test_only_the_lookback_period_is_evaluated_and_this_limit_is_a_rule_parameter(self):
        starts = {f"2026-09-{d:02d}": f"2026-09-{d:02d}T02:30:05Z" for d in (8, 10, 11, 12, 13, 14)}    # the 9th is missing
        result = self.evaluate(starts)
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["windows"]["due"][0], "2026-09-12T02:30:00Z")

    def test_no_run_at_all_is_three_missed_windows_not_silence(self):
        result = self.evaluate({})
        self.assertEqual(len(result["windows"]["missed"]), 3)
        self.assertEqual(self.found(result)[0][0], "window_missed")

    def test_a_schedule_in_local_time_is_reported_and_windows_are_not_evaluated(self):
        result = self.evaluate({"2026-09-12": "2026-09-12T02:30:05Z"}, tz="Europe/Rome")    # two windows would be missed
        self.assertEqual(self.found(result), [["tz_mixed", "policy/backup.yaml", "schedule", "A-011"]])
        self.assertEqual(result["windows"], {"evaluable": False, "due": [], "covered": [], "missed": []})
        self.assertIsNone(result["catch_up"])

    def test_names_of_utc_are_accepted_and_nothing_else(self):
        runs = {f"2026-09-{d}": f"2026-09-{d}T02:30:05Z" for d in (12, 13, 14)}
        for tz in ("UTC", "Etc/UTC", "Z"):
            self.assertEqual(self.evaluate(runs, tz=tz)["findings"], [], tz)
        for tz in ("utc", "GMT", "Europe/London", "CET", "+00:00", ""):
            self.assertEqual(self.found(self.evaluate(runs, tz=tz))[0][3], "A-011", tz)

    def test_a_schedule_time_that_is_not_hh_mm_is_unreadable_not_guessed(self):
        runs = {f"2026-09-{d}": f"2026-09-{d}T02:30:05Z" for d in (12, 13, 14)}
        for at in (750, "25:00", "2:30", "02:30:00", None):
            with self.subTest(at=at):
                policy = self.policy()
                policy["schedule"]["at"] = at
                result = heartbeat.evaluate(policy, self.jobs(runs), AS_OF)
                self.assertEqual(self.found(result), [["policy_unreadable", "policy/backup.yaml", "schedule", "A-010"]])
                self.assertFalse(result["windows"]["evaluable"])

    def test_a_timestamp_without_a_zone_is_reported_with_its_line_and_windows_abstain(self):
        result = self.evaluate({"2026-09-12": "2026-09-12T02:30:05Z", "2026-09-13": "2026-09-13 04:30:05"})
        self.assertEqual(self.found(result), [["tz_mixed", "trace.jsonl", "job-timestamps", "A-021"]])
        entries = result["findings"][0]["detail"]["entries"]
        self.assertEqual([[e["line"], e["field"], e["kind"], e["value"]] for e in entries],
                         [[2, "start", "naive", "2026-09-13 04:30:05"], [2, "end", "naive", "2026-09-13 04:30:05"]])
        self.assertFalse(result["windows"]["evaluable"])

    def test_a_timestamp_with_an_explicit_offset_is_reported_and_converted(self):
        runs = {"2026-09-12": "2026-09-12T02:30:05Z", "2026-09-13": "2026-09-13T04:30:05+02:00", "2026-09-14": "2026-09-14T02:30:05Z"}
        result = self.evaluate(runs)
        self.assertEqual(self.found(result), [["tz_mixed", "trace.jsonl", "job-timestamps", "A-022"]])
        self.assertTrue(result["windows"]["evaluable"])
        self.assertEqual(result["windows"]["missed"], [])         # 04:30:05+02:00 is 02:30:05Z: the window is covered

    def test_a_timestamp_that_is_not_a_timestamp_is_unreadable_evidence(self):
        result = self.evaluate({"2026-09-12": "2026-09-12T02:30:05Z", "2026-09-13": "soon"})
        self.assertEqual(self.found(result), [["trace_unreadable", "trace.jsonl", "job-timestamps", "A-020"]])
        self.assertFalse(result["windows"]["evaluable"])


class Secondary(U.TempCase):
    DAYS = ("2026-09-10", "2026-09-11", "2026-09-12")

    def build(self, per_day=None, max_lag=None) -> dict:
        U.backup_instance(self.path("inst"), "hb", days=self.DAYS, policy=U.policy_doc(max_lag=max_lag), per_day=per_day)
        return lab_run.audit(self.path("inst"), self.path("work"))

    def found(self, report: dict) -> list:
        return [[f["class"], f["file"], f["item"], f["rule"]] for f in report["findings"]]

    def test_both_repositories_fresh(self):
        report = self.build()
        self.assertEqual([report["verdict"], report["findings"]], ["OK", []])
        self.assertEqual(report["secondary"]["facts"], {"secondary_readable": True, "secondary_has_snapshots": True, "lag_exceeds_max": False})
        self.assertLess(abs(report["secondary"]["lag_hours"]), 0.02)

    def test_secondary_two_nights_behind_is_an_alert_that_carries_as_of(self):
        report = self.build(per_day={day: {"skip": ["backup_b"]} for day in self.DAYS[1:]})
        self.assertEqual(self.found(report), [["repo_b_stale", "repo_b", "20260910T023145Z", "A-041"]])
        self.assertEqual([report["verdict"], report["exit_code"]], ["ALERT", 1])
        detail = report["findings"][0]["detail"]
        self.assertEqual([detail["as_of"], detail["max_lag_hours"], detail["lag_hours"]], ["2026-09-12T06:10:00Z", 26, 47.99])
        self.assertEqual(report["drill"]["result"], "ok")               # the primary restore held
        self.assertNotIn("RUN OK", lab_run.render(report))

    def test_the_status_a_job_printed_is_not_evidence(self):
        report = self.build(per_day={day: {"claimed_not_done": ["backup_b"]} for day in self.DAYS[1:]})
        self.assertEqual(self.found(report), [["repo_b_stale", "repo_b", "20260910T023145Z", "A-041"]])
        self.assertEqual(report["findings"][0]["detail"]["job_log_claims"], ["ok"])

    def test_the_allowed_lag_is_read_from_the_policy(self):
        one_night = {self.DAYS[-1]: {"skip": ["backup_b"]}}
        self.assertEqual(self.build(per_day=one_night, max_lag=30)["findings"], [])        # 24 h behind, 30 allowed
        self.tmp = self.path("second")
        self.assertEqual(self.found(self.build(per_day=one_night, max_lag=12))[0][0], "repo_b_stale")

    def test_a_secondary_without_any_snapshot_is_not_a_second_copy(self):
        report = self.build(per_day={day: {"skip": ["backup_b"]} for day in self.DAYS})
        self.assertEqual(self.found(report), [["repo_b_stale", "repo_b", None, "A-040"]])

    def test_a_secondary_that_cannot_be_read_is_reported_not_assumed_fresh(self):
        U.backup_instance(self.path("inst"), "hb", days=self.DAYS)
        repo = backup.Repo(self.path("inst", "repo_b"))
        fsx.write_bytes(repo.snapshot_path(repo.snapshot_ids()[-1]), b"{ not json")
        report = lab_run.audit(self.path("inst"), self.path("work"))
        self.assertEqual(self.found(report), [["repo_b_unreadable", "repo_b", "20260912T023145Z", "A-035"]])
        self.assertEqual([report["verdict"], report["exit_code"]], ["ALERT", 1])
        self.assertNotIn("RUN OK", lab_run.render(report))
        self.assertNotIn("lag_hours", report["secondary"])


class PolicyFile(U.TempCase):
    def test_an_unquoted_time_read_as_a_number_blocks_the_run(self):
        U.backup_instance(self.path("inst"), "at")
        path = self.path("inst", "policy", "backup.yaml")
        text = fsx.read_bytes(path).decode("utf-8")
        self.assertIn('at: "02:30"', text)
        fsx.write_bytes(path, text.replace('at: "02:30"', "at: 12:30").encode("utf-8"))      # YAML 1.1 reads this as 750
        report = lab_run.audit(self.path("inst"), self.path("work"))
        self.assertEqual([[f["class"], f["rule"]] for f in report["findings"]], [["policy_unreadable", "A-010"]])
        self.assertEqual([report["verdict"], report["exit_code"]], ["BLOCKED", 2])

    def test_a_policy_that_cannot_be_parsed_is_blocked_and_nothing_else_is_asserted(self):
        U.backup_instance(self.path("inst"), "bad")
        fsx.write_bytes(self.path("inst", "policy", "backup.yaml"), b"schedule: [unclosed\n")
        report = lab_run.audit(self.path("inst"), self.path("work"))
        self.assertEqual([[f["class"], f["rule"]] for f in report["findings"]], [["policy_unreadable", "RUN-030"]])
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertNotIn("drill", report)


if __name__ == "__main__":
    unittest.main()
