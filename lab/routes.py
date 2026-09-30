"""File-provider route table: one YAML file per service under ``routes/``, loaded recursively.

Every ``*.yaml`` / ``*.yml`` file under ``routes/`` is loaded, in sorted POSIX-path order,
sub-folders included: a copy left in a forgotten sub-folder is part of the table. Nothing else
is loaded. Exposing a new service means adding one file; nothing else is touched.

Resolution of a request ``(entrypoint, host, path)``: among the enabled routers on that
entrypoint whose host is equal (case-insensitive) and whose ``path_prefix`` is a segment prefix
of the path, the winner has the highest ``priority``, then the longest prefix, then the
earliest load position. Two routers with the same ``(entrypoint, host, path_prefix)`` therefore
hide one another: that is reported by the lint, not resolved silently.
"""
from __future__ import annotations

from dataclasses import dataclass

from lab import fsx, jsonio, yamlio
from lab.topology import as_int

ROUTES_DIR = "routes"
_TOP_KEYS = ("service", "routers", "backends")
_ROUTER_KEYS = ("name", "entrypoint", "entrypoints", "host", "path_prefix", "backend", "priority", "enabled")


@dataclass(frozen=True)
class Router:
    file: str
    name: str
    entrypoints: tuple
    host: str
    path_prefix: str
    backend: str
    target_service: str | None   # None when the backend name is not defined in the file
    target_port: int | None
    priority: int
    enabled: bool
    order: int


def norm_host(host: str) -> str:
    host = host.strip().lower()
    return host[:-1] if host.endswith(".") else host


def norm_prefix(prefix: str) -> str:
    prefix = prefix.strip()
    while len(prefix) > 1 and prefix.endswith("/"):
        prefix = prefix[:-1]
    return prefix


def route_files(instance_dir) -> list[str]:
    root = fsx.join(instance_dir, ROUTES_DIR)
    return [f"{ROUTES_DIR}/{rel}" for rel in fsx.walk_files(root) if rel.endswith((".yaml", ".yml"))]


def _unknown(mapping: dict, allowed: tuple) -> list:
    return [k for k in mapping if not isinstance(k, str) or (k not in allowed and not k.startswith("x-"))]


def parse_file(rel: str, doc, first_order: int) -> tuple[list[Router], str | None]:
    """Parse one route document. Returns ``(routers, None)`` or ``([], reason)``: all or nothing."""
    if not isinstance(doc, dict):
        return [], "top level is not a mapping"
    bad = _unknown(doc, _TOP_KEYS)
    if bad:
        return [], f"unknown key {bad[0]!r}"
    if not isinstance(doc.get("service"), str):
        return [], "service must be a string"
    raw_routers, raw_backends = doc.get("routers"), doc.get("backends", {})
    if not isinstance(raw_routers, list) or not raw_routers:
        return [], "routers must be a non-empty list"
    if not isinstance(raw_backends, dict):
        return [], "backends must be a mapping"
    backends: dict = {}
    for name, spec in raw_backends.items():
        if not isinstance(name, str) or not isinstance(spec, dict) or _unknown(spec, ("target",)):
            return [], f"backend {name!r}: expected a mapping with 'target'"
        target = spec.get("target")
        if not isinstance(target, str) or target.count(":") != 1:
            return [], f"backend {name}: target must be 'service:port'"
        svc, port = target.split(":")
        port_n = as_int(port)
        if not svc or port_n is None:
            return [], f"backend {name}: target must be 'service:port'"
        backends[name] = (svc, port_n)
    routers: list[Router] = []
    names = set()
    for pos, spec in enumerate(raw_routers):
        if not isinstance(spec, dict):
            return [], f"router #{pos + 1} is not a mapping"
        bad = _unknown(spec, _ROUTER_KEYS)
        if bad:
            return [], f"router #{pos + 1}: unknown key {bad[0]!r}"
        name = spec.get("name")
        if not isinstance(name, str) or not name or name in names:
            return [], f"router #{pos + 1}: name must be a unique non-empty string"
        names.add(name)
        if ("entrypoint" in spec) == ("entrypoints" in spec):
            return [], f"router {name}: exactly one of 'entrypoint' / 'entrypoints' is required"
        eps = [spec["entrypoint"]] if "entrypoint" in spec else spec["entrypoints"]
        if not isinstance(eps, list) or not eps or any(not isinstance(e, str) or not e for e in eps) or len(set(eps)) != len(eps):
            return [], f"router {name}: entrypoints must be distinct non-empty strings"
        host, prefix, backend = spec.get("host"), spec.get("path_prefix"), spec.get("backend")
        if not isinstance(host, str) or not norm_host(host):
            return [], f"router {name}: host must be a non-empty string"
        if not isinstance(prefix, str) or not prefix.strip().startswith("/"):
            return [], f"router {name}: path_prefix must start with '/'"
        if not isinstance(backend, str) or not backend:
            return [], f"router {name}: backend must be a non-empty string"
        priority = as_int(spec.get("priority", 0))
        if priority is None:
            return [], f"router {name}: priority must be an integer"
        enabled = spec.get("enabled", True)
        if not isinstance(enabled, bool):
            return [], f"router {name}: enabled must be true or false"
        svc, port = backends.get(backend, (None, None))
        routers.append(Router(rel, name, tuple(eps), norm_host(host), norm_prefix(prefix), backend, svc, port,
                              priority, enabled, first_order + pos))
    return routers, None


def load(instance_dir) -> tuple[list[Router], list[tuple[str, str]], list[str]]:
    """Return ``(routers, problems, files)``; ``problems`` is a list of ``(file, reason)``."""
    routers: list[Router] = []
    problems: list[tuple[str, str]] = []
    files = route_files(instance_dir)
    for rel in files:
        doc, err = yamlio.load_file(fsx.join(instance_dir, rel))
        if err:
            problems.append((rel, err))
            continue
        parsed, reason = parse_file(rel, doc, len(routers))
        if reason:
            problems.append((rel, reason))
            continue
        routers.extend(parsed)
    return routers, problems, files


def table(routers: list[Router]) -> list[dict]:
    """One entry per enabled router and entrypoint, sorted by key then load position."""
    entries = []
    for r in routers:
        if not r.enabled:
            continue
        for ep in r.entrypoints:
            entries.append({"entrypoint": ep, "host": r.host, "path_prefix": r.path_prefix, "priority": r.priority,
                            "router": r.name, "file": r.file, "backend": r.backend,
                            "target_service": r.target_service, "target_port": r.target_port, "order": r.order})
    return sorted(entries, key=lambda e: (e["entrypoint"], e["host"], e["path_prefix"], e["order"]))


def prefix_matches(prefix: str, path: str) -> bool:
    if prefix == "/":
        return path.startswith("/")
    return path == prefix or path.startswith(prefix + "/")


def resolve(entries: list[dict], entrypoint: str, host: str, path: str) -> dict | None:
    host = norm_host(host)
    best = None
    for e in entries:
        if e["entrypoint"] != entrypoint or e["host"] != host or not prefix_matches(e["path_prefix"], path):
            continue
        rank = (e["priority"], len(e["path_prefix"]), -e["order"])
        if best is None or rank > best[0]:
            best = (rank, e)
    return best[1] if best else None


def _public(entry: dict) -> dict:
    return {k: v for k, v in entry.items() if k != "order"}


def diff(before: list[dict], after: list[dict]) -> dict:
    """Entries added, removed and changed between two tables (keyed by file and router and entrypoint)."""
    def key(e):
        return (e["file"], e["router"], e["entrypoint"])
    a = {key(e): _public(e) for e in before}
    b = {key(e): _public(e) for e in after}
    return {"added": [b[k] for k in sorted(b) if k not in a],
            "removed": [a[k] for k in sorted(a) if k not in b],
            "changed": [{"before": a[k], "after": b[k]} for k in sorted(a) if k in b and a[k] != b[k]]}


def table_sha256(entries: list[dict]) -> str:
    return jsonio.sha256_bytes(jsonio.dumps([_public(e) for e in entries]).encode("utf-8"))
