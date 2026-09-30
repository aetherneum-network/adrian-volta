"""S05 - backup and restore of a tree with long paths and accented names: counts reconcile and every hash matches."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from corpus import reference_hashes  # noqa: E402  (independent reference, shares no code with the lab)
from lab import fsx, jsonio, restore  # noqa: E402

SID = "S05"


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = os.path.join(work, "instance")
    C.build_backup_instance(scn, inst)
    checks = C.Checks()

    audit_dir = os.path.join(work, "audit")
    code, text = C.cli(inst, audit_dir, keep_restore=True)
    report = jsonio.read(os.path.join(audit_dir, "report.json"))
    drill = report["drill"]
    manifest = restore.source_manifest(os.path.join(inst, "source"))
    restored_dir = os.path.join(audit_dir, "restore", "a")
    restored = {rel: jsonio.sha256_file(fsx.join(restored_dir, rel)) for rel in fsx.walk_files(restored_dir)}
    reference = reference_hashes.check(inst)
    longest = max((len(rel) for rel in restored), default=0)

    checks.eq("verdict OK", report["verdict"], "OK")
    checks.eq("exit code 0", code, 0)
    checks.true("console says RUN OK", C.console_says_ok(text))
    checks.eq("no findings", C.brief(report), [])
    checks.eq("drill result ok", drill["result"], "ok")
    checks.eq("counts reconcile: source = declared by the snapshot = restored",
              [drill["source_count"], drill["declared_count"], drill["restored_count"]], [manifest["count"]] * 3)
    checks.eq("no file differs", drill["mismatched_files"], [])
    checks.eq("every restored file has the SHA-256 of its source (re-hashed here, file by file)",
              restored, {rel: f["sha256"] for rel, f in manifest["files"].items()})
    checks.eq("the independent reference finds no differing file in repository a", reference["repos"]["a"]["differing"], [])
    checks.eq("the independent reference finds no differing file in repository b", reference["repos"]["b"]["differing"], [])
    checks.eq("the independent reference counts the same source files", reference["source_files"], manifest["count"])
    checks.true("a restored relative path is longer than 260 characters", longest > 260)
    checks.true("the tree contains names with accented letters", any(not rel.isascii() for rel in restored))
    checks.true("the tree contains an empty file", any(f["size"] == 0 for f in manifest["files"].values()))
    checks.eq("the drill is on record and the registry chain verifies",
              [report["registry"]["chain_valid"], report["registry"]["entries"]], [True, 3])

    actual = {"scenario": SID, "verdict": report["verdict"], "exit_code": code, "findings": C.brief(report),
              "snapshot": drill["snapshot"], "source_count": drill["source_count"],
              "declared_count": drill["declared_count"], "restored_count": drill["restored_count"],
              "source_manifest_sha256": drill["source_manifest_sha256"], "source_bytes": manifest["bytes"],
              "longest_relative_path": max(len(rel) for rel in manifest["files"]),
              "non_ascii_paths": sum(1 for rel in manifest["files"] if not rel.isascii())}
    C.pinned(scn, checks, actual)
    fsx.rmtree(os.path.join(audit_dir, "restore"))
    return C.report(SID, checks, f"{drill['restored_count']} of {drill['source_count']} files restored, every SHA-256 equal to the source, 0 differing",
                    actual)


if __name__ == "__main__":
    C.main(check)
