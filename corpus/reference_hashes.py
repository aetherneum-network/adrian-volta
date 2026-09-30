"""Reference 3 of 3 - direct SHA-256 of the source tree against a naive restore.

Independent of the code under test. It hashes every file of the source tree, rebuilds the newest
snapshot of each repository in memory by plain concatenation of the byte ranges the index points
to (no block verification at all) and compares the two trees path by path. A file is reported
when it is in the source and not rebuilt, rebuilt and not in the source, or rebuilt with another
SHA-256. This is the judge of the never-event: a restore may be called successful only when this
list is empty.

Covers: the list of files that differ after a restore (``backup_truncated``, ``block_corrupt``,
``job_order_inverted``) and the lag of the secondary repository (``repo_b_stale``).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

from corpus import reference_routes as RR

_BS = chr(92)
DEFAULT_MAX_LAG_HOURS = 26


def _long(path: str) -> str:
    p = os.path.abspath(path)
    return (_BS * 2 + "?" + _BS + p) if os.name == "nt" and not p.startswith(_BS * 2) else p


def source_hashes(source_dir: str) -> dict:
    out = {}
    base = _long(source_dir)
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            full = os.path.join(dirpath, name)
            with open(full, "rb") as fh:
                out[full[len(base) + 1:].replace(_BS, "/")] = hashlib.sha256(fh.read()).hexdigest()
    return out


def newest_snapshot(repo_dir: str):
    folder = _long(os.path.join(repo_dir, "snapshots"))
    if not os.path.isdir(folder):
        return None
    names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    if not names:
        return None
    with open(os.path.join(folder, names[-1]), "r", encoding="utf-8") as fh:
        return json.load(fh)


def naive_restore(repo_dir: str, snapshot: dict) -> dict:
    """Path -> SHA-256 of the bytes the index points to, for every listed file."""
    with open(_long(os.path.join(repo_dir, "index.json")), "r", encoding="utf-8") as fh:
        blocks = json.load(fh)["blocks"]
    with open(_long(os.path.join(repo_dir, "pack.bin")), "rb") as fh:
        pack = fh.read()
    out = {}
    for entry in snapshot["files"]:
        data = b""
        for address in entry["blocks"]:
            offset, length = blocks.get(address, [len(pack), 0])
            data += pack[offset:offset + length]
        out[entry["path"]] = hashlib.sha256(data).hexdigest()
    return out


def differing(source: dict, restored: dict) -> list:
    return sorted(path for path in set(source) | set(restored) if source.get(path) != restored.get(path))


def check(instance_dir: str) -> dict:
    policy = RR.read_yaml(os.path.join(instance_dir, "policy", "backup.yaml"))
    source = source_hashes(os.path.join(instance_dir, policy["source"]))
    out = {"source_files": len(source), "repos": {}}
    created = {}
    for name in ("a", "b"):
        repo_dir = os.path.join(instance_dir, policy["repos"][name]["path"])
        snapshot = newest_snapshot(repo_dir)
        if snapshot is None:
            out["repos"][name] = {"snapshot": None, "differing": sorted(source)}
            continue
        created[name] = dt.datetime.strptime(snapshot["created"], "%Y-%m-%dT%H:%M:%SZ")
        out["repos"][name] = {"snapshot": snapshot["id"], "differing": differing(source, naive_restore(repo_dir, snapshot))}
    max_lag = int(policy["repos"]["b"].get("max_lag_hours") or DEFAULT_MAX_LAG_HOURS)
    if "b" not in created:
        out["secondary_stale"] = True
    elif "a" in created:
        out["secondary_stale"] = (created["a"] - created["b"]).total_seconds() / 3600 > max_lag
    else:
        out["secondary_stale"] = None
    return out
