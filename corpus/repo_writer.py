"""Generator-side writer of the repository and registry formats (independent of the lab's writer).

Format, as specified in docs/FORMAT.md: blocks of ``block_size`` bytes addressed by SHA-256,
appended to ``pack.bin`` in order of first appearance while walking the files in sorted path
order; ``index.json`` maps each address to ``[offset, length]``; one ``snapshots/<id>.json``
per backup. Canonical JSON: sorted keys, two-space indent, UTF-8, LF, one trailing newline.
"""
from __future__ import annotations

import hashlib
import json

BLOCK_SIZE = 1024
GENESIS = "0" * 64


def canonical(obj) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def compact(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class MemRepo:
    """A repository held in memory, so that a fault can be planted before anything is written."""

    def __init__(self):
        self.pack = bytearray()
        self.blocks: dict = {}
        self.snapshots: dict = {}

    def add_snapshot(self, sid: str, created: str, files: dict) -> dict:
        entries = []
        for rel in sorted(files):
            data = files[rel]
            addresses = []
            for start in range(0, len(data), BLOCK_SIZE):
                chunk = data[start:start + BLOCK_SIZE]
                address = _sha(chunk)
                if address not in self.blocks:
                    self.blocks[address] = [len(self.pack), len(chunk)]
                    self.pack += chunk
                addresses.append(address)
            entries.append({"path": rel, "size": len(data), "sha256": _sha(data), "blocks": addresses})
        snap = {"format": 1, "id": sid, "created": created, "source_file_count": len(entries),
                "total_bytes": sum(e["size"] for e in entries), "files": entries}
        self.snapshots[sid] = snap
        return snap

    def newest(self) -> dict:
        return self.snapshots[max(self.snapshots)]

    def files(self) -> dict:
        out = {"pack.bin": bytes(self.pack),
               "index.json": canonical({"format": 1, "block_size": BLOCK_SIZE, "blocks": self.blocks})}
        for sid, snap in self.snapshots.items():
            out[f"snapshots/{sid}.json"] = canonical(snap)
        return out


def registry(entries: list) -> bytes:
    """Hash-chained drill registry: one compact JSON line per entry."""
    lines = []
    prev = GENESIS
    for seq, body in enumerate(entries, start=1):
        entry = dict(body, seq=seq, prev=prev)
        entry["hash"] = _sha(compact(entry).encode("utf-8"))
        prev = entry["hash"]
        lines.append(compact(entry) + "\n")
    return "".join(lines).encode("utf-8")
