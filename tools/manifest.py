"""Write or check ``MANIFEST.sha256``: the SHA-256 of every tracked file, naming the commit it describes.

    python tools/manifest.py --write      # after a commit: hashes the tracked files, names that commit
    python tools/manifest.py --check      # every listed file has the listed hash; nothing tracked is missing

The manifest is committed on top of the commit it names, so it cannot contain its own hash:
``MANIFEST.sha256`` is the only tracked file that is not listed. Text files are stored with LF
line endings on every system (see ``.gitattributes``), so the hashes are the same on Windows
and Linux. The list of files comes from ``git ls-files``; without git, ``--check`` verifies the
listed files only and says so.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lab import fsx, jsonio  # noqa: E402

NAME = "MANIFEST.sha256"


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", ROOT, "-c", "core.quotepath=false", *args], capture_output=True, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode("utf-8")


def tracked() -> list | None:
    out = _git("ls-files", "-z")
    if out is None:
        return None
    return sorted(rel for rel in out.split("\0") if rel and rel != NAME)


def build() -> str:
    rels = tracked()
    commit = (_git("rev-parse", "HEAD") or "").strip()
    if rels is None or not commit:
        raise SystemExit("git is needed to write the manifest")
    if (_git("status", "--porcelain", "--untracked-files=no") or "").strip().replace(f" M {NAME}", "").strip():
        raise SystemExit("tracked files differ from the commit: commit first, then write the manifest")
    entries = [(rel, jsonio.sha256_file(fsx.join(ROOT, rel))) for rel in rels]
    total = jsonio.tree_digest(entries)
    lines = [f"# MANIFEST.sha256 - SHA-256 of every tracked file except this one\n",
             f"# commit {commit}\n",
             f"# files {len(entries)}\n",
             f"# total {total}  (SHA-256 over the lines below)\n"]
    lines += [f"{digest}  {rel}\n" for rel, digest in entries]
    return "".join(lines)


def parse(text: str) -> tuple[dict, dict]:
    header, entries = {}, {}
    for line in text.splitlines():
        if line.startswith("# ") and len(line.split()) >= 3 and line.split()[1] in ("commit", "files", "total"):
            header[line.split()[1]] = line.split()[2]
        elif line and not line.startswith("#"):
            digest, rel = line.split("  ", 1)
            entries[rel] = digest
    return header, entries


def check() -> tuple[bool, list]:
    path = os.path.join(ROOT, NAME)
    if not fsx.exists(path):
        return False, [f"{NAME} is missing"]
    header, entries = parse(fsx.read_bytes(path).decode("utf-8"))
    problems = []
    for rel, digest in sorted(entries.items()):
        full = fsx.join(ROOT, rel)
        if not fsx.isfile(full):
            problems.append(f"missing: {rel}")
        elif jsonio.sha256_file(full) != digest:
            problems.append(f"differs: {rel}")
    if header.get("total") != jsonio.tree_digest(entries.items()):
        problems.append("the total does not match the listed lines")
    rels = tracked()
    notes = [f"commit named by the manifest: {header.get('commit', '[missing]')}", f"files listed: {len(entries)}"]
    if rels is None:
        notes.append("git not available: only the listed files were verified")
    else:
        problems += [f"tracked but not listed: {rel}" for rel in rels if rel not in entries]
        problems += [f"listed but not tracked: {rel}" for rel in entries if rel not in rels]
    return not problems, problems + notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    if args.write:
        text = build()
        jsonio.write_text(os.path.join(ROOT, NAME), text)
        print(f"written: {NAME} ({text.count(chr(10)) - 4} files)")
        return 0
    ok, lines = check()
    for line in lines:
        print(line)
    print("manifest: " + ("OK" if ok else "DIFFERENT"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
