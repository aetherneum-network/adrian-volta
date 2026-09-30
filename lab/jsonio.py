"""Canonical JSON and hashing helpers.

Canonical form used for every generated file: UTF-8, LF, sorted keys, two-space indent,
non-ASCII characters written as themselves, one trailing newline. JSON Lines use the compact
form (sorted keys, no spaces). Files are always written in binary mode so that no operating
system rewrites the line endings.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from lab import fsx


def dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def line(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write(path, obj: Any) -> None:
    fsx.write_bytes(path, dumps(obj).encode("utf-8"))


def write_text(path, text: str) -> None:
    fsx.write_bytes(path, text.replace("\r\n", "\n").encode("utf-8"))


def write_jsonl(path, rows: Iterable[Any]) -> None:
    fsx.write_bytes(path, "".join(line(r) + "\n" for r in rows).encode("utf-8"))


def read(path) -> Any:
    return json.loads(fsx.read_bytes(path).decode("utf-8"))


def read_jsonl(path) -> list[Any]:
    text = fsx.read_bytes(path).decode("utf-8")
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(fsx.ext(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(entries: Iterable[tuple[str, str]]) -> str:
    """SHA-256 over sorted ``<file sha256>  <posix relative path>`` lines."""
    body = "".join(f"{digest}  {rel}\n" for rel, digest in sorted(entries))
    return sha256_bytes(body.encode("utf-8"))
