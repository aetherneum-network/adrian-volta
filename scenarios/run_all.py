"""Run the ten scenarios S01-S10 in order and print one line each.

    python scenarios/run_all.py [--json reports/scenarios.json]

Exit code 0 only if all ten pass. Each scenario can also be run alone from its own folder with
``python check.py``. Offline: sockets are blocked before anything else runs. The JSON report
contains no duration and no path outside the repository, so two runs give the same bytes.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _common  # noqa: E402,F401  (puts the repository root on the path and blocks sockets)
from lab import jsonio  # noqa: E402

IDS = [f"S{n:02d}" for n in range(1, 11)]


def load(sid: str):
    path = os.path.join(HERE, sid, "check.py")
    spec = importlib.util.spec_from_file_location(f"scenario_{sid}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def run(ids=IDS, quiet: bool = False) -> list:
    results = []
    for sid in ids:
        started = time.monotonic()
        try:
            ok, line, details = load(sid)()
        except Exception as exc:  # a checker that crashes is a failed scenario, said as such
            ok, line = False, f"{sid} FAIL - checker raised {type(exc).__name__}: {exc}"
            details = {"scenario": sid, "pass": False, "summary": line, "checks": [], "actual": None}
        if not quiet:
            print(f"{line} [{time.monotonic() - started:.1f}s]")
        results.append(details)
    return results


def summary(results: list) -> dict:
    """The deterministic part of the results: no duration, no path."""
    return {"scenarios": [{"scenario": r["scenario"], "pass": r["pass"], "summary": r["summary"],
                           "checks": len(r["checks"]),
                           "checks_failed": [c["check"] for c in r["checks"] if not c["pass"]]} for r in results],
            "passed": sum(1 for r in results if r["pass"]), "total": len(results)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="write the results (without durations) to this file")
    args = ap.parse_args(argv)
    results = run()
    passed = sum(1 for r in results if r["pass"])
    print(f"Scenarios: {passed}/{len(results)} PASS")
    if args.json:
        jsonio.write(args.json, summary(results))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
