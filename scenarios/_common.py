"""Helpers shared by the scenario checkers S01-S10 (synthetic, offline, deterministic).

A checker is run from its own folder with ``python check.py`` and nothing else: no arguments,
no environment variables. ``as_of``, dates and content seeds live in ``input/``. Everything it
writes goes under ``build/scenarios/<id>/``.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
import sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lab import backup, drill, fsx, jsonio, offline, timeutil, yamlio  # noqa: E402
from lab import run as lab_run  # noqa: E402

offline.enforce()
BUILD = os.path.join(ROOT, "build", "scenarios")
STEP_SECONDS = {"export": 60, "backup_a": 30, "backup_b": 30, "verify": 20, "prune": 5}
GAP_SECONDS = 5


def scenario_dir(file: str) -> str:
    return os.path.dirname(os.path.abspath(file))


def workdir(sid: str) -> str:
    path = os.path.join(BUILD, sid)
    fsx.rmtree(path)
    fsx.makedirs(path)
    return path


def copy_input(scn: str, rel: str, dest: str) -> str:
    """Copy a committed input tree into the work directory (inputs are never modified in place)."""
    fsx.copytree(os.path.join(scn, "input", rel), dest)
    return dest


def read_json(scn: str, rel: str):
    return jsonio.read(os.path.join(scn, rel))


def stream(seed: str, size: int) -> bytes:
    """Deterministic bytes from a seed: SHA-256 in counter mode. No random module, no clock."""
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{seed}:{counter}".encode("utf-8")).digest()
        counter += 1
    return bytes(out[:size])


def write_tree(source_dir: str, files: list) -> None:
    """Materialise ``[{"path", "size", "seed"}]`` (paths may exceed 260 characters; they are built here, not committed)."""
    for spec in files:
        fsx.write_bytes(fsx.join(source_dir, spec["path"]), stream(spec["seed"], spec["size"]))


def night(inst: str, policy: dict, spec: dict, trace: list) -> dict:
    """Run one night of the chain, in the order written in the policy, on the virtual clock.

    ``spec``: ``run`` (date), ``start`` (UTC), ``export_seed``, optional ``changes`` (files
    rewritten during the day), ``skip`` (steps that do not happen and are not logged),
    ``claimed_not_done`` (steps the job log reports as ok although nothing was done).
    The export step writes the export file; each backup step snapshots the source *as it is at
    that moment*. That is the whole point of the chain: order decides what is saved.
    """
    source = fsx.join(inst, policy["source"])
    write_tree(source, spec.get("changes", []))
    t = timeutil.parse_utc(spec["start"])
    skip = set(spec.get("skip", []))
    claimed = set(spec.get("claimed_not_done", []))   # logged as ok, never performed
    export_rel = policy["export_path"][len(policy["source"]) + 1:]
    out = {}
    for step in policy["chain"]:
        if step in skip:
            continue
        start = t + dt.timedelta(seconds=GAP_SECONDS)
        end = start + dt.timedelta(seconds=STEP_SECONDS[step])
        if step in claimed:
            pass
        elif step == "export":
            fsx.write_bytes(fsx.join(source, export_rel), stream(spec["export_seed"], spec.get("export_size", 1800)))
        elif step in ("backup_a", "backup_b"):
            repo = fsx.join(inst, policy["repos"][step[-1]]["path"])
            out[step] = backup.backup(source, repo, start)["id"]
        elif step == "verify":
            result = drill.run(source, fsx.join(inst, policy["repos"]["a"]["path"]), "a",
                               fsx.join(inst, ".scratch"), end, snapshot=out.get("backup_a"))
            drill.append(fsx.join(inst, drill.FILE), result)
            fsx.rmtree(fsx.join(inst, ".scratch"))
        trace.append({"kind": "job", "run": spec["run"], "step": step, "start": timeutil.fmt(start),
                      "end": timeutil.fmt(end), "status": "ok"})
        t = end
    return out


def build_backup_instance(scn: str, inst: str, variant: str = "instance", events: str | None = None) -> dict:
    """Instance for the backup scenarios: committed policy and tree spec, nights replayed by ``night``.

    ``variant`` names the committed instance folder under ``input/``; ``events`` an optional
    JSON-lines file of simulation events (host off/on, for example) added to the trace.
    """
    copy_input(scn, variant, inst)
    policy, problems = backup.load_policy(inst)
    if policy is None:
        raise ValueError(f"scenario policy unreadable: {problems}")
    write_tree(fsx.join(inst, policy["source"]), read_json(scn, "input/tree.json"))
    trace: list = []
    ids = [night(inst, policy, spec, trace) for spec in read_json(scn, "input/nights.json")]
    if events:
        trace = jsonio.read_jsonl(os.path.join(scn, "input", events)) + trace
    jsonio.write_jsonl(fsx.join(inst, "trace.jsonl"), trace)
    return {"policy": policy, "nights": ids}


def console_says_ok(text: str) -> bool:
    """True only when the console summary contains the words an operator reads as success."""
    return "RUN OK" in text


def audit(inst: str, work: str) -> tuple[dict, str]:
    """Audit an instance; returns the report and the console text (what an operator would read)."""
    report = lab_run.audit(inst, work)
    return report, lab_run.render(report)


def cli(inst: str, work: str, keep_restore: bool = False) -> tuple[int, str]:
    """The command line entry point, captured: exit code and console text."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = lab_run.main(["--instance", inst, "--work", work] + (["--keep-restore"] if keep_restore else []))
    return code, buf.getvalue()


def brief(report: dict) -> list:
    return [{"class": f["class"], "verdict": f["verdict"], "file": f["file"], "also": f["also"], "item": f["item"],
             "rule": f["rule"]} for f in report["findings"]]


class Checks:
    """Named assertions; a scenario passes only if every one of them holds."""

    def __init__(self):
        self.rows = []

    def true(self, label: str, condition) -> bool:
        self.rows.append({"check": label, "pass": bool(condition)})
        return bool(condition)

    def eq(self, label: str, got, want) -> bool:
        ok = got == want
        row = {"check": label, "pass": ok}
        if not ok:
            row.update({"got": got, "want": want})
        self.rows.append(row)
        return ok

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(r["pass"] for r in self.rows)

    def failed(self) -> list:
        return [r["check"] for r in self.rows if not r["pass"]]


def pinned(scn: str, checks: Checks, actual: dict) -> None:
    """Compare the deterministic summary with ``expected/expected.json`` (byte-stable across runs and systems)."""
    path = os.path.join(scn, "expected", "expected.json")
    if "--pin" in sys.argv:          # author tool: rewrite the pinned values after a reviewed change
        jsonio.write(path, actual)
    checks.eq("summary equals expected/expected.json", actual, jsonio.read(path))


def report(sid: str, checks: Checks, summary: str, actual: dict) -> tuple[bool, str, dict]:
    ok = checks.ok
    if not ok:
        summary = "failed: " + "; ".join(checks.failed())
    details = {"scenario": sid, "pass": ok, "summary": summary, "checks": checks.rows, "actual": actual}
    jsonio.write(os.path.join(BUILD, sid, "result.json"), details)
    return ok, f"{sid} {'PASS' if ok else 'FAIL'} - {summary}", details


def main(check) -> None:
    ok, line, _ = check()
    print(line)
    sys.exit(0 if ok else 1)
