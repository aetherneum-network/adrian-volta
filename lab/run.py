"""Audit one synthetic instance: routes, health, nightly chain, restore drill, retention.

    python -m lab.run --instance <dir> [--work <dir>] [--json <file>]

Exit codes: ``0`` OK, ``1`` ALERT, ``2`` BLOCKED, ``3`` FAILED. The words "RUN OK" are printed only
when the verdict is OK. The instance is read-only: the restore goes to a scratch directory and
the drill is appended to a *copy* of the registry inside the work directory.

``as_of`` comes from ``instance.json``; the wall clock is never read.
"""
from __future__ import annotations

import argparse
import os
import sys

if __package__ in (None, ""):  # allow `python lab/run.py`
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lab import __version__, backup, drill, fsx, heartbeat, jsonio, lint, offline, postmortem, probe, routes  # noqa: E402
from lab import findings as F  # noqa: E402
from lab import rules_engine, sim, timeutil, topology  # noqa: E402

SCOPES = ("routing", "health", "backup")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _finish(report: dict, found: list[dict], work_dir) -> dict:
    report["findings"] = F.ordered(found)
    report["verdict"] = F.overall(found)
    report["exit_code"] = F.exit_code(report["verdict"])
    if work_dir is not None:
        jsonio.write(fsx.join(work_dir, "report.json"), report)
        if report["verdict"] == "FAILED" and report.get("as_of"):
            jsonio.write_text(fsx.join(work_dir, "postmortem.md"), postmortem.render(report))
    return report


def audit(instance_dir, work_dir, keep_restore: bool = False) -> dict:
    found: list[dict] = []
    report: dict = {"lab": __version__, "rules": rules_engine.versions(), "instance": os.path.basename(os.path.normpath(str(instance_dir))),
                    "as_of": None}
    try:
        inst = jsonio.read(fsx.join(instance_dir, "instance.json"))
        as_of = timeutil.parse_utc(inst["as_of"])
        report["instance"] = str(inst["instance_id"])
        scope = inst.get("scope", list(SCOPES))
        if not isinstance(scope, list) or not scope or any(s not in SCOPES for s in scope):
            raise ValueError("scope")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        found.append(F.make("instance_unreadable", "RUN-000", "instance.json", None,
                            detail={"reason": f"{type(exc).__name__}: instance.json missing, not JSON, without an explicit-UTC as_of, or with an unknown scope"}))
        return _finish(report, found, work_dir)
    report.update({"as_of": timeutil.fmt(as_of), "scope": scope})

    topo, trace, trace_ok = None, {"sim": [], "jobs": []}, True
    if "routing" in scope or "health" in scope:
        topo, problems = topology.load(instance_dir)
        if topo is None:
            found.append(F.make("topology_unreadable", "RUN-010", topology.FILE, None, detail={"problems": problems}))
    if "health" in scope or "backup" in scope:
        trace, problems = sim.load_trace(instance_dir)
        if problems:
            trace_ok = False
            found.append(F.make("trace_unreadable", "RUN-020", sim.FILE, None, detail={"problems": problems}))

    # ---- routing
    linted = None
    if topo is not None:
        linted = lint.run(instance_dir, topo)
        if "routing" in scope:
            found.extend(linted["findings"])
            accepted = linted["accepted"]
            report["routes"] = {
                "files": len(linted["files"]), "count": len(linted["provisional"]),
                "provisional_sha256": routes.table_sha256(linted["provisional"]),
                "accepted": accepted is not None,
                "accepted_sha256": routes.table_sha256(accepted) if accepted is not None else None,
                "admin_public_edges_in_accepted_table": lint.reach_matrix(accepted, topo)["admin_public_edges"],
            }

    # ---- health (end-to-end probe on the table as loaded)
    if "health" in scope and topo is not None and trace_ok:
        world = sim.World(trace["sim"])
        health = probe.evaluate(topo, linted["provisional"], world, as_of)
        found.extend(health["findings"])
        report["health"] = {k: v for k, v in health.items() if k != "findings"}

    # ---- nightly chain, repositories, restore drill, retention
    if "backup" in scope:
        policy, problems = backup.load_policy(instance_dir)
        if policy is None:
            found.append(F.make("policy_unreadable", "RUN-030", backup.POLICY_FILE, None, detail={"problems": problems}))
        elif trace_ok:
            _audit_backup(instance_dir, work_dir, policy, trace["jobs"], as_of, found, report, keep_restore)
    return _finish(report, found, work_dir)


def _audit_backup(instance_dir, work_dir, policy, jobs, as_of, found, report, keep_restore) -> None:
    rules = rules_engine.load(backup.RULES)
    beat = heartbeat.evaluate(policy, jobs, as_of)
    found.extend(beat["findings"])
    report["heartbeat"] = {k: v for k, v in beat.items() if k != "findings"}

    chain = backup.chain_facts(policy, jobs)
    then, rid = rules.decide("chain_rules", chain["facts"])
    order_inverted = rid is not None
    if order_inverted:
        item = "chain" if then["item"] == "chain" else chain["evidence"].get("latest_run")
        timeline = []
        if then["item"] != "chain":
            timeline = [[chain["evidence"]["backup_a_start"], "trace.jsonl", "backup_a started"],
                        [chain["evidence"]["export_end"], "trace.jsonl", "export ended (after the backup had started)"]]
        found.append(F.make(then["class"], rid, then["file"], item, detail=dict(chain["evidence"], timeline=timeline)))
    report["chain"] = chain

    repo_a = backup.Repo(fsx.join(instance_dir, policy["repos"]["a"]["path"]))
    repo_b = backup.Repo(fsx.join(instance_dir, policy["repos"]["b"]["path"]))
    second = heartbeat.secondary(policy, repo_a, repo_b, jobs, as_of)
    if second["finding"]:
        found.append(second["finding"])
    report["secondary"] = {k: v for k, v in second.items() if k != "finding"}

    registry_path = fsx.join(work_dir, drill.FILE)
    source_registry = fsx.join(instance_dir, drill.FILE)
    if fsx.exists(registry_path):
        fsx.move(registry_path, registry_path + ".previous")  # a work dir is reused: keep the old copy aside
    if fsx.exists(source_registry):
        fsx.write_bytes(registry_path, fsx.read_bytes(source_registry))
    state = drill.read_registry(registry_path)
    if not state["chain_ok"]:
        found.append(F.make("registry_tampered", "RUN-040", drill.FILE, None, detail={"problem": state["problem"]}))
    on_record = drill.verified(state, "a")

    source_dir = fsx.join(instance_dir, policy["source"])
    scratch_a = fsx.join(work_dir, "restore/a")
    result = drill.run(source_dir, repo_a.path, "a", scratch_a, as_of)
    report["drill"] = result.as_dict()
    fallback = None
    if not result.ok:
        then, rid = rules.decide("drill_rules", drill.facts(result, order_inverted))
        report["drill"]["rule"] = rid
        report["drill"]["explained_by"] = then.get("explained_by")
        if then.get("class"):
            timeline = [[timeutil.fmt(as_of), "drill", f"restore of snapshot {result.snapshot} from {policy['repos']['a']['path']}: "
                         f"{len(result.mismatched_files)} file(s) differ from the source manifest"]]
            found.append(F.make(then["class"], rid, policy["repos"]["a"]["path"], result.snapshot,
                                detail={"mismatched_files": result.mismatched_files,
                                        "problems": report["drill"]["problems"],
                                        "registry_claimed_ok": result.snapshot in on_record, "timeline": timeline}))
        second_try = drill.run(source_dir, repo_b.path, "b", fsx.join(work_dir, "restore/b"), as_of)
        fallback = dict(second_try.as_dict(), declared=True)
    report["fallback"] = fallback
    if state["chain_ok"]:
        drill.append(registry_path, result)
        if fallback is not None:
            drill.append(registry_path, second_try)
    after = drill.read_registry(registry_path)
    report["registry"] = {"chain_valid": after["chain_ok"], "entries": len(after["entries"]), "head": after["head"]}

    if "prune" in policy["chain"]:
        verified = set(on_record)
        if result.snapshot is not None:
            if not result.ok:
                verified.discard(result.snapshot)
            elif chain["facts"].get("policy_verify_before_prune"):
                verified.add(result.snapshot)
        decision = backup.retention(policy, repo_a, verified)
        decision["applied"] = False
        if decision["class"]:
            found.append(F.make(decision["class"], decision["rule"], backup.POLICY_FILE, "retention",
                                detail={k: decision[k] for k in ("facts", "keep_last", "snapshots", "keep", "supersede", "verified")}))
        report["retention"] = decision
    else:
        report["retention"] = {"decision": "not_scheduled", "applied": False}
    if not keep_restore:
        fsx.rmtree(fsx.join(work_dir, "restore"))


def render(report: dict) -> str:
    """Console summary. "RUN OK" appears only for an OK verdict; no other line uses that word."""
    lines = [f"instance {report['instance']} - data as of {report.get('as_of') or '[unknown]'}"]
    for f in report["findings"]:
        also = (" + " + ", ".join(f["also"])) if f["also"] else ""
        lines.append(f"{f['verdict']:<8}{f['class']}  {f['file']}{also}  {f['item'] or '-'}  [{f['rule']}]")
    d = report.get("drill")
    if d:
        state = "verified against the source manifest" if d["result"] == "ok" else f"failed ({len(d['mismatched_files'])} file(s) differ)"
        lines.append(f"drill   repository {d['repo']} snapshot {d['snapshot']}: {state}")
    fb = report.get("fallback")
    if fb:
        state = "verified against the source manifest" if fb["result"] == "ok" else f"failed ({len(fb['mismatched_files'])} file(s) differ)"
        lines.append(f"fallback declared: repository {fb['repo']} snapshot {fb['snapshot']}: {state}")
    cu = (report.get("heartbeat") or {}).get("catch_up")
    if cu:
        lines.append(f"catch-up declared: one run recovers window {cu['recover_window']}")
    lines.append(f"RUN {report['verdict']} - data as of {report.get('as_of') or '[unknown]'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    offline.enforce()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--instance", required=True, help="instance directory (read-only)")
    ap.add_argument("--work", help="work directory (default: build/run/<instance directory name>)")
    ap.add_argument("--json", help="also write the report to this file")
    ap.add_argument("--keep-restore", action="store_true", help="keep the restored tree in the work directory")
    args = ap.parse_args(argv)
    work = args.work or os.path.join(ROOT, "build", "run", os.path.basename(os.path.normpath(args.instance)))
    report = audit(args.instance, work, keep_restore=args.keep_restore)
    if args.json:
        jsonio.write(args.json, report)
    print(render(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
