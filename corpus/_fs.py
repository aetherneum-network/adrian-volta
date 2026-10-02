"""File helpers of the corpus side (kept separate from the lab's on purpose)."""
from __future__ import annotations

import hashlib
import os
import shutil

_BS = chr(92)


def long(path) -> str:
    """Absolute path; extended-length form on Windows so that trees deeper than 260 characters work."""
    p = os.path.abspath(os.fspath(path))
    if os.name == "nt" and not p.startswith(_BS * 2):
        return _BS * 2 + "?" + _BS + p
    return p


def put(root, rel: str, data: bytes) -> None:
    full = long(os.path.join(os.fspath(root), *rel.split("/")))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)


def get(root, rel: str) -> bytes:
    with open(long(os.path.join(os.fspath(root), *rel.split("/"))), "rb") as fh:
        return fh.read()


def listing(root) -> list[str]:
    base = long(root)
    out = []
    if not os.path.isdir(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        rel_dir = dirpath[len(base):].strip("/" + _BS).replace(_BS, "/")
        out.extend(f"{rel_dir}/{n}" if rel_dir else n for n in filenames)
    return sorted(out)


def wipe(path) -> None:
    """Remove a generated output directory before regenerating it (generated data only)."""
    full = long(path)
    if os.path.isdir(full):
        shutil.rmtree(full)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(entries) -> str:
    """SHA-256 over sorted ``<sha256>  <relative path>`` lines."""
    body = "".join(f"{d}  {rel}\n" for rel, d in sorted(entries))
    return sha(body.encode("utf-8"))
