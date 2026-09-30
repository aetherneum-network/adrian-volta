"""S09 - host off for two nights across the daylight-saving change: windows in UTC, one declared catch-up, no guess."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from lab import jsonio  # noqa: E402

SID = "S09"


def _all_utc(value) -> bool:
    """Every timestamp-looking string inside a JSON value ends with Z."""
    if isinstance(value, dict):
        return all(_all_utc(v) for v in value.values())
    if isinstance(value, list):
        return all(_all_utc(v) for v in value)
    if isinstance(value, str) and len(value) >= 19 and value[:4].isdigit() and value[4] == "-" and "T" in value:
        return value.endswith("Z")
    return True


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    checks = C.Checks()

    # 1. schedule anchored to UTC
    inst = os.path.join(work, "instance")
    C.build_backup_instance(scn, inst, events="events.jsonl")
    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    beat = report["heartbeat"]
    catch_up = beat["catch_up"] or {}
    checks.eq("utc: verdict ALERT", report["verdict"], "ALERT")
    checks.eq("utc: exit code 1", code, 1)
    checks.true("utc: console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("utc: one finding, on the trace and the earliest missed night",
              [[f["class"], f["file"], f["item"]] for f in found], [["window_missed", "trace.jsonl", "2026-10-24"]])
    checks.eq("utc: windows due", beat["windows"]["due"],
              ["2026-10-23T00:30:00Z", "2026-10-24T00:30:00Z", "2026-10-25T00:30:00Z"])
    checks.eq("utc: window covered", beat["windows"]["covered"], ["2026-10-23T00:30:00Z"])
    checks.eq("utc: windows missed", beat["windows"]["missed"], ["2026-10-24T00:30:00Z", "2026-10-25T00:30:00Z"])
    checks.true("utc: the catch-up is declared", catch_up.get("declared") is True)
    checks.eq("utc: one run, not two", catch_up.get("runs"), 1)
    checks.eq("utc: the run recovers the latest window", catch_up.get("recover_window"), "2026-10-25T00:30:00Z")
    checks.eq("utc: the earlier window stays recorded as missed", catch_up.get("recorded_as_missed"), ["2026-10-24T00:30:00Z"])
    checks.true("utc: console declares the catch-up", "catch-up declared: one run recovers window 2026-10-25T00:30:00Z" in text)
    checks.true("utc: every timestamp in the report is explicit UTC", _all_utc(report))
    checks.eq("utc: the last snapshot still restores and verifies", report["drill"]["result"], "ok")

    # 2. the same nights with the schedule written in local time
    local = os.path.join(work, "instance-local")
    C.build_backup_instance(scn, local, variant="instance-local", events="events.jsonl")
    code2, text2 = C.cli(local, os.path.join(work, "audit-local"))
    report2 = jsonio.read(os.path.join(work, "audit-local", "report.json"))
    found2 = C.brief(report2)
    beat2 = report2["heartbeat"]
    checks.eq("local: verdict ALERT", report2["verdict"], "ALERT")
    checks.eq("local: exit code 1", code2, 1)
    checks.true("local: console does not say RUN OK", not C.console_says_ok(text2))
    checks.eq("local: one finding, on the schedule written in the policy",
              [[f["class"], f["file"], f["item"]] for f in found2], [["tz_mixed", "policy/backup.yaml", "schedule"]])
    checks.true("local: windows are not evaluated", beat2["windows"]["evaluable"] is False)
    checks.eq("local: no window is guessed", [beat2["windows"]["due"], beat2["windows"]["missed"]], [[], []])
    checks.true("local: no catch-up is invented", beat2["catch_up"] is None)

    actual = {"scenario": SID,
              "utc": {"verdict": report["verdict"], "exit_code": code, "findings": found, "windows": beat["windows"],
                      "catch_up": {k: catch_up.get(k) for k in ("declared", "as_of", "recover_window", "recorded_as_missed", "runs")}},
              "local": {"verdict": report2["verdict"], "exit_code": code2, "findings": found2, "windows": beat2["windows"],
                        "catch_up": beat2["catch_up"]}}
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "2 windows missed across the DST change: 1 declared catch-up run, all times UTC; local-time schedule: abstains",
                    actual)


if __name__ == "__main__":
    C.main(check)
