"""Content-addressed backup into a repository, policy reading, chain order and retention.

Repository layout (the same for the primary ``a`` and the secondary ``b``)::

    pack.bin                   blocks, concatenated in order of first appearance
    index.json                 {"format": 1, "block_size": n, "blocks": {sha256: [offset, length]}}
    snapshots/<id>.json        one manifest per backup: files, sizes, SHA-256, block lists
    superseded/<id>.json       manifests moved out by retention - moved, never deleted

A block is addressed by the SHA-256 of its content, so an unchanged file costs nothing on the
next night and a changed byte is visible as a wrong address. A snapshot is never overwritten.
``backup`` re-reads what it wrote before returning: a write that cannot be confirmed is a failed run.
``inspect`` reads a repository completely - every index entry, every byte of the pack, every
snapshot manifest - and returns each part it could not read or verify with its location.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from lab import fsx, jsonio, rules_engine, timeutil, yamlio
from lab.topology import as_int

POLICY_FILE = "policy/backup.yaml"
STEPS = ("export", "backup_a", "backup_b", "verify", "prune")
RULES = "backup_policy.json"
_RE_SNAPSHOT = re.compile(r"^\d{8}T\d{6}Z$")
_RE_SHA = re.compile(r"^[0-9a-f]{64}$")


class RepoError(Exception):
    """The repository cannot be read as a repository."""


class BackupError(Exception):
    """A backup could not be written and confirmed."""


def _no_repeated_keys(pairs: list) -> dict:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        repeated = sorted({k for k in keys if keys.count(k) > 1})[0]
        raise ValueError(f"repeated key {repeated[:16]!r}: two values for one name, none is chosen")
    return dict(pairs)


def read_json_strict(path):
    """Read one JSON file of a repository. A repeated key is an error, not "last one wins"."""
    raw = fsx.read_bytes(path)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_no_repeated_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{exc.msg} (character {exc.pos} of {len(exc.doc)}; file of {len(raw)} bytes)") from None


def _why(exc: Exception) -> str:
    """The reason, without any path: reports stay free of host paths and identical across folders."""
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'cannot be read'}"
    if isinstance(exc, UnicodeDecodeError):
        return f"UnicodeDecodeError: {exc.reason} at byte {exc.start}"
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def safe_rel(path) -> bool:
    """A relative POSIX path that stays inside its root."""
    if not isinstance(path, str) or not path or path.startswith("/") or chr(92) in path or ":" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


# ----------------------------------------------------------------------------- policy

def parse_policy(doc) -> tuple[dict | None, list[str]]:
    problems: list[str] = []
    if not isinstance(doc, dict):
        return None, ["policy: top level is not a mapping"]
    allowed = ("schedule", "chain", "source", "export_path", "repos", "retention")
    for key in doc:
        if not isinstance(key, str) or (key not in allowed and not key.startswith("x-")):
            problems.append(f"policy: unknown key {key!r}")
    schedule = doc.get("schedule")
    if not isinstance(schedule, dict) or "at" not in schedule or "tz" not in schedule:
        problems.append("policy: schedule needs 'at' and 'tz'")
        schedule = {}
    chain = doc.get("chain")
    if (not isinstance(chain, list) or len(set(map(str, chain))) != len(chain) or any(s not in STEPS for s in chain)
            or "export" not in chain or "backup_a" not in chain):
        problems.append(f"policy: chain must list distinct steps among {STEPS}, with export and backup_a")
        chain = []
    source = doc.get("source")
    if not safe_rel(source):
        problems.append("policy: source must be a relative path")
    export_path = doc.get("export_path")
    if export_path is not None and not safe_rel(export_path):
        problems.append("policy: export_path must be a relative path")
    repos = doc.get("repos")
    out_repos: dict = {}
    if not isinstance(repos, dict) or "a" not in repos or "b" not in repos:
        problems.append("policy: repos must define 'a' and 'b'")
        repos = {}
    for name in ("a", "b"):
        spec = repos.get(name)
        if not isinstance(spec, dict) or not safe_rel(spec.get("path")):
            problems.append(f"policy: repos.{name}.path must be a relative path")
            continue
        lag = spec.get("max_lag_hours")
        if lag is not None and (as_int(lag) is None or as_int(lag) <= 0):
            problems.append(f"policy: repos.{name}.max_lag_hours must be a positive integer")
        out_repos[name] = {"path": spec["path"], "max_lag_hours": as_int(lag) if lag is not None else None}
    retention = doc.get("retention")
    if not isinstance(retention, dict) or "keep_last" not in retention:
        problems.append("policy: retention needs 'keep_last'")
        retention = {}
    if problems:
        return None, problems
    return {"schedule": {"at": schedule["at"], "tz": schedule["tz"]}, "chain": list(chain), "source": source,
            "export_path": export_path, "repos": out_repos, "retention": {"keep_last": retention["keep_last"]}}, []


def load_policy(instance_dir) -> tuple[dict | None, list[str]]:
    doc, err = yamlio.load_file(fsx.join(instance_dir, POLICY_FILE))
    if err:
        return None, [err]
    return parse_policy(doc)


# ----------------------------------------------------------------------------- repository

class Repo:
    def __init__(self, path):
        self.path = path

    @property
    def pack_path(self) -> str:
        return fsx.join(self.path, "pack.bin")

    @property
    def index_path(self) -> str:
        return fsx.join(self.path, "index.json")

    def snapshot_path(self, sid: str) -> str:
        return fsx.join(self.path, f"snapshots/{sid}.json")

    def exists(self) -> bool:
        return fsx.isfile(self.index_path)

    def load_index(self) -> dict:
        """The whole index, or ``RepoError``: missing, empty, truncated, not JSON, a repeated key or a malformed entry."""
        try:
            index = read_json_strict(self.index_path)
        except (OSError, ValueError) as exc:
            raise RepoError(f"index unreadable: {_why(exc)}") from None
        blocks = index.get("blocks") if isinstance(index, dict) else None
        if not isinstance(blocks, dict) or as_int(index.get("block_size")) is None:
            raise RepoError("index malformed: 'blocks' must be a mapping and 'block_size' an integer")
        for sha, loc in blocks.items():
            if (not _RE_SHA.match(str(sha)) or not isinstance(loc, list) or len(loc) != 2
                    or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in loc)):
                raise RepoError(f"index malformed: entry {str(sha)[:16]!r} is not <sha256>: [offset, length]")
        return index

    def snapshot_ids(self) -> list[str]:
        """Snapshot ids in order. A snapshots folder that cannot be listed is ``RepoError``, never "no snapshot"."""
        folder = fsx.join(self.path, "snapshots")
        if fsx.exists(folder) and not fsx.isdir(folder):
            raise RepoError("snapshots is not a folder")
        try:
            rels = fsx.walk_files(folder)
        except OSError as exc:
            raise RepoError(f"snapshots folder cannot be listed: {_why(exc)}") from None
        names = [rel[:-5] for rel in rels if "/" not in rel and rel.endswith(".json")]
        return sorted(n for n in names if _RE_SNAPSHOT.match(n))

    def read_snapshot(self, sid: str) -> dict:
        try:
            snap = read_json_strict(self.snapshot_path(sid))
        except (OSError, ValueError) as exc:
            raise RepoError(f"snapshot {sid} unreadable: {_why(exc)}") from None
        if not isinstance(snap, dict) or snap.get("id") != sid or not isinstance(snap.get("files"), list):
            raise RepoError(f"snapshot {sid} malformed")
        if timeutil.classify(snap.get("created"))[0] != "utc":
            raise RepoError(f"snapshot {sid}: 'created' is not an explicit-UTC timestamp")
        count = snap.get("source_file_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise RepoError(f"snapshot {sid}: source_file_count missing")
        for entry in snap["files"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str)
                    or isinstance(entry.get("size"), bool) or not isinstance(entry.get("size"), int) or entry["size"] < 0
                    or not _RE_SHA.match(str(entry.get("sha256"))) or not isinstance(entry.get("blocks"), list)
                    or any(not _RE_SHA.match(str(b)) for b in entry["blocks"])):
                raise RepoError(f"snapshot {sid}: malformed file entry")
        return snap

    def created(self, sid: str) -> dt.datetime:
        return timeutil.parse_utc(self.read_snapshot(sid)["created"])


# ----------------------------------------------------------------------------- complete read

UNREADABLE_KINDS = ("index_unreadable", "pack_unreadable", "snapshot_unreadable", "snapshots_unlisted")


def inspect(repo: Repo) -> dict:
    """Read every part of a repository completely and verify it; nothing is skipped, nothing assumed.

    * ``index.json`` is parsed in full (a truncated, empty, non-JSON or malformed index is a problem);
    * ``pack.bin`` is read in full and every block the index lists must lie inside it and hash to
      its address;
    * every snapshot manifest under ``snapshots/`` is read and validated; every block it lists must
      be in the index, and every file it lists must rebuild to its recorded size and SHA-256, with
      as many files listed as declared.

    ``superseded/`` (manifests moved out by retention) is not restored and is not read here.
    Each problem carries its location inside the repository (``index.json``, ``pack.bin``,
    ``snapshots/<id>.json``). ``present`` is False only for a repository that holds nothing at all
    (no index, no pack, no manifest). ``verified`` is True only when the repository is present,
    every part was read to the end, no problem was met and at least one snapshot was read.
    Comparing a snapshot with the source tree is the restore drill's job, not this one's.
    """
    out: dict = {"present": False, "complete": False, "verified": False, "problems": [],
                 "index": {"file": "index.json", "read": False, "bytes": None, "blocks": None},
                 "pack": {"file": "pack.bin", "read": False, "bytes": None, "blocks_verified": 0},
                 "snapshots": {"listed": [], "read": [], "newest": None, "newest_created": None, "files_rebuilt": 0}}
    problems = out["problems"]

    def problem(kind: str, location: str, note: str, **where) -> None:
        problems.append(dict({"kind": kind, "location": location, "note": note}, **where))

    try:
        ids = repo.snapshot_ids()
    except RepoError as exc:
        ids = []
        problem("snapshots_unlisted", "snapshots/", str(exc))
    out["snapshots"]["listed"] = ids
    has_index, has_pack = fsx.exists(repo.index_path), fsx.exists(repo.pack_path)
    out["present"] = bool(has_index or has_pack or ids or problems)
    if not out["present"]:
        out["complete"] = True          # nothing is there; a repository that holds nothing is not verified
        return out

    index = None
    if has_index:
        try:
            out["index"]["bytes"] = fsx.size(repo.index_path)
            index = repo.load_index()
        except (OSError, RepoError) as exc:
            problem("index_unreadable", "index.json", str(exc) if isinstance(exc, RepoError) else _why(exc))
        else:
            out["index"].update({"read": True, "blocks": len(index["blocks"])})
    else:
        problem("index_unreadable", "index.json", "missing while the repository holds a pack or a snapshot")

    pack = None
    listed_blocks = len(index["blocks"]) if index is not None else None
    if has_pack:
        try:
            pack = fsx.read_bytes(repo.pack_path)
        except OSError as exc:
            problem("pack_unreadable", "pack.bin", _why(exc))
        else:
            out["pack"].update({"read": True, "bytes": len(pack)})
    elif listed_blocks:
        problem("pack_unreadable", "pack.bin", f"missing while the index lists {listed_blocks} block(s)")
    elif index is not None:
        pack = b""                        # nothing listed, nothing stored: consistent
        out["pack"].update({"read": True, "bytes": 0})

    good: set = set()
    if index is not None and pack is not None:
        for sha, (off, length) in sorted(index["blocks"].items()):
            if off + length > len(pack):
                problem("block_missing", "pack.bin", f"listed by the index at [{off}, {length}], "
                        f"beyond the end of the pack ({len(pack)} bytes)", block=sha)
            elif jsonio.sha256_bytes(pack[off:off + length]) != sha:
                problem("block_corrupt", "pack.bin", f"bytes at [{off}, {length}] do not hash to their address", block=sha)
            else:
                good.add(sha)
        out["pack"]["blocks_verified"] = len(good)

    rebuilt = 0
    for sid in ids:
        where = f"snapshots/{sid}.json"
        try:
            snap = repo.read_snapshot(sid)
        except RepoError as exc:
            problem("snapshot_unreadable", where, str(exc), snapshot=sid)
            continue
        out["snapshots"]["read"].append(sid)
        if len(snap["files"]) != snap["source_file_count"]:
            problem("listing_short" if len(snap["files"]) < snap["source_file_count"] else "listing_inconsistent", where,
                    f"{len(snap['files'])} listed, {snap['source_file_count']} declared", snapshot=sid)
        seen: set = set()
        for entry in snap["files"]:
            rel = entry["path"]
            if not safe_rel(rel) or rel in seen:
                problem("unsafe_path", where, "path escapes the root or repeats", snapshot=sid, file=rel)
                continue
            seen.add(rel)
            if index is None or pack is None:
                continue                  # already reported: the index or the pack cannot be read
            absent = [b for b in entry["blocks"] if b not in index["blocks"]]
            if absent:
                problem("block_missing", where, f"{len(absent)} block(s) listed by the snapshot are not in the index",
                        snapshot=sid, file=rel, block=absent[0])
                continue
            if any(b not in good for b in entry["blocks"]):
                continue                  # already reported at the pack level, with the block
            data = b"".join(pack[index["blocks"][b][0]:index["blocks"][b][0] + index["blocks"][b][1]] for b in entry["blocks"])
            if len(data) != entry["size"]:
                problem("size_short", where, f"{len(data)} bytes in blocks, {entry['size']} declared", snapshot=sid, file=rel)
            elif jsonio.sha256_bytes(data) != entry["sha256"]:
                problem("file_hash", where, "rebuilt bytes differ from the SHA-256 the manifest records", snapshot=sid, file=rel)
            else:
                rebuilt += 1
        if sid == ids[-1]:
            out["snapshots"].update({"newest": sid, "newest_created": snap["created"]})
    out["snapshots"]["files_rebuilt"] = rebuilt
    out["complete"] = not any(p["kind"] in UNREADABLE_KINDS for p in problems)
    out["verified"] = out["complete"] and not problems and bool(out["snapshots"]["read"])
    return out


def statement(inspection: dict, where: str | None = None) -> str:
    """One sentence per repository for the report and the console: what was read and verified, or what was not."""
    if not inspection["present"]:
        return "NOT verified: the repository holds nothing (no index, no pack, no snapshot manifest)"
    idx, pack, snaps = inspection["index"], inspection["pack"], inspection["snapshots"]
    parts = [f"index read in full ({idx['blocks']} block(s))" if idx["read"] else "index NOT read",
             (f"pack read in full ({pack['bytes']} bytes, {pack['blocks_verified']} of {idx['blocks']} listed block(s) "
              "hash to their address)" if idx["read"] else
              f"pack read ({pack['bytes']} bytes) but its blocks cannot be verified without the index")
             if pack["read"] else "pack NOT read",
             f"{len(snaps['read'])} of {len(snaps['listed'])} snapshot manifest(s) read",
             f"{snaps['files_rebuilt']} listed file(s) rebuilt to their recorded SHA-256"]
    if inspection["verified"]:
        return "verified: " + ", ".join(parts)
    if not inspection["problems"]:
        return "NOT verified: no snapshot to restore (" + ", ".join(parts) + ")"
    first = inspection["problems"][0]
    more = len(inspection["problems"]) - 1
    return (f"NOT verified: {where + '/' if where else ''}{first['location']}: {first['kind']} - {first['note']}"
            + (f" (+{more} more problem(s))" if more else "") + "; " + ", ".join(parts))


def block_size() -> int:
    return int(rules_engine.load(RULES).params["block_size"])


def backup(source_dir, repo_dir, created: dt.datetime) -> dict:
    """Write one snapshot of ``source_dir`` into the repository and confirm it by re-reading."""
    repo = Repo(repo_dir)
    size = block_size()
    if repo.exists():
        index = repo.load_index()
        if index["block_size"] != size:
            raise BackupError("repository was written with a different block size")
    else:
        index = {"format": 1, "block_size": size, "blocks": {}}
    sid = timeutil.snapshot_id(created)
    if fsx.exists(repo.snapshot_path(sid)):
        raise BackupError(f"snapshot {sid} already exists: a snapshot is never overwritten")
    pack_len = fsx.size(repo.pack_path) if fsx.exists(repo.pack_path) else 0
    if any(off + length > pack_len for off, length in index["blocks"].values()):
        raise BackupError("index points beyond the end of the pack: refusing to append to a damaged repository")
    rels = fsx.walk_files(source_dir)
    if not rels:
        raise BackupError("source tree is empty or missing: nothing to back up")
    fresh = bytearray()
    files = []
    for rel in rels:
        data = fsx.read_bytes(fsx.join(source_dir, rel))
        blocks = []
        for start in range(0, len(data), size):
            chunk = data[start:start + size]
            sha = jsonio.sha256_bytes(chunk)
            if sha not in index["blocks"]:
                index["blocks"][sha] = [pack_len + len(fresh), len(chunk)]
                fresh += chunk
            blocks.append(sha)
        files.append({"path": rel, "size": len(data), "sha256": jsonio.sha256_bytes(data), "blocks": blocks})
    snapshot = {"format": 1, "id": sid, "created": timeutil.fmt(created), "source_file_count": len(files),
                "total_bytes": sum(f["size"] for f in files), "files": files}
    fsx.append_bytes(repo.pack_path, bytes(fresh))
    jsonio.write(repo.index_path, index)
    jsonio.write(repo.snapshot_path(sid), snapshot)
    # confirm: what is on disk must be what was meant
    if repo.read_snapshot(sid) != snapshot or repo.load_index() != index:
        raise BackupError("snapshot or index differs after write")
    pack = fsx.read_bytes(repo.pack_path)
    for entry in files:
        for sha in entry["blocks"]:
            off, length = index["blocks"][sha]
            if jsonio.sha256_bytes(pack[off:off + length]) != sha:
                raise BackupError(f"block {sha[:12]} of {entry['path']} differs after write")
    return snapshot


# ----------------------------------------------------------------------------- chain order

def chain_facts(policy: dict, jobs: list[dict]) -> dict:
    """Order of export and backup: as written in the policy and as it happened in the latest run."""
    chain = policy["chain"]
    facts: dict = {"policy_export_before_backup": chain.index("export") < chain.index("backup_a")}
    evidence: dict = {"chain": chain}
    if "verify" in chain and "prune" in chain:
        facts["policy_verify_before_prune"] = chain.index("verify") < chain.index("prune")
    runs = sorted({j["run"] for j in jobs})
    if runs:
        latest = runs[-1]
        steps = {j["step"]: j for j in jobs if j["run"] == latest}
        export, backup_a = steps.get("export"), steps.get("backup_a")
        evidence["latest_run"] = latest
        if export and backup_a and export["_end"] is not None and backup_a["_start"] is not None:
            facts["trace_export_before_backup"] = export["_end"] <= backup_a["_start"]
            evidence["export_end"] = timeutil.fmt(export["_end"])
            evidence["backup_a_start"] = timeutil.fmt(backup_a["_start"])
        else:
            evidence["trace_order"] = "unknown: export or backup_a missing, or a timestamp without a zone"
    return {"facts": facts, "evidence": evidence}


# ----------------------------------------------------------------------------- retention

def retention(policy: dict, repo: Repo, verified: set) -> dict:
    """Decide what retention may do. ``verified`` holds snapshot ids with a successful drill on record."""
    rules = rules_engine.load(RULES)
    keep_last = as_int(policy["retention"]["keep_last"])
    ids = repo.snapshot_ids()
    valid = keep_last is not None and keep_last >= 0
    kept = ids[len(ids) - keep_last:] if valid and keep_last > 0 else []
    if valid and keep_last >= len(ids):
        kept = list(ids)
    superseded = [s for s in ids if s not in kept] if valid else []
    facts = {"keep_last_valid": valid, "to_supersede": len(superseded),
             "kept_verified": len([s for s in kept if s in verified])}
    then, rid = rules.decide("retention_rules", facts)
    return {"rule": rid, "decision": then["decision"], "class": then.get("class"), "facts": facts,
            "keep_last": policy["retention"]["keep_last"], "snapshots": ids, "keep": kept,
            "supersede": superseded, "verified": sorted(verified)}


def apply_retention(repo: Repo, decision: dict) -> list[str]:
    """Move superseded manifests to ``superseded/``. Blocks stay in the pack. Nothing is deleted."""
    if decision["decision"] != "supersede":
        return []
    moved = []
    for sid in decision["supersede"]:
        fsx.move(repo.snapshot_path(sid), fsx.join(repo.path, f"superseded/{sid}.json"))
        moved.append(sid)
    return moved
