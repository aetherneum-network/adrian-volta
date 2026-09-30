"""File-system helpers that survive Windows long paths and keep relative paths portable.

Rules applied everywhere in the pack:

* relative paths are POSIX (forward slashes) and are compared as strings;
* on Windows every call goes through the extended-length path form, so trees whose paths
  exceed 260 characters are enumerated, written, hashed and removed like any other;
* enumeration is sorted, so two runs see the files in the same order.
"""
from __future__ import annotations

import os
import shutil

_BS = chr(92)  # one backslash
_EXT_PREFIX = _BS * 2 + "?" + _BS
_UNC_PREFIX = _BS * 2


def ext(path) -> str:
    """Absolute path; on Windows in extended-length form."""
    p = os.path.abspath(os.fspath(path))
    if os.name != "nt" or p.startswith(_EXT_PREFIX):
        return p
    if p.startswith(_UNC_PREFIX):
        return _EXT_PREFIX + "UNC" + _BS + p[2:]
    return _EXT_PREFIX + p


def join(root, rel: str) -> str:
    """Join a root with a POSIX relative path."""
    return os.path.join(os.fspath(root), *rel.split("/")) if rel else os.fspath(root)


def exists(path) -> bool:
    return os.path.exists(ext(path))


def isdir(path) -> bool:
    return os.path.isdir(ext(path))


def isfile(path) -> bool:
    return os.path.isfile(ext(path))


def makedirs(path) -> None:
    os.makedirs(ext(path), exist_ok=True)


def read_bytes(path) -> bytes:
    with open(ext(path), "rb") as fh:
        return fh.read()


def write_bytes(path, data: bytes) -> None:
    """Write, then confirm the size on disk: a write that cannot be confirmed is an error."""
    full = ext(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)
    if os.path.getsize(full) != len(data):
        raise OSError(f"short write: {os.fspath(path)}")


def append_bytes(path, data: bytes) -> None:
    full = ext(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    before = os.path.getsize(full) if os.path.exists(full) else 0
    with open(full, "ab") as fh:
        fh.write(data)
    if os.path.getsize(full) != before + len(data):
        raise OSError(f"short append: {os.fspath(path)}")


def size(path) -> int:
    return os.path.getsize(ext(path))


def _listing_failed(exc: OSError) -> None:
    raise exc


def walk_files(root) -> list[str]:
    """Sorted POSIX relative paths of every file under ``root`` (empty list if it is missing).

    A folder that exists but cannot be listed raises ``OSError``: it is never skipped in silence
    (``os.walk`` alone drops such a folder and its files without a word).
    """
    base = ext(root)
    if not os.path.isdir(base):
        return []
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base, onerror=_listing_failed):
        dirnames.sort()
        rel_dir = dirpath[len(base):].strip("/" + _BS).replace(_BS, "/")
        for name in filenames:
            out.append(f"{rel_dir}/{name}" if rel_dir else name)
    return sorted(out)


def rmtree(path) -> None:
    """Remove a scratch tree (work directories only; repository data is never removed)."""
    full = ext(path)
    if os.path.isdir(full):
        shutil.rmtree(full)


def copytree(src, dst) -> None:
    for rel in walk_files(src):
        write_bytes(join(dst, rel), read_bytes(join(src, rel)))


def move(src, dst) -> None:
    os.makedirs(os.path.dirname(ext(dst)), exist_ok=True)
    os.replace(ext(src), ext(dst))
