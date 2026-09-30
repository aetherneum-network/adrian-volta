"""Restore drill and its append-only registry.

The never-event of this pack: *a restore declared successful while even one file's hash
differs from the source manifest.* The guard is structural:

* ``DrillResult`` has no field that says "ok". ``ok`` is a property computed from the evidence:
  the source has files, every count reconciles (source = declared by the snapshot = restored on
  disk), no problem was met while rebuilding, and no path is missing, extra or different.
* the comparison is made against the source tree as hashed at drill time, and against the
  restored files as re-read from disk;
* when anything is unknown (unreadable repository, empty source) the result is ``failed``.

The registry is one JSON line per drill, chained by SHA-256 (``prev`` -> ``hash``). Lines are
only appended. A line edited afterwards breaks the chain, and a broken chain vouches for nothing.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from lab import fsx, jsonio, restore as R, timeutil
from lab.backup import Repo, RepoError

GENESIS = "0" * 64
FILE = "drills.jsonl"


class RegistryError(Exception):
    """The registry cannot be appended to (its chain does not verify)."""


@dataclass(frozen=True)
class DrillResult:
    repo: str
    snapshot: str | None
    as_of: str
    source_count: int
    declared_count: int | None
    restored_count: int
    mismatched: tuple          # of (path, reason)
    problems: tuple            # of (kind, file)
    source_manifest_sha256: str | None

    @property
    def ok(self) -> bool:
        return (self.snapshot is not None
                and self.source_count > 0
                and self.declared_count is not None
                and self.source_count == self.declared_count == self.restored_count
                and not self.problems
                and not self.mismatched)

    @property
    def result(self) -> str:
        return "ok" if self.ok else "failed"

    @property
    def mismatched_files(self) -> list[str]:
        return sorted({path for path, _ in self.mismatched})

    def as_dict(self) -> dict:
        return {"repo": self.repo, "snapshot": self.snapshot, "as_of": self.as_of, "result": self.result,
                "source_count": self.source_count, "declared_count": self.declared_count,
                "restored_count": self.restored_count, "mismatched_files": self.mismatched_files,
                "mismatched": [{"path": p, "reason": r} for p, r in self.mismatched],
                "problems": [{"kind": k, "file": f} for k, f in self.problems],
                "source_manifest_sha256": self.source_manifest_sha256}


def run(source_dir, repo_dir, repo_name: str, scratch_dir, as_of: dt.datetime, snapshot: str | None = None) -> DrillResult:
    """Restore the newest (or the given) snapshot into an empty scratch directory and verify it."""
    manifest = R.source_manifest(source_dir)
    repo = Repo(repo_dir)
    problems: list[tuple] = []
    sid = snapshot
    if sid is None:
        ids = repo.snapshot_ids()
        sid = ids[-1] if ids else None
    if sid is None:
        problems.append(("no_snapshot", None))
        return DrillResult(repo_name, None, timeutil.fmt(as_of), manifest["count"], None, 0, (), tuple(problems),
                           manifest["sha256"])
    fsx.rmtree(scratch_dir)
    fsx.makedirs(scratch_dir)
    rebuilt = R.restore(repo, sid, scratch_dir)
    problems.extend((p["kind"], p.get("file")) for p in rebuilt["problems"])
    checked = R.verify(scratch_dir, manifest)
    return DrillResult(repo_name, sid, timeutil.fmt(as_of), manifest["count"], rebuilt["declared_count"],
                       checked["restored_count"], tuple((m["path"], m["reason"]) for m in checked["mismatched"]),
                       tuple(problems), manifest["sha256"])


def facts(result: DrillResult, order_inverted: bool) -> dict:
    """Facts for ``rules/backup_policy.json`` (``drill_rules``). Counting only - no decision here."""
    kinds = [k for k, _ in result.problems]
    truncating = ("block_missing", "listing_short", "size_short")
    known = ("block_corrupt", "repo_unreadable", "no_snapshot") + truncating
    return {
        "ok": result.ok,
        "repo_readable": "repo_unreadable" not in kinds,
        "has_snapshot": result.snapshot is not None,
        "source_files": result.source_count,
        "blocks_corrupt": kinds.count("block_corrupt"),
        "truncated": any(k in truncating for k in kinds),
        "mismatched": len(result.mismatched),
        "order_inverted": order_inverted,
        "other_problems": len([k for k in kinds if k not in known]),
    }


# ----------------------------------------------------------------------------- registry

def _entry_hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return jsonio.sha256_bytes(jsonio.line(body).encode("utf-8"))


def read_registry(path) -> dict:
    """Read and verify the chain. ``chain_ok`` is False as soon as one line does not verify."""
    out = {"entries": [], "chain_ok": True, "problem": None, "head": GENESIS}
    if not fsx.exists(path):
        return out
    try:
        lines = [ln for ln in fsx.read_bytes(path).decode("utf-8").splitlines() if ln.strip()]
    except (OSError, UnicodeDecodeError):
        return dict(out, chain_ok=False, problem="registry unreadable")
    prev = GENESIS
    for number, raw in enumerate(lines, start=1):
        try:
            entry = json.loads(raw)
        except ValueError:
            return dict(out, chain_ok=False, problem=f"line {number}: not JSON")
        if (not isinstance(entry, dict) or entry.get("seq") != number or entry.get("prev") != prev
                or entry.get("hash") != _entry_hash(entry) or entry.get("result") not in ("ok", "failed")):
            return dict(out, chain_ok=False, problem=f"line {number}: chain does not verify")
        prev = entry["hash"]
        out["entries"].append(entry)
    out["head"] = prev
    return out


def append(path, result: DrillResult) -> dict:
    """Append one drill to the registry. The recorded result is the computed one; it cannot be passed in."""
    state = read_registry(path)
    if not state["chain_ok"]:
        raise RegistryError(f"refusing to append to a registry whose chain does not verify ({state['problem']})")
    entry = {"seq": len(state["entries"]) + 1, "as_of": result.as_of, "repo": result.repo,
             "snapshot": result.snapshot, "result": result.result, "source_count": result.source_count,
             "restored_count": result.restored_count, "mismatched": result.mismatched_files,
             "problems": len(result.problems), "source_manifest_sha256": result.source_manifest_sha256,
             "prev": state["head"]}
    entry["hash"] = _entry_hash(entry)
    fsx.append_bytes(path, (jsonio.line(entry) + "\n").encode("utf-8"))
    check = read_registry(path)
    if not check["chain_ok"] or check["head"] != entry["hash"]:
        raise RegistryError("registry did not verify after the append")
    return entry


def verified(state: dict, repo_name: str) -> set:
    """Snapshots whose *latest* drill on record succeeded. A broken chain verifies nothing."""
    if not state["chain_ok"]:
        return set()
    latest: dict = {}
    for entry in state["entries"]:
        if entry.get("repo") == repo_name and entry.get("snapshot"):
            latest[entry["snapshot"]] = entry["result"]
    return {sid for sid, res in latest.items() if res == "ok"}
