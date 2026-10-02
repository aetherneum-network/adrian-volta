"""S06 - one corrupt block in the primary repository: the drill fails, names the file, and the fallback is declared."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from corpus import reference_hashes  # noqa: E402  (independent reference, shares no code with the lab)
from lab import backup, fsx, jsonio  # noqa: E402

SID = "S06"


def damage(inst: str, spec: dict) -> dict:
    """Flip one byte of one block of the newest snapshot of the repository, as written in ``input/damage.json``."""
    repo = backup.Repo(os.path.join(inst, f"repo_{spec['repo']}"))
    snapshot = repo.read_snapshot(repo.snapshot_ids()[-1])
    entry = next(e for e in snapshot["files"] if e["path"] == spec["file"])
    address = entry["blocks"][spec["block"]]
    offset, length = repo.load_index()["blocks"][address]
    pack = bytearray(fsx.read_bytes(repo.pack_path))
    position = offset + spec["offset"]
    pack[position] ^= spec["xor"]
    fsx.write_bytes(repo.pack_path, bytes(pack))
    return {"snapshot": snapshot["id"], "block": address, "pack_offset": position, "block_length": length}


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = os.path.join(work, "instance")
    C.build_backup_instance(scn, inst)
    spec = C.read_json(scn, "input/damage.json")
    checks = C.Checks()

    before, _ = C.audit(inst, os.path.join(work, "audit-before"))
    checks.eq("before the damage: verdict OK", before["verdict"], "OK")
    done = damage(inst, spec)

    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    drill, fallback = report["drill"], report["fallback"] or {}
    reference = reference_hashes.check(inst)
    checks.eq("verdict FAILED", report["verdict"], "FAILED")
    checks.eq("exit code 3", code, 3)
    checks.true("console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("one finding: corrupt block, on the primary repository and its newest snapshot",
              [[f["class"], f["file"], f["item"]] for f in found], [["block_corrupt", "repo_a", done["snapshot"]]])
    checks.eq("drill result failed", drill["result"], "failed")
    checks.eq("the damaged file is named, and only that file", drill["mismatched_files"], [spec["file"]])
    checks.eq("the restored count is one short of the source", drill["source_count"] - drill["restored_count"], 1)
    checks.eq("the independent reference finds the same file in repository a", reference["repos"]["a"]["differing"], [spec["file"]])
    checks.true("fallback on the secondary repository is declared", fallback.get("declared") is True)
    checks.eq("fallback: repository b restores and verifies", [fallback.get("repo"), fallback.get("result")], ["b", "ok"])
    checks.eq("the independent reference finds no differing file in repository b", reference["repos"]["b"]["differing"], [])
    checks.true("console declares the fallback", "fallback declared: repository b" in text)
    checks.true("a postmortem is written", fsx.isfile(os.path.join(work, "audit", "postmortem.md")))
    checks.true("the registry had recorded this snapshot as restored successfully before the damage",
                report["findings"] and report["findings"][0]["detail"]["registry_claimed_ok"] is True)

    actual = {"scenario": SID, "verdict": report["verdict"], "exit_code": code, "findings": found,
              "damage": done, "mismatched_files": drill["mismatched_files"],
              "source_count": drill["source_count"], "restored_count": drill["restored_count"],
              "fallback": {"repo": fallback.get("repo"), "snapshot": fallback.get("snapshot"), "result": fallback.get("result")}}
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "1 byte flipped in 1 block: restore FAILED, 1 file named, fallback on repository b declared and verified",
                    actual)


if __name__ == "__main__":
    C.main(check)
