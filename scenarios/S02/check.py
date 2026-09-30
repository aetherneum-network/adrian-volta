"""S02 - forty-one routes and one forgotten copy in a sub-folder: the table is refused, with both files named."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from lab import fsx, jsonio  # noqa: E402

SID = "S02"
COPY = "routes/archive-2025/shop.yaml"
ORIGINAL = "routes/shop.yaml"


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = C.copy_input(scn, "instance", os.path.join(work, "instance"))
    checks = C.Checks()

    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    checks.eq("verdict BLOCKED", report["verdict"], "BLOCKED")
    checks.eq("exit code 2", code, 2)
    checks.true("console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("exactly one finding", len(found), 1)
    checks.eq("the finding is the duplicate", found and found[0]["class"], "route_duplicate_shadow")
    checks.eq("both files are named", found and sorted([found[0]["file"]] + found[0]["also"]), sorted([COPY, ORIGINAL]))
    checks.eq("table as loaded has 42 entries", report["routes"]["count"], 42)
    checks.true("table not accepted", report["routes"]["accepted"] is False)
    detail = report["findings"][0]["detail"] if report["findings"] else {}
    checks.eq("the pair shares entrypoint, host and prefix",
              [detail.get("entrypoint"), detail.get("host"), detail.get("path_prefix")],
              ["public", "shop.serrabruna.example", "/"])
    checks.eq("the copy would win silently (higher priority)", detail.get("winner"), "shop-main-2025")
    others = [f for f in report["findings"] if f["file"] not in (COPY, ORIGINAL)]
    checks.eq("no finding on the other forty routes", others, [])

    # the same tree without the forgotten copy (in the work copy: the committed input is never modified)
    clean = C.copy_input(scn, "instance", os.path.join(work, "instance-without-copy"))
    fsx.move(fsx.join(clean, COPY), os.path.join(work, "set-aside", "shop.yaml"))
    code2, text2 = C.cli(clean, os.path.join(work, "audit-without-copy"))
    report2 = jsonio.read(os.path.join(work, "audit-without-copy", "report.json"))
    checks.eq("without the copy: verdict OK", report2["verdict"], "OK")
    checks.eq("without the copy: exit code 0", code2, 0)
    checks.true("without the copy: console says RUN OK", C.console_says_ok(text2))
    checks.eq("without the copy: 41 entries", report2["routes"]["count"], 41)

    actual = {
        "scenario": SID,
        "with_copy": {"verdict": report["verdict"], "exit_code": code, "findings": found,
                      "routes_as_loaded": report["routes"]["count"], "accepted": report["routes"]["accepted"],
                      "winner": detail.get("winner")},
        "without_copy": {"verdict": report2["verdict"], "exit_code": code2, "routes": report2["routes"]["count"],
                         "table_sha256": report2["routes"]["accepted_sha256"]},
    }
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "42 routes loaded, 1 duplicate found with both files named, table refused; 41 accepted without the copy",
                    actual)


if __name__ == "__main__":
    C.main(check)
