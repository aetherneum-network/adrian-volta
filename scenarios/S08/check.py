"""S08 - backup before export: the snapshot holds yesterday's export. The chain in the right order has the fresh one."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from corpus import reference_hashes  # noqa: E402  (independent reference, shares no code with the lab)
from lab import backup, jsonio  # noqa: E402

SID = "S08"
EXPORT = "export/orders-export.csv"


def export_in_newest_snapshot(inst: str) -> str:
    repo = backup.Repo(os.path.join(inst, "repo_a"))
    snapshot = repo.read_snapshot(repo.snapshot_ids()[-1])
    return next(e["sha256"] for e in snapshot["files"] if e["path"] == EXPORT)


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    checks = C.Checks()
    nights = C.read_json(scn, "input/nights.json")
    size = 1800
    yesterday = jsonio.sha256_bytes(C.stream(nights[-2]["export_seed"], size))
    today = jsonio.sha256_bytes(C.stream(nights[-1]["export_seed"], size))

    # 1. the policy lists backup_a before export
    inst = os.path.join(work, "instance")
    C.build_backup_instance(scn, inst)
    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    saved = export_in_newest_snapshot(inst)
    checks.eq("inverted: verdict FAILED", report["verdict"], "FAILED")
    checks.eq("inverted: exit code 3", code, 3)
    checks.true("inverted: console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("inverted: one finding, on the chain written in the policy",
              [[f["class"], f["file"], f["item"]] for f in found], [["job_order_inverted", "policy/backup.yaml", "chain"]])
    checks.eq("inverted: the drill fails on the export file, and only on it", report["drill"]["mismatched_files"], [EXPORT])
    checks.eq("inverted: the drill failure is explained by the order, not reported twice",
              report["drill"]["explained_by"], "job_order_inverted")
    checks.eq("inverted: the snapshot holds yesterday's export", saved, yesterday)
    checks.true("inverted: the snapshot does not hold today's export", saved != today)
    checks.eq("inverted: the source holds today's export", jsonio.sha256_file(os.path.join(inst, "source", *EXPORT.split("/"))), today)
    checks.eq("inverted: the independent reference finds the same file", reference_hashes.check(inst)["repos"]["a"]["differing"], [EXPORT])
    checks.eq("inverted: the nightly drill had already failed twice on record, before the audit",
              [e["result"] for e in jsonio.read_jsonl(os.path.join(inst, "drills.jsonl"))], ["failed", "failed"])

    # 2. the same nights with the chain in order (export, then backup)
    chained = os.path.join(work, "instance-chained")
    C.build_backup_instance(scn, chained, variant="instance-chained")
    code2, text2 = C.cli(chained, os.path.join(work, "audit-chained"))
    report2 = jsonio.read(os.path.join(work, "audit-chained", "report.json"))
    saved2 = export_in_newest_snapshot(chained)
    checks.eq("chained: verdict OK", report2["verdict"], "OK")
    checks.eq("chained: exit code 0", code2, 0)
    checks.true("chained: console says RUN OK", C.console_says_ok(text2))
    checks.eq("chained: the snapshot holds today's export", saved2, today)
    checks.eq("chained: the drill verifies", [report2["drill"]["result"], report2["drill"]["mismatched_files"]], ["ok", []])

    actual = {"scenario": SID,
              "inverted": {"verdict": report["verdict"], "exit_code": code, "findings": found,
                           "mismatched_files": report["drill"]["mismatched_files"],
                           "drill_rule": report["drill"]["rule"], "explained_by": report["drill"]["explained_by"],
                           "export_in_snapshot_sha256": saved},
              "chained": {"verdict": report2["verdict"], "exit_code": code2, "export_in_snapshot_sha256": saved2},
              "export_yesterday_sha256": yesterday, "export_today_sha256": today}
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "backup before export: snapshot holds yesterday's export, FAILED on 1 file; chain in order: today's export, OK",
                    actual)


if __name__ == "__main__":
    C.main(check)
