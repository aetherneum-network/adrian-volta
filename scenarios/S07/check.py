"""S07 - the secondary repository stopped three days ago while the job log says ok: the repository is read, not the log."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from corpus import reference_hashes  # noqa: E402  (independent reference, shares no code with the lab)
from lab import jsonio  # noqa: E402

SID = "S07"


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = os.path.join(work, "instance")
    built = C.build_backup_instance(scn, inst)
    checks = C.Checks()

    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    second = report["secondary"]
    b_newest = built["nights"][0]["backup_b"]
    jobs = [j for j in jsonio.read_jsonl(os.path.join(inst, "trace.jsonl")) if j.get("step") == "backup_b"]
    checks.eq("the job log claims ok for backup_b on all four nights", [j["status"] for j in jobs], ["ok"] * 4)
    checks.eq("verdict ALERT", report["verdict"], "ALERT")
    checks.eq("exit code 1", code, 1)
    checks.true("console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("one finding: secondary repository stale, on the repository and its newest snapshot",
              [[f["class"], f["file"], f["item"]] for f in found], [["repo_b_stale", "repo_b", b_newest]])
    checks.true("the lag is three days (72 h minus the seconds between the two backup steps)", 71.9 <= second["lag_hours"] <= 72.0)
    checks.eq("the limit written in the policy is 26 hours", second["max_lag_hours"], 26)
    checks.eq("the alert carries the instant the data refers to", second["as_of"], "2026-09-12T06:10:00Z")
    checks.eq("what the job log claimed is reported next to what the repository shows", second["job_log_claims"], ["ok"])
    checks.eq("the primary repository restores and verifies", report["drill"]["result"], "ok")
    checks.true("the independent reference also finds the secondary stale", reference_hashes.check(inst)["secondary_stale"] is True)

    actual = {"scenario": SID, "verdict": report["verdict"], "exit_code": code, "findings": found,
              "a_newest": second["a_newest"], "b_newest": second["b_newest"], "lag_hours": second["lag_hours"],
              "b_age_hours_at_as_of": second["b_age_hours_at_as_of"], "max_lag_hours": second["max_lag_hours"],
              "as_of": second["as_of"], "job_log_claims": second["job_log_claims"]}
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "job log says ok, secondary repository 72 h behind (limit 26 h): ALERT with as_of, primary drill ok",
                    actual)


if __name__ == "__main__":
    C.main(check)
