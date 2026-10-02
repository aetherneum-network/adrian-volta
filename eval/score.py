"""Score the lab against the gold labels of one suite.

    python eval/score.py --suite dev
    python eval/score.py --suite stress
    python eval/score.py --suite holdout          # aggregates only; the generated corpus is removed afterwards
    python eval/score.py --suite blind --seed N --runner "NAME" --date YYYY-MM-DD \
        [--styles a,b,c] [--faults-per-instance 2] [--handwritten DIR]

What is measured is *internal consistency on synthetic data*: the generator and the rules have
the same author, and the faults are the ones the generator knows how to plant. Nothing here says
anything about real systems.

Per class: ``detected`` (a finding of that class exists) and ``localised`` (class, file(s) and
item all equal to the gold). Plus: verdict accuracy, false alarms on clean instances, findings
that match no planted fault, exactness of the list of files that differ after the restore, and
the two never-events, judged by the references and not by the lab:

* ``never_event_restore``: the lab called a restore successful while the reference finds a
  file whose SHA-256 differs from the source tree;
* ``never_event_admin_public``: the lab accepted a route table in which the reference finds an
  admin service reachable from a public entrypoint.

Results go to ``eval/results.json`` (deterministic: no durations, no clock). A blind run is
appended to ``eval/history.json`` with the commit it measured, and can be done once per commit:
a new freeze tag gets its own blind run, on a seed never used before.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from corpus import generate as G, reference_hashes, reference_reach, reference_routes, yamlout  # noqa: E402
from lab import fsx, jsonio, offline, run as lab_run  # noqa: E402

RESULTS = os.path.join(ROOT, "eval", "results.json")
HISTORY = os.path.join(ROOT, "eval", "history.json")
BUILD = os.path.join(ROOT, "build", "eval")
DATA_CLASSES = ("backup_truncated", "block_corrupt", "job_order_inverted")
VERDICTS = ("OK", "ALERT", "BLOCKED", "FAILED")
ALL_SCOPE = ("routing", "health", "backup")
_REFERENCE_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError)
LABEL_KEYS = ("clean", "faults", "instance", "verdict")
FAULT_KEYS = ("also", "class", "file", "items", "mismatched_files", "variant", "verdict")


def load_handwritten(folder: str, reserved_prefix: str) -> list:
    """Labels of the hand-written instances, checked before anything is generated or audited.

    A label that cannot be read is an error said at once: it is never completed by guessing.
    """
    path = os.path.join(folder, "labels.jsonl")
    if not fsx.isfile(path):
        raise ValueError("labels.jsonl not found in the folder")
    golds = jsonio.read_jsonl(path)
    seen = set()
    for number, gold in enumerate(golds, start=1):
        where = f"labels.jsonl line {number}"
        if not isinstance(gold, dict) or any(key not in gold for key in LABEL_KEYS):
            raise ValueError(f"{where}: an object with the keys {', '.join(LABEL_KEYS)} is needed")
        name = gold["instance"]
        if not isinstance(name, str) or not name or "/" in name or chr(92) in name or name.startswith("."):
            raise ValueError(f"{where}: 'instance' must be the name of a sub-folder")
        if name in seen or name.startswith(reserved_prefix):
            raise ValueError(f"{where}: instance name '{name}' is repeated or starts with '{reserved_prefix}'")
        seen.add(name)
        if not os.path.isdir(fsx.ext(os.path.join(folder, name))):
            raise ValueError(f"{where}: folder '{name}' not found")
        if gold["verdict"] not in VERDICTS or not isinstance(gold["clean"], bool) or not isinstance(gold["faults"], list):
            raise ValueError(f"{where}: 'verdict' must be one of {', '.join(VERDICTS)}, 'clean' true or false, 'faults' a list")
        if gold["clean"] != (not gold["faults"]) or (gold["clean"] and gold["verdict"] != "OK"):
            raise ValueError(f"{where}: a clean instance has no fault and verdict OK; any other has at least one fault")
        for fault in gold["faults"]:
            if not isinstance(fault, dict) or any(key not in fault for key in FAULT_KEYS):
                raise ValueError(f"{where}: every fault needs the keys {', '.join(FAULT_KEYS)}")
            if not isinstance(fault["class"], str) or not isinstance(fault["file"], str) or fault["verdict"] not in VERDICTS:
                raise ValueError(f"{where}: 'class' and 'file' are strings, 'verdict' one of {', '.join(VERDICTS)}")
            if not isinstance(fault["also"], list) or not isinstance(fault["mismatched_files"], list) or not (
                    fault["items"] is None or isinstance(fault["items"], list)):
                raise ValueError(f"{where}: 'also' and 'mismatched_files' are lists, 'items' a list or null")
    return golds


def matches(finding: dict, fault: dict) -> bool:
    if finding["class"] != fault["class"]:
        return False
    if sorted([finding["file"]] + list(finding["also"])) != sorted([fault["file"]] + list(fault["also"])):
        return False
    return fault["items"] is None or finding["item"] in fault["items"]


def declared_scope(instance_dir: str) -> list | None:
    """The parts an instance declares, read here without the lab; ``None`` when that cannot be read."""
    try:
        doc = json.loads(fsx.read_bytes(os.path.join(instance_dir, "instance.json")).decode("utf-8"))
        scope = doc.get("scope", list(ALL_SCOPE))
    except (OSError, ValueError, AttributeError):
        return None
    return scope if isinstance(scope, list) and scope and all(part in ALL_SCOPE for part in scope) else None


def _ask(reference, instance_dir: str):
    try:
        return reference(instance_dir)
    except _REFERENCE_ERRORS:
        return None


def reference_view(instance_dir: str) -> dict:
    """What the three references say about one instance, in gold vocabulary.

    A part outside the declared scope is not judged. A part that is in scope but that its reference
    cannot read is listed in ``unread``: nobody vouches for it, so the lab is not allowed to have
    called it good (see ``score``). Every generated instance has the three parts, all judged.
    """
    view = {"findings": [], "edges": [], "a_differing": None, "b_differing": None, "secondary_stale": None,
            "judged": [], "unread": []}
    scope = declared_scope(instance_dir)
    if scope is None:
        return view
    if "routing" in scope:
        routing, world = _ask(reference_routes.check, instance_dir), _ask(reference_reach.matrix, instance_dir)
        if routing is None or world is None:
            view["unread"].append("routing")
        else:
            view["findings"] += routing
            view["edges"] = world["admin_public_edges"]
            view["judged"].append("routing")
    if "health" in scope:
        health = _ask(reference_reach.check, instance_dir)
        if health is None:
            view["unread"].append("health")
        else:
            view["findings"] += health
            view["judged"].append("health")
    if "backup" in scope:
        hashes = _ask(reference_hashes.check, instance_dir)
        if hashes is None:
            view["unread"].append("backup")
        else:
            view.update({"a_differing": hashes["repos"]["a"]["differing"], "b_differing": hashes["repos"]["b"]["differing"],
                         "secondary_stale": hashes["secondary_stale"]})
            view["judged"].append("backup")
    return view


def gold_agrees_with_references(gold: dict, ref: dict) -> list:
    """Disagreements between the fault plan and the references, for the classes the references cover."""
    problems = []
    covered = ("route_missing_service", "route_duplicate_shadow", "admin_on_public", "health_probes_process", "restart_loop")
    planted = [f for f in gold["faults"] if f["class"] in covered]
    for fault in planted:
        if not any(r["class"] == fault["class"] and sorted(r["files"]) == sorted([fault["file"]] + fault["also"])
                   and (fault["items"] is None or r["item"] in fault["items"]) for r in ref["findings"]):
            problems.append(f"planted {fault['class']} not seen by the reference")
    for r in ref["findings"]:
        if not any(r["class"] == f["class"] and sorted(r["files"]) == sorted([f["file"]] + f["also"])
                   and (f["items"] is None or r["item"] in f["items"]) for f in planted):
            problems.append(f"reference reports {r['class']} that was not planted")
    expected = sorted({p for f in gold["faults"] if f["class"] in DATA_CLASSES for p in f["mismatched_files"]})
    if ref["a_differing"] != expected:
        problems.append("files differing after a naive restore of A are not the planted ones")
    if bool(ref["secondary_stale"]) != any(f["class"] == "repo_b_stale" for f in gold["faults"]):
        problems.append("secondary staleness differs from the plan")
    return problems


def score(golds: list, corpus_dir: str, work_dir: str, details_path: str | None) -> dict:
    classes = {c: {"planted": 0, "detected": 0, "localised": 0} for c in G.CLASSES}
    out = {"instances": len(golds), "clean": sum(1 for g in golds if g["clean"]),
           "planted_faults": sum(len(g["faults"]) for g in golds), "verdict_correct": 0,
           "false_alarms_on_clean": 0, "spurious_findings": 0, "spurious_by_class": {},
           "restore_lists_planted": 0, "restore_lists_exact": 0, "never_event_restore": 0,
           "never_event_admin_public": 0, "gold_reference_disagreements": 0, "reference_covered_instances": 0}
    details = []
    for gold in golds:
        inst = os.path.join(corpus_dir, gold["instance"])
        report = lab_run.audit(inst, os.path.join(work_dir, gold["instance"]))
        ref = reference_view(inst)
        findings = report["findings"]
        row = {"instance": gold["instance"], "gold_verdict": gold["verdict"], "verdict": report["verdict"],
               "gold": [[f["class"], f["variant"]] for f in gold["faults"]], "decoys": gold.get("decoys", []),
               "findings": [[f["class"], f["file"], f["item"], f["rule"]] for f in findings], "notes": []}
        out["verdict_correct"] += report["verdict"] == gold["verdict"]
        if gold["clean"] and report["verdict"] != "OK":
            out["false_alarms_on_clean"] += 1
            row["notes"].append("false alarm on a clean instance")
        for fault in gold["faults"]:
            c = classes.setdefault(fault["class"], {"planted": 0, "detected": 0, "localised": 0})   # hand-written labels may name other classes
            c["planted"] += 1
            c["detected"] += any(f["class"] == fault["class"] for f in findings)
            hit = any(matches(f, fault) for f in findings)
            c["localised"] += hit
            if not hit:
                row["notes"].append(f"not localised: {fault['class']}")
            if fault["class"] in DATA_CLASSES:
                out["restore_lists_planted"] += 1
                exact = (report.get("drill") or {}).get("mismatched_files") == fault["mismatched_files"]
                out["restore_lists_exact"] += exact
                if not exact:
                    row["notes"].append("list of differing files not exact")
        for f in findings:
            if not any(matches(f, fault) for fault in gold["faults"]):
                out["spurious_findings"] += 1
                out["spurious_by_class"][f["class"]] = out["spurious_by_class"].get(f["class"], 0) + 1
                row["notes"].append(f"spurious: {f['class']} {f['file']} {f['item']}")
        drill, fallback = report.get("drill"), report.get("fallback")
        unvouched = "backup" in ref["unread"]      # the reference could not read it: "ok" is not acceptable either
        if (drill and drill["result"] == "ok" and (unvouched or ref["a_differing"])) or (
                fallback and fallback["result"] == "ok" and (unvouched or ref["b_differing"])):
            out["never_event_restore"] += 1
            row["notes"].append("NEVER-EVENT: restore declared ok while the reference finds differing files")
        accepted = (report.get("routes") or {}).get("accepted")
        if accepted and ("routing" in ref["unread"] or ref["edges"]
                         or any(r["class"] == "admin_on_public" for r in ref["findings"])):
            out["never_event_admin_public"] += 1
            row["notes"].append("NEVER-EVENT: table accepted while the reference finds an admin-public edge")
        if sorted(ref["judged"]) == sorted(ALL_SCOPE):
            out["reference_covered_instances"] += 1
            problems = gold_agrees_with_references(gold, ref)
            if problems:
                out["gold_reference_disagreements"] += 1
                row["notes"].extend("gold/reference: " + p for p in problems)
        else:
            row["notes"].append("references judged: " + (", ".join(ref["judged"]) or "nothing")
                                + ("; could not read: " + ", ".join(ref["unread"]) if ref["unread"] else ""))
        details.append(row)
    out["classes"] = classes
    planted = sum(c["planted"] for c in classes.values())
    out["detected"] = sum(c["detected"] for c in classes.values())
    out["localised"] = sum(c["localised"] for c in classes.values())
    out["recall_detected"] = round(out["detected"] / planted, 4) if planted else None
    out["recall_localised"] = round(out["localised"] / planted, 4) if planted else None
    out["verdict_accuracy"] = round(out["verdict_correct"] / len(golds), 4) if golds else None
    if details_path:
        jsonio.write_jsonl(details_path, details)
    return out


def table(name: str, m: dict) -> str:
    lines = [f"suite {name}: {m['instances']} instances ({m['clean']} clean), {m['planted_faults']} planted faults",
             f"  {'class':<30}{'planted':>8}{'detected':>10}{'localised':>11}"]
    for cls, c in m["classes"].items():
        lines.append(f"  {cls:<30}{c['planted']:>8}{c['detected']:>10}{c['localised']:>11}")
    lines += [f"  detected {m['detected']}/{m['planted_faults']} ({m['recall_detected']}), "
              f"localised {m['localised']}/{m['planted_faults']} ({m['recall_localised']})",
              f"  verdict correct {m['verdict_correct']}/{m['instances']} ({m['verdict_accuracy']})",
              f"  false alarms on clean {m['false_alarms_on_clean']}/{m['clean']}, "
              f"spurious findings {m['spurious_findings']} {m['spurious_by_class'] or ''}",
              f"  restore lists exact {m['restore_lists_exact']}/{m['restore_lists_planted']}",
              f"  never-event restore {m['never_event_restore']}, never-event admin-public {m['never_event_admin_public']}",
              f"  gold/reference disagreements {m['gold_reference_disagreements']}/{m['reference_covered_instances']}"]
    return "\n".join(lines)


def _commit() -> str:
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "[TO CONFIRM]"


def main(argv=None) -> int:
    offline.enforce()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", required=True, choices=["dev", "holdout", "stress", "blind"])
    ap.add_argument("--seed", type=int, help="blind only: a seed never used before")
    ap.add_argument("--runner", help="blind only: who runs it (a different hand from the author)")
    ap.add_argument("--date", help="blind only: date of the run, YYYY-MM-DD (the clock is not read)")
    ap.add_argument("--styles", default="", help="blind only: comma-separated styles (see corpus/yamlout.py)")
    ap.add_argument("--faults-per-instance", type=int, choices=(1, 2), default=1, help="blind only")
    ap.add_argument("--handwritten", help="blind only: directory with hand-written instances and labels.jsonl")
    ap.add_argument("--count", type=int, default=G.COUNT, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    history = jsonio.read(HISTORY) if fsx.exists(HISTORY) else {"runs": []}
    commit = None
    if args.suite == "blind":
        if args.seed is None or not args.runner or not args.date:
            ap.error("a blind run needs --seed, --runner and --date")
        used = {s["seed"] for s in G.SUITES.values()} | {r.get("seed") for r in history["runs"]}
        if args.seed in used:
            ap.error("this seed has already been used: a blind run needs a fresh one")
        commit = _commit()
        # once per measured commit; when the commit cannot be read, any recorded blind run refuses
        if any(r.get("suite") == "blind" and (r.get("commit") == commit or commit == "[TO CONFIRM]")
               for r in history["runs"]):
            ap.error("a blind run of this commit is already recorded in eval/history.json: it is done once per frozen commit")
        styles = [s for s in args.styles.split(",") if s]
        unknown = [s for s in styles if s not in yamlout.ALL_STYLES + ["tz-alias"]]
        if unknown:
            ap.error(f"unknown style(s): {', '.join(unknown)}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date):
            ap.error("--date must be written YYYY-MM-DD")
        seed, per, count = args.seed, args.faults_per_instance, args.count
    else:
        if args.handwritten:
            ap.error("--handwritten goes with --suite blind")
        spec = G.SUITES[args.suite]
        seed, per, styles, count = spec["seed"], spec["faults"], spec["styles"], G.COUNT

    extra = []
    if args.handwritten:
        try:
            extra = load_handwritten(args.handwritten, args.suite + "-")
        except (ValueError, OSError) as exc:
            ap.error(f"hand-written instances: {exc}")

    base = os.path.join(BUILD, args.suite)
    corpus_dir, work_dir = os.path.join(base, "corpus"), os.path.join(base, "work")
    fsx.rmtree(work_dir)
    golds = G.generate(seed, args.suite, count, per, styles, corpus_dir)
    for gold in extra:
        fsx.copytree(os.path.join(args.handwritten, gold["instance"]), os.path.join(corpus_dir, gold["instance"]))
    golds += extra
    handwritten = len(extra)
    keep_details = args.suite in ("dev", "stress", "blind")
    metrics = score(golds, corpus_dir, work_dir, os.path.join(base, "details.jsonl") if keep_details else None)
    metrics.update({"seed": seed, "faults_per_instance": per, "styles": styles, "handwritten": handwritten})
    if args.suite == "holdout":
        fsx.rmtree(corpus_dir)   # never inspected: nothing of it is left on disk
        fsx.rmtree(work_dir)

    results = jsonio.read(RESULTS) if fsx.exists(RESULTS) else {}
    results[args.suite] = metrics
    jsonio.write(RESULTS, results)
    print(table(args.suite, metrics))
    if args.suite == "blind":
        history["runs"].append({"suite": "blind", "when": args.date, "runner": args.runner, "seed": seed,
                                "commit": commit, "metrics": metrics,
                                "note": "blind run after the freeze: seed and styles chosen by the runner"})
        jsonio.write(HISTORY, history)
        print("recorded in eval/history.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
