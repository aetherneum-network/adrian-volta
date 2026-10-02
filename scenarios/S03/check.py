"""S03 (negative) - an admin service must not become reachable from the public plane; the table is refused."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _common as C  # noqa: E402
from corpus import reference_reach  # noqa: E402  (independent reference, shares no code with the lab)
from lab import jsonio, lint, topology  # noqa: E402

SID = "S03"


def check():
    scn = C.scenario_dir(__file__)
    work = C.workdir(SID)
    checks = C.Checks()
    actual = {"scenario": SID}

    # 1. one router lists the admin and the public entrypoint
    inst = C.copy_input(scn, "instance", os.path.join(work, "instance"))
    code, text = C.cli(inst, os.path.join(work, "audit"))
    report = jsonio.read(os.path.join(work, "audit", "report.json"))
    found = C.brief(report)
    topo, _ = topology.load(inst)
    as_loaded = lint.reach_matrix(lint.run(inst, topo)["provisional"], topo)["admin_public_edges"]
    reference = reference_reach.matrix(inst)["admin_public_edges"]
    checks.eq("router: verdict BLOCKED", report["verdict"], "BLOCKED")
    checks.eq("router: exit code 2", code, 2)
    checks.true("router: console does not say RUN OK", not C.console_says_ok(text))
    checks.eq("router: one finding, on the file and router that draw the edge",
              [[f["class"], f["file"], f["item"]] for f in found],
              [["admin_on_public", "routes/registry.yaml", "registry-main"]])
    checks.true("router: table not accepted", report["routes"]["accepted"] is False)
    checks.eq("router: no admin-public edge in the accepted table",
              report["routes"]["admin_public_edges_in_accepted_table"], [])
    checks.eq("router: the table as loaded would have drawn the edge", as_loaded, [["public", "registry"]])
    checks.eq("router: the independent reference sees the same edge", reference, [["public", "registry"]])
    actual["router"] = {"verdict": report["verdict"], "exit_code": code, "findings": found,
                        "edges_as_loaded": as_loaded, "edges_reference": reference,
                        "edges_in_accepted_table": report["routes"]["admin_public_edges_in_accepted_table"]}

    # 2. no router is wrong, but an admin service is attached to the public network
    inst2 = C.copy_input(scn, "instance-network", os.path.join(work, "instance-network"))
    code2, text2 = C.cli(inst2, os.path.join(work, "audit-network"))
    report2 = jsonio.read(os.path.join(work, "audit-network", "report.json"))
    found2 = C.brief(report2)
    checks.eq("network: verdict BLOCKED", report2["verdict"], "BLOCKED")
    checks.eq("network: exit code 2", code2, 2)
    checks.true("network: console does not say RUN OK", not C.console_says_ok(text2))
    checks.eq("network: one finding, on the topology and the service",
              [[f["class"], f["file"], f["item"]] for f in found2], [["admin_on_public", "topology.yaml", "metrics"]])
    checks.true("network: table not accepted", report2["routes"]["accepted"] is False)
    actual["network"] = {"verdict": report2["verdict"], "exit_code": code2, "findings": found2}

    C.pinned(scn, checks, actual)
    return C.report(SID, checks, "admin service on the public plane refused twice (router, network); accepted table has 0 admin-public edges",
                    actual)


if __name__ == "__main__":
    C.main(check)
