"""Content-addressed backup into a repository, policy reading, chain order and retention.

Repository layout (the same for the primary ``a`` and the secondary ``b``)::

    pack.bin                   blocks, concatenated in order of first appearance
    index.json                 {"format": 1, "block_size": n, "blocks": {sha256: [offset, length]}}
    snapshots/<id>.json        one manifest per backup: files, sizes, SHA-256, block lists
    superseded/<id>.json       manifests moved out by retention - moved, never deleted

A block is addressed by the SHA-256 of its content, so an unchanged file costs nothing on the
next night and a changed byte is visible as a wrong address. A snapshot is never overwritten.
``backup`` re-reads what it wrote before returning: a write that cannot be confirmed is a failed run.
"""
from __future__ import annotations

import datetime as dt
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
        try:
            index = jsonio.read(self.index_path)
        except (OSError, ValueError) as exc:
            raise RepoError(f"index unreadable: {type(exc).__name__}") from None
        blocks = index.get("blocks") if isinstance(index, dict) else None
        if not isinstance(blocks, dict) or as_int(index.get("block_size")) is None:
            raise RepoError("index malformed")
        for sha, loc in blocks.items():
            if (not _RE_SHA.match(str(sha)) or not isinstance(loc, list) or len(loc) != 2
                    or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in loc)):
                raise RepoError("index malformed")
        return index

    def snapshot_ids(self) -> list[str]:
        names = [rel[:-5] for rel in fsx.walk_files(fsx.join(self.path, "snapshots"))
                 if "/" not in rel and rel.endswith(".json")]
        return sorted(n for n in names if _RE_SNAPSHOT.match(n))

    def read_snapshot(self, sid: str) -> dict:
        try:
            snap = jsonio.read(self.snapshot_path(sid))
        except (OSError, ValueError) as exc:
            raise RepoError(f"snapshot {sid} unreadable: {type(exc).__name__}") from None
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
