"""S10 (negative) - retention must not supersede the only verified snapshot; when it may act, it moves, never deletes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from lab import backup, drill, fsx, jsonio, timeutil  # noqa: E402

SID = "S10"


def _manifests(repo_dir: str) -> dict:
    return {"snapshots": [r for r in fsx.walk_files(os.path.join(repo_dir, "snapshots"))],
            "superseded": [r for r in fsx.walk_files(os.path.join(repo_dir, "superseded"))]}


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = os.path.join(work, "instance")
    built = C.build_backup_instance(scn, inst)
    repo_dir = os.path.join(inst, "repo_a")
    repo = backup.Repo(repo_dir)
    ids = [night["backup_a"] for night in built["nights"]]
    checks = C.Checks()

    # 1. keep_last 1, prune before verify, only the oldest snapshot has a successful drill on record
    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    decision = report["retention"]
    pack_before = jsonio.sha256_file(repo.pack_path)
    checks.eq("verdict BLOCKED", report["verdict"], "BLOCKED")
    checks.eq("exit code 2", code, 2)
    checks.true("console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("one finding, on the retention written in the policy",
              [[f["class"], f["file"], f["item"]] for f in found],
              [["retention_deletes_only_valid", "policy/backup.yaml", "retention"]])
    checks.eq("decision: block", decision["decision"], "block")
    checks.eq("the only verified snapshot is the oldest one", decision["verified"], [ids[0]])
    checks.eq("retention would keep only the newest, unverified", decision["keep"], [ids[2]])
    checks.eq("retention would supersede the verified one", decision["supersede"], [ids[0], ids[1]])
    checks.eq("a blocked decision moves nothing", backup.apply_retention(repo, decision), [])
    checks.eq("all three manifests are still in place", _manifests(repo_dir), {"snapshots": [f"{i}.json" for i in ids], "superseded": []})

    # 2. the newest snapshot is restored and verified, and the drill goes on record
    result = drill.run(os.path.join(inst, "source"), repo_dir, "a", os.path.join(work, "scratch"),
                       timeutil.parse_utc("2026-09-12T06:20:00Z"), snapshot=ids[2])
    drill.append(os.path.join(inst, drill.FILE), result)
    fsx.rmtree(os.path.join(work, "scratch"))
    inst_info = jsonio.read(os.path.join(inst, "instance.json"))
    inst_info["as_of"] = "2026-09-12T06:30:00Z"
    jsonio.write(os.path.join(inst, "instance.json"), inst_info)
    code2, text2 = C.cli(inst, os.path.join(work, "audit-after-drill"))
    report2 = jsonio.read(os.path.join(work, "audit-after-drill", "report.json"))
    decision2 = report2["retention"]
    checks.eq("after the drill: the newest snapshot verified", result.result, "ok")
    checks.eq("after the drill: verdict OK", report2["verdict"], "OK")
    checks.eq("after the drill: exit code 0", code2, 0)
    checks.true("after the drill: console says RUN OK", C.console_says_ok(text2))
    checks.eq("after the drill: decision supersede", decision2["decision"], "supersede")
    checks.true("after the drill: the audit itself applies nothing", decision2["applied"] is False)
    moved = backup.apply_retention(repo, decision2)
    after = _manifests(repo_dir)
    checks.eq("applied: the two older manifests are moved", moved, [ids[0], ids[1]])
    checks.eq("applied: moved to superseded/, the newest stays", after,
              {"snapshots": [f"{ids[2]}.json"], "superseded": [f"{ids[0]}.json", f"{ids[1]}.json"]})
    checks.eq("applied: no manifest is deleted (three before, three after)", len(after["snapshots"]) + len(after["superseded"]), 3)
    checks.eq("applied: the pack is untouched", jsonio.sha256_file(repo.pack_path), pack_before)
    code3, _ = C.cli(inst, os.path.join(work, "audit-after-retention"))
    checks.eq("applied: the instance still audits OK", code3, 0)

    actual = {"scenario": SID,
              "blocked": {"verdict": report["verdict"], "exit_code": code, "findings": found,
                          "decision": decision["decision"], "rule": decision["rule"], "facts": decision["facts"],
                          "keep": decision["keep"], "supersede": decision["supersede"], "verified": decision["verified"]},
              "after_drill": {"verdict": report2["verdict"], "exit_code": code2, "decision": decision2["decision"],
                              "rule": decision2["rule"], "facts": decision2["facts"], "moved": moved, "manifests": after}}
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "retention refused while it would supersede the only verified snapshot; after a verified drill: 2 manifests moved, 0 deleted",
                    actual)


if __name__ == "__main__":
    C.main(check)
