"""S01 - a new service is exposed by adding one file; nothing else is touched."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from lab import fsx, jsonio, lint, routes, topology  # noqa: E402

SID = "S01"


def _digests(inst: str) -> dict:
    return {rel: jsonio.sha256_file(fsx.join(inst, rel)) for rel in fsx.walk_files(inst)}


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = C.copy_input(scn, "instance", os.path.join(work, "instance"))
    checks = C.Checks()

    topo, problems = topology.load(inst)
    checks.eq("topology loads", problems, [])
    before = lint.run(inst, topo)
    report_before, _ = C.audit(inst, os.path.join(work, "audit-before"))
    files_before = _digests(inst)
    unreachable = routes.resolve(before["provisional"], "public", "tracking.valdora.example", "/")

    # the change: one file is added under routes/
    fsx.write_bytes(fsx.join(inst, "routes/tracking.yaml"),
                    fsx.read_bytes(os.path.join(scn, "input", "new", "tracking.yaml")))
    after = lint.run(inst, topo)
    code, text = C.cli(inst, os.path.join(work, "audit-after"))
    report_after = jsonio.read(os.path.join(work, "audit-after", "report.json"))
    files_after = _digests(inst)

    checks.eq("before: verdict OK", report_before["verdict"], "OK")
    checks.true("before: the new service is declared but not reachable", unreachable is None)
    checks.eq("after: verdict OK", report_after["verdict"], "OK")
    checks.eq("after: exit code 0", code, 0)
    checks.true("after: console says RUN OK", C.console_says_ok(text))
    checks.eq("after: no findings", C.brief(report_after), [])
    checks.true("after: table accepted", after["accepted"] is not None)

    diff = routes.diff(before["accepted"] or [], after["accepted"] or [])
    checks.eq("diff: one entry added", len(diff["added"]), 1)
    checks.eq("diff: nothing removed", diff["removed"], [])
    checks.eq("diff: nothing changed", diff["changed"], [])
    checks.eq("files: exactly one file added", sorted(set(files_after) - set(files_before)), ["routes/tracking.yaml"])
    checks.eq("files: no file removed", sorted(set(files_before) - set(files_after)), [])
    checks.eq("files: every pre-existing file byte-identical",
              [rel for rel in files_before if files_after.get(rel) != files_before[rel]], [])

    hit = routes.resolve(after["accepted"] or [], "public", "tracking.valdora.example", "/")
    checks.eq("the new host resolves to the new service", hit and [hit["target_service"], hit["target_port"]],
              ["tracking", 3000])
    checks.true("the new host is not served on the admin entrypoint",
                routes.resolve(after["accepted"] or [], "admin", "tracking.valdora.example", "/") is None)
    unchanged = [e for e in after["accepted"] or [] if e["file"] != "routes/tracking.yaml"]
    checks.eq("every other route resolves as before",
              [[e["router"], e["target_service"]] for e in unchanged],
              [[e["router"], e["target_service"]] for e in before["accepted"] or []])

    actual = {
        "scenario": SID,
        "before": {"verdict": report_before["verdict"], "routes": report_before["routes"]["count"],
                   "table_sha256": report_before["routes"]["accepted_sha256"]},
        "after": {"verdict": report_after["verdict"], "exit_code": code, "routes": report_after["routes"]["count"],
                  "table_sha256": report_after["routes"]["accepted_sha256"]},
        "diff": diff,
        "files_added": sorted(set(files_after) - set(files_before)),
    }
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "one file added, 1 route added, 0 removed, 0 changed, table accepted", actual)


if __name__ == "__main__":
    C.main(check)
