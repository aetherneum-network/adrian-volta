"""Rebuild every deterministic output of the pack into one folder and print its SHA-256.

    python tools/rebuild.py --out DIR [--count N]

Run it twice, into two different folders: the two digests must be equal. What is rebuilt:

* ``corpus/dev/``            the development suite, regenerated from its seed
* ``corpus/dev.labels.jsonl``, ``corpus/MANIFEST.sha256``   gold labels and per-instance digests
* ``audit/<instance>/``      the audit report of every instance (and the postmortem of the failed ones)
* ``eval/dev.metrics.json``, ``eval/dev.details.jsonl``     the score against the gold labels
* ``scenarios/Sxx/``         the work tree and the result of the ten scenarios
* ``scenarios.json``         one line per scenario

``REBUILD.sha256`` is the digest of all of the above: SHA-256 over the sorted lines
``<sha256 of the file>  <relative path>``. No file contains a duration, a wall-clock time or a
path outside the folder. The ``compose/`` profile is not part of the fingerprint.
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scenarios"))
sys.path.insert(0, os.path.join(ROOT, "eval"))

from corpus import generate as G  # noqa: E402
from lab import fsx, jsonio, offline  # noqa: E402

DIGEST_FILE = "REBUILD.sha256"


def digest(out: str) -> tuple[str, int]:
    rels = [rel for rel in fsx.walk_files(out) if rel != DIGEST_FILE]
    return jsonio.tree_digest((rel, jsonio.sha256_file(fsx.join(out, rel))) for rel in rels), len(rels)


def rebuild(out: str, count: int = G.COUNT, quiet: bool = False) -> dict:
    import _common          # scenario helpers (puts nothing on disk by itself)
    import run_all
    import score

    def say(text: str) -> None:
        if not quiet:
            print(text, flush=True)

    fsx.rmtree(out)
    fsx.makedirs(out)
    spec = G.SUITES["dev"]
    corpus_dir = os.path.join(out, "corpus", "dev")
    golds = G.generate(spec["seed"], "dev", count, spec["faults"], spec["styles"], corpus_dir)
    fsx.write_bytes(os.path.join(out, "corpus", "dev.labels.jsonl"), G.gold_bytes(golds))
    fsx.write_bytes(os.path.join(out, "corpus", "MANIFEST.sha256"), G.manifest_bytes("dev", golds))
    say(f"corpus: {len(golds)} instances (seed {spec['seed']})")

    metrics = score.score(golds, corpus_dir, os.path.join(out, "audit"), os.path.join(out, "eval", "dev.details.jsonl"))
    metrics.update({"seed": spec["seed"], "faults_per_instance": spec["faults"], "styles": spec["styles"], "handwritten": 0})
    jsonio.write(os.path.join(out, "eval", "dev.metrics.json"), metrics)
    say(f"audit: {metrics['localised']}/{metrics['planted_faults']} planted faults localised, "
        f"{metrics['false_alarms_on_clean']} false alarms on {metrics['clean']} clean instances")

    previous = _common.BUILD
    _common.BUILD = os.path.join(out, "scenarios")
    try:
        results = run_all.run(quiet=True)
    finally:
        _common.BUILD = previous
    passed = sum(1 for r in results if r["pass"])
    jsonio.write(os.path.join(out, "scenarios.json"), run_all.summary(results))
    say(f"scenarios: {passed}/{len(results)} PASS")

    value, files = digest(out)
    jsonio.write_text(os.path.join(out, DIGEST_FILE), f"{value}  rebuild ({files} files, {len(golds)} instances)\n")
    say(f"rebuild SHA-256: {value}  ({files} files)")
    return {"sha256": value, "files": files, "instances": len(golds), "scenarios_passed": passed,
            "scenarios": len(results), "metrics": metrics}


def main(argv=None) -> int:
    offline.enforce()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder to rebuild into (it is emptied first)")
    ap.add_argument("--count", type=int, default=G.COUNT, help="number of corpus instances (default: the full suite)")
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    if os.path.commonpath([out, ROOT]) == ROOT and not out.startswith(os.path.join(ROOT, "build")):
        ap.error("--out must be outside the repository, or under build/")
    result = rebuild(out, args.count)
    return 0 if result["scenarios_passed"] == result["scenarios"] else 1


if __name__ == "__main__":
    sys.exit(main())
