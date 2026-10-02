"""S04 - the healthcheck watches the process, the route answers 404: the end-to-end probe declares it in two beats."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from corpus import reference_reach  # noqa: E402  (independent reference, shares no code with the lab)
from lab import fsx, jsonio  # noqa: E402

SID = "S04"


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    inst = C.copy_input(scn, "instance", os.path.join(work, "instance"))
    checks = C.Checks()

    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    checks.eq("verdict FAILED", report["verdict"], "FAILED")
    checks.eq("exit code 3", code, 3)
    checks.true("console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("one finding: the healthcheck of the service, in the topology",
              [[f["class"], f["file"], f["item"]] for f in found], [["health_probes_process", "topology.yaml", "status"]])
    detail = report["findings"][0]["detail"] if report["findings"] else {}
    checks.eq("first failing beat", detail.get("first_failing_beat"), "2026-09-14T06:42:00Z")
    checks.eq("declared at the second beat", detail.get("declared_at"), "2026-09-14T06:43:00Z")
    checks.eq("declared within two beats", detail.get("beats_to_declare"), 2)
    checks.eq("the route answered 404", detail.get("statuses"), [404])
    checks.eq("the declared healthcheck only looks at the process", detail.get("health_kind"), "process")
    checks.eq("the other services have no failing beat",
              {name: s["probe_consecutive_failures"] for name, s in report["health"]["services"].items() if name != "status"},
              {"catalog": 0, "console": 0, "orders": 0})
    checks.eq("the independent replay finds the same service",
              reference_reach.check(inst), [{"class": "health_probes_process", "files": ["topology.yaml"], "item": "status"}])

    postmortem_path = os.path.join(work, "audit", "postmortem.md")
    checks.true("a postmortem is written", fsx.isfile(postmortem_path))
    postmortem = fsx.read_bytes(postmortem_path).decode("utf-8") if fsx.isfile(postmortem_path) else ""
    expected_path = os.path.join(scn, "expected", "postmortem.md")
    if "--pin" in sys.argv:          # author tool, see _common.pinned
        jsonio.write_text(expected_path, postmortem)
    expected = fsx.read_bytes(expected_path).decode("utf-8") if fsx.isfile(expected_path) else None
    checks.true("postmortem equals expected/postmortem.md", postmortem != "" and postmortem == expected)
    for heading in ("## Summary", "## Timeline (UTC)", "## Evidence", "## What the checks did not see", "## Follow-ups"):
        checks.true(f"postmortem has section {heading[3:]}", heading in postmortem)
    checks.true("postmortem timeline cites the first failing beat", "| 2026-09-14T06:42:00Z | probe |" in postmortem)

    actual = {"scenario": SID, "verdict": report["verdict"], "exit_code": code, "findings": found,
              "first_failing_beat": detail.get("first_failing_beat"), "declared_at": detail.get("declared_at"),
              "failing_beats": detail.get("failing_beats"), "statuses": detail.get("statuses"),
              "postmortem_sha256": jsonio.sha256_bytes(postmortem.encode("utf-8"))}
    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "process healthy, route 404: declared FAILED at the second beat, postmortem rendered", actual)


if __name__ == "__main__":
    C.main(check)
