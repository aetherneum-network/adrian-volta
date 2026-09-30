"""Restore drill and its append-only registry.

The never-event of this pack: *a restore declared successful while even one file's hash
differs from the source manifest.* The guard is structural:

* ``DrillResult`` has no field that says "ok". ``ok`` is a property computed from the evidence:
  the source has files, every count reconciles (source = declared by the snapshot = restored on
  disk), no problem was met while rebuilding, and no path is missing, extra or different.
* the comparison is made against the source tree as hashed at drill time, and against the
  restored files as re-read from disk;
* the drill also reads its whole repository (``backup.inspect``): every index entry, every byte
  of the pack, every snapshot manifest. A drill without that complete read, or with any part
  that could not be read or verified, is not ok - even when the newest snapshot restored well;
* when anything is unknown (unreadable repository, empty source) the result is ``failed``.

``ScopeResult`` is the drill of the backup part of an instance: the primary drill *and* the
complete read of the secondary repository. It is ok only when every backup part in scope was read
completely and verified, so an ok never leaves the secondary unsaid.

The registry is one JSON line per drill, chained by SHA-256 (``prev`` -> ``hash``). Lines are
only appended. A line edited afterwards breaks the chain, and a broken chain vouches for nothing.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from lab import backup as B
from lab import fsx, jsonio, restore as R, timeutil
from lab.backup import Repo, RepoError

GENESIS = "0" * 64
FILE = "drills.jsonl"
UNREADABLE = ("repo_unreadable", "pack_unreadable") + B.UNREADABLE_KINDS
TRUNCATING = ("block_missing", "listing_short", "size_short")


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
    problems: tuple            # of (kind, file): met while restoring the snapshot
    source_manifest_sha256: str | None
    inspection: dict | None = None   # backup.inspect of the whole repository; None = not read = not ok

    @property
    def parts(self) -> tuple:
        """(kind, location) of every part of the repository that could not be read or verified."""
        if self.inspection is None:
            return (("repo_unread", None),)
        return tuple((p["kind"], p["location"]) for p in self.inspection["problems"])

    @property
    def ok(self) -> bool:
        return (self.snapshot is not None
                and self.source_count > 0
                and self.declared_count is not None
                and self.source_count == self.declared_count == self.restored_count
                and not self.problems
                and not self.mismatched
                and self.inspection is not None
                and self.inspection["verified"]
                and not self.parts)

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
                "parts": [{"kind": k, "location": loc} for k, loc in self.parts],
                "source_manifest_sha256": self.source_manifest_sha256}


def run(source_dir, repo_dir, repo_name: str, scratch_dir, as_of: dt.datetime, snapshot: str | None = None) -> DrillResult:
    """Read the whole repository, restore the newest (or the given) snapshot into an empty scratch
    directory and verify it against the source tree."""
    problems: list[tuple] = []
    try:
        manifest = R.source_manifest(source_dir)
    except OSError:              # a source folder or file that cannot be read: no manifest, said
        manifest = {"files": {}, "count": 0, "bytes": 0, "sha256": None}
        problems.append(("source_unreadable", None))
    repo = Repo(repo_dir)
    inspection = B.inspect(repo)
    sid = snapshot
    if sid is None:
        ids = inspection["snapshots"]["listed"]
        sid = ids[-1] if ids else None
    if sid is None:
        problems.append(("no_snapshot", None))
        return DrillResult(repo_name, None, timeutil.fmt(as_of), manifest["count"], None, 0, (), tuple(problems),
                           manifest["sha256"], inspection)
    fsx.rmtree(scratch_dir)
    fsx.makedirs(scratch_dir)
    rebuilt = R.restore(repo, sid, scratch_dir)
    problems.extend((p["kind"], p.get("file")) for p in rebuilt["problems"])
    checked = R.verify(scratch_dir, manifest)
    return DrillResult(repo_name, sid, timeutil.fmt(as_of), manifest["count"], rebuilt["declared_count"],
                       checked["restored_count"], tuple((m["path"], m["reason"]) for m in checked["mismatched"]),
                       tuple(problems), manifest["sha256"], inspection)


def facts(result: DrillResult, order_inverted: bool) -> dict:
    """Facts for ``rules/backup_policy.json`` (``drill_rules``). Counting only - no decision here.

    The kinds come from the restore of the snapshot *and* from the complete read of the repository.
    """
    kinds = [k for k, _ in result.problems] + [k for k, _ in result.parts]
    known = ("block_corrupt", "no_snapshot", "source_unreadable", "repo_unread") + UNREADABLE + TRUNCATING
    return {
        "ok": result.ok,
        "repo_readable": not any(k in UNREADABLE or k == "repo_unread" for k in kinds),
        "has_snapshot": result.snapshot is not None,
        "source_files": result.source_count,
        "blocks_corrupt": kinds.count("block_corrupt"),
        "truncated": any(k in TRUNCATING for k in kinds),
        "mismatched": len(result.mismatched),
        "order_inverted": order_inverted,
        "other_problems": len([k for k in kinds if k not in known]),
    }


# ----------------------------------------------------------------------------- the backup part as a whole

def repository_view(role: str, path: str, inspection: dict | None) -> dict:
    """What was read and verified in one repository, said in the report (locations are instance-relative)."""
    if inspection is None:
        return {"role": role, "path": path, "present": None, "verified": False, "index": None, "pack": None,
                "snapshots": None, "problems": [{"kind": "repo_unread", "location": path, "note": "the repository was not read"}],
                "statement": "NOT verified: the repository was not read"}
    return {"role": role, "path": path, "present": inspection["present"], "verified": inspection["verified"],
            "index": inspection["index"], "pack": inspection["pack"], "snapshots": inspection["snapshots"],
            "problems": [dict(p, location=f"{path}/{p['location']}") for p in inspection["problems"]],
            "statement": B.statement(inspection, path)}


@dataclass(frozen=True)
class ScopeResult:
    """The restore drill of the backup part: the primary drill and the complete read of the secondary.

    ``ok`` only when the primary drill is ok (whole primary read and verified, newest snapshot equal
    to the source tree) *and* the secondary repository is present, read completely and verified
    (every index entry, every block of the pack, every manifest, at least one snapshot). Its age is
    judged elsewhere (``heartbeat.secondary``); a stale but readable secondary does not make the
    drill fail, it makes the run ALERT.
    """
    primary: DrillResult
    primary_path: str
    secondary: dict            # backup.inspect of the secondary repository
    secondary_path: str

    @property
    def ok(self) -> bool:
        return self.primary.ok and self.secondary["verified"]

    @property
    def result(self) -> str:
        return "ok" if self.ok else "failed"

    def repositories(self) -> dict:
        p = self.primary
        a = repository_view("primary", self.primary_path, p.inspection)
        a["compared_with_source"] = p.snapshot
        if p.snapshot is None:
            a["restore"] = "no snapshot restored"
        elif p.problems or p.mismatched or not (p.source_count == p.declared_count == p.restored_count) or not p.source_count:
            a["restore"] = (f"snapshot {p.snapshot} restored: {len(p.mismatched_files)} file(s) differ from the source tree, "
                            f"{len(p.problems)} problem(s) met, counts source/declared/restored "
                            f"{p.source_count}/{p.declared_count}/{p.restored_count}")
        else:
            a["restore"] = f"snapshot {p.snapshot} restored: {p.restored_count} file(s) equal to the source tree"
        a["verified"] = p.ok
        if a["statement"].startswith("verified") and not p.ok:
            a["statement"] = "NOT verified: " + a["restore"] + "; " + a["statement"][len("verified: "):]
        elif p.ok:
            a["statement"] += "; " + a["restore"]
        b = repository_view("secondary", self.secondary_path, self.secondary)
        b["compared_with_source"] = None
        b["restore"] = ("not compared with the source tree (the secondary may lag; its age is judged by rules A-040/A-041): "
                        "every listed file of every snapshot is rebuilt against the SHA-256 its manifest records")
        return {"a": a, "b": b}

    def as_dict(self) -> dict:
        """The primary drill's evidence, with ``result`` for the whole backup part and one view per repository."""
        out = self.primary.as_dict()
        views = self.repositories()
        unverified = []
        for name, view in sorted(views.items()):
            unverified += [dict(problem, repo=name) for problem in view["problems"]]
            if not view["verified"] and not view["problems"]:
                unverified.append({"repo": name, "kind": "not_verified", "location": view["path"], "note": view["statement"]})
        out.update({"result": self.result, "primary_result": self.primary.result, "repositories": views,
                    "unverified": unverified})
        return out


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
