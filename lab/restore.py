"""Restore a snapshot and verify it against the source manifest.

Two separate steps, on purpose:

1. ``restore`` rebuilds files from blocks. A file is written only if every block is present and
   hashes to its address, and the assembled bytes have the declared size and SHA-256.
2. ``verify`` walks what is actually on disk, hashes every restored file again and compares it
   with the manifest of the *source tree* - not with what the snapshot says about itself.
   A snapshot that is perfectly consistent with itself can still be a backup of the wrong data.
"""
from __future__ import annotations

from lab import fsx, jsonio
from lab.backup import Repo, RepoError, safe_rel


def source_manifest(source_dir) -> dict:
    """SHA-256 and size of every file of the source tree, read now (never remembered)."""
    files = {}
    for rel in fsx.walk_files(source_dir):
        full = fsx.join(source_dir, rel)
        files[rel] = {"sha256": jsonio.sha256_file(full), "size": fsx.size(full)}
    return {"files": files, "count": len(files), "bytes": sum(f["size"] for f in files.values()),
            "sha256": jsonio.tree_digest((rel, f["sha256"]) for rel, f in files.items())}


def restore(repo: Repo, sid: str, dest_dir) -> dict:
    """Rebuild snapshot ``sid`` under ``dest_dir``. Returns what was written and every problem met."""
    out = {"snapshot": sid, "declared_count": None, "listed_count": 0, "written": [], "problems": []}
    try:
        index = repo.load_index()
        snap = repo.read_snapshot(sid)
    except RepoError as exc:
        out["problems"].append({"kind": "repo_unreadable", "file": None, "note": str(exc)})
        return out
    if fsx.exists(repo.pack_path) or index["blocks"]:
        try:
            pack = fsx.read_bytes(repo.pack_path)
        except OSError as exc:   # said, never replaced by an empty pack
            out["problems"].append({"kind": "pack_unreadable", "file": None,
                                    "note": f"{type(exc).__name__}: pack.bin cannot be read while the index lists "
                                            f"{len(index['blocks'])} block(s)"})
            return out
    else:
        pack = b""                # no block listed and no pack: only empty files can be restored
    out["declared_count"] = snap["source_file_count"]
    out["listed_count"] = len(snap["files"])
    if len(snap["files"]) != snap["source_file_count"]:
        out["problems"].append({"kind": "listing_short" if len(snap["files"]) < snap["source_file_count"]
                                else "listing_inconsistent", "file": None,
                                "note": f"{len(snap['files'])} listed, {snap['source_file_count']} declared"})
    seen = set()
    for entry in snap["files"]:
        rel = entry["path"]
        if not safe_rel(rel) or rel in seen:
            out["problems"].append({"kind": "unsafe_path", "file": rel, "note": "path escapes the root or repeats"})
            continue
        seen.add(rel)
        parts, bad = [], False
        for sha in entry["blocks"]:
            loc = index["blocks"].get(sha)
            if loc is None or loc[0] + loc[1] > len(pack):
                out["problems"].append({"kind": "block_missing", "file": rel, "block": sha})
                bad = True
                continue
            chunk = pack[loc[0]:loc[0] + loc[1]]
            if jsonio.sha256_bytes(chunk) != sha:
                out["problems"].append({"kind": "block_corrupt", "file": rel, "block": sha})
                bad = True
                continue
            parts.append(chunk)
        if bad:
            continue
        data = b"".join(parts)
        if len(data) != entry["size"]:
            out["problems"].append({"kind": "size_short", "file": rel,
                                    "note": f"{len(data)} bytes in blocks, {entry['size']} declared"})
            continue
        if jsonio.sha256_bytes(data) != entry["sha256"]:
            out["problems"].append({"kind": "file_hash", "file": rel, "note": "assembled bytes differ from the manifest"})
            continue
        fsx.write_bytes(fsx.join(dest_dir, rel), data)
        out["written"].append(rel)
    return out


def verify(dest_dir, manifest: dict) -> dict:
    """Compare the tree on disk with the source manifest: path set, size and SHA-256 of every file."""
    restored = {}
    for rel in fsx.walk_files(dest_dir):
        full = fsx.join(dest_dir, rel)
        restored[rel] = {"sha256": jsonio.sha256_file(full), "size": fsx.size(full)}
    mismatched = []
    for rel in sorted(set(manifest["files"]) | set(restored)):
        want, got = manifest["files"].get(rel), restored.get(rel)
        if got is None:
            mismatched.append({"path": rel, "reason": "missing"})
        elif want is None:
            mismatched.append({"path": rel, "reason": "extra"})
        elif want["size"] != got["size"]:
            mismatched.append({"path": rel, "reason": "size"})
        elif want["sha256"] != got["sha256"]:
            mismatched.append({"path": rel, "reason": "hash"})
    return {"restored_count": len(restored), "restored_bytes": sum(f["size"] for f in restored.values()),
            "mismatched": mismatched}
