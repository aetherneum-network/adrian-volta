"""Minimal content scanner for the repository (evidence item E7, until a shared scanner replaces it).

    python tools/scan.py [--json reports/scan.json]

It reads every text file of the repository (``build/``, version-control data, this report and
the derived ``MANIFEST.sha256`` excluded) and reports, line by line:

* ``PATH-WIN``   a Windows drive path
* ``PATH-UNIX``  an absolute path under a system or home directory
* ``SECRET-*``   a private-key header, a well-known token prefix, or a password-like assignment
* ``DOMAIN``     a host name under a real top-level domain that is not on the allow-list below
* ``IPV4``       an IPv4 address outside the documentation and loopback ranges

Exit code 0 when nothing is found, 1 otherwise. The report has no date and no path outside the
repository, so the same tree gives the same bytes. The scanner is deliberately small: it proves
the absence of a few recognisable patterns, not the absence of every possible leak.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from lab import fsx, jsonio, offline  # noqa: E402

VERSION = "1"
SKIP_DIRS = ("build/", ".git/")
# Not scanned: this report itself, and the manifest, which is derived (SHA-256 digests and the paths of
# files that are scanned) and is written on top of the commit that holds this report.
SKIP_FILES = ("reports/scan.json", "MANIFEST.sha256")
BINARY_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".bin", ".pyc")

# Host names that may appear: the declared publisher of the profile, the code host, the model
# vendor named in MODEL.md, the home pages of the tools the pack depends on, and the name space
# of the standard YAML tags.
ALLOWED_DOMAINS = ("aetherneum.com", "github.com", "anthropic.com", "pyyaml.org", "yaml.org", "python.org", "opensource.org")
# Reserved names (RFC 2606 / RFC 6761) are always fine: they cannot belong to anyone.
RESERVED_SUFFIXES = (".example", ".invalid", ".test", ".localhost", ".example.com", ".example.org", ".example.net")
REAL_TLDS = ("com", "org", "net", "io", "eu", "app", "dev", "ai", "info", "cloud", "xyz", "fit", "biz")
DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.", "127.", "0.0.0.0")

RULES = [
    ("PATH-WIN", re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?=[A-Za-z_])")),
    ("PATH-UNIX", re.compile(r"(?<![A-Za-z0-9_.-])/(?:opt|home|Users|root|var/lib|var/log|srv|mnt)/[A-Za-z0-9_.-]")),
    ("SECRET-KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("SECRET-TOKEN", re.compile(r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|sk-[A-Za-z0-9_-]{24,}|xox[baprs]-[A-Za-z0-9-]{10,})")),
    ("SECRET-ASSIGN", re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\b\s*[:=]\s*[\"']?[A-Za-z0-9/+_-]{12,}")),
]
_RE_DOMAIN = re.compile(r"(?<![A-Za-z0-9_.@/-])((?:[a-z0-9-]+\.)+(?:" + "|".join(REAL_TLDS) + r"))(?![A-Za-z0-9_-])")
_RE_EMAIL_DOMAIN = re.compile(r"@((?:[a-z0-9-]+\.)+[a-z]{2,})")
_RE_URL_DOMAIN = re.compile(r"https?://((?:[a-z0-9-]+\.)+[a-z]{2,})")
_RE_IPV4 = re.compile(r"(?<![0-9.])((?:\d{1,3}\.){3}\d{1,3})(?![0-9.])")


def _allowed(host: str) -> bool:
    host = host.lower()
    if host in ("example.com", "example.org", "example.net") or host.endswith(RESERVED_SUFFIXES):
        return True
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def scan_text(rel: str, text: str) -> list:
    found = []
    for number, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in RULES:
            if pattern.search(line):
                found.append({"file": rel, "line": number, "rule": rule})
        hosts = set(_RE_DOMAIN.findall(line)) | set(_RE_EMAIL_DOMAIN.findall(line.lower())) | set(_RE_URL_DOMAIN.findall(line.lower()))
        for host in sorted(hosts):
            if not _allowed(host):
                found.append({"file": rel, "line": number, "rule": "DOMAIN", "value": host})
        for address in sorted(set(_RE_IPV4.findall(line))):
            parts = address.split(".")
            if all(p.isdigit() and int(p) <= 255 for p in parts) and not address.startswith(DOC_NETS):
                found.append({"file": rel, "line": number, "rule": "IPV4", "value": address})
    return found


def files(root: str) -> list:
    return [rel for rel in fsx.walk_files(root)
            if not rel.startswith(SKIP_DIRS) and "/__pycache__/" not in "/" + rel and rel not in SKIP_FILES]


def scan(root: str = ROOT) -> dict:
    found, scanned, binary, undecodable = [], 0, [], []
    for rel in files(root):
        if rel.lower().endswith(BINARY_SUFFIXES):
            binary.append(rel)
            continue
        try:
            text = fsx.read_bytes(fsx.join(root, rel)).decode("utf-8")
        except UnicodeDecodeError:
            undecodable.append(rel)
            found.append({"file": rel, "line": 0, "rule": "NOT-UTF8"})
            continue
        scanned += 1
        found.extend(scan_text(rel, text))
    return {"scanner": "tools/scan.py", "version": VERSION, "rules": [r for r, _ in RULES] + ["DOMAIN", "IPV4", "NOT-UTF8"],
            "allowed_domains": list(ALLOWED_DOMAINS), "files_scanned": scanned, "binary_files_not_scanned": binary,
            "findings": found, "result": "clean" if not found else "findings",
            "limits": "pattern scan only; it does not prove the absence of every possible leak",
            "declaration_signed_by": "[TO CONFIRM]"}


def main(argv=None) -> int:
    offline.enforce()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", help="write the report to this file")
    args = ap.parse_args(argv)
    report = scan()
    if args.json:
        jsonio.write(args.json, report)
    for f in report["findings"]:
        print(f"{f['rule']:<14}{f['file']}:{f['line']}  {f.get('value', '')}")
    print(f"scan: {report['files_scanned']} text files, {len(report['findings'])} finding(s) - {report['result']}")
    return 0 if not report["findings"] else 1


if __name__ == "__main__":
    sys.exit(main())
