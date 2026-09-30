"""Reference 1 of 3 - every routing rule by brute force.

Independent of the code under test: it shares no function with ``lab/``. It reads the instance
with plain ``yaml.safe_load`` and enumerates the whole universe of requests
``(entrypoint, host, path prefix)`` that the route files mention; for each request it lists the
enabled routers that answer it. No ordering rule, no priority logic: a request answered by two
enabled routers with the same prefix is a shadowed route, whatever the winner would be.

Covers: ``route_missing_service``, ``route_duplicate_shadow``, ``admin_on_public``.
"""
from __future__ import annotations

import os

import yaml

_BS = chr(92)


def _long(path: str) -> str:
    p = os.path.abspath(path)
    return (_BS * 2 + "?" + _BS + p) if os.name == "nt" and not p.startswith(_BS * 2) else p


def read_yaml(path: str):
    with open(_long(path), "rb") as fh:
        return yaml.safe_load(fh.read().decode("utf-8-sig"))


def route_documents(instance_dir: str) -> list:
    """``[(posix relative path, document)]`` for every ``.yaml`` / ``.yml`` under ``routes/``, sub-folders included."""
    out = []
    root = os.path.join(instance_dir, "routes")
    for dirpath, _dirs, names in os.walk(_long(root)):
        for name in names:
            if name.endswith(".yaml") or name.endswith(".yml"):
                full = os.path.join(dirpath, name)
                rel = "routes" + full[len(_long(root)):].replace(_BS, "/")
                out.append((rel, read_yaml(full)))
    return sorted(out, key=lambda pair: pair[0])


def rules(instance_dir: str) -> list:
    """Flat list of enabled routers with normalised host and prefix and the resolved target."""
    flat = []
    for position, (rel, doc) in enumerate(route_documents(instance_dir)):
        backends = doc.get("backends") or {}
        for index, router in enumerate(doc["routers"]):
            if router.get("enabled", True) is False:
                continue
            entrypoints = router["entrypoints"] if "entrypoints" in router else [router["entrypoint"]]
            prefix = router["path_prefix"].strip()
            while len(prefix) > 1 and prefix.endswith("/"):
                prefix = prefix[:-1]
            target = (backends.get(router["backend"]) or {}).get("target")
            service, port = (target.split(":") + [None])[:2] if target else (None, None)
            flat.append({"file": rel, "router": router["name"], "entrypoints": list(entrypoints),
                         "host": router["host"].strip().lower().rstrip("."), "prefix": prefix,
                         "priority": int(router.get("priority", 0)), "service": service,
                         "port": int(port) if port is not None else None, "order": (position, index)})
    return flat


def check(instance_dir: str) -> list:
    topology = read_yaml(os.path.join(instance_dir, "topology.yaml"))
    services, entrypoints, networks = topology["services"], topology["entrypoints"], topology["networks"]
    flat = rules(instance_dir)
    found = []
    for rule in flat:
        if rule["service"] is None or rule["service"] not in services:
            found.append({"class": "route_missing_service", "files": [rule["file"]], "item": rule["router"]})
            continue
        public = any(entrypoints[ep]["plane"] == "public" for ep in rule["entrypoints"])
        if public and services[rule["service"]]["plane"] == "admin":
            found.append({"class": "admin_on_public", "files": [rule["file"]], "item": rule["router"]})
    for name, spec in services.items():
        if spec["plane"] == "admin" and any(networks[n]["plane"] == "public" for n in spec["networks"]):
            found.append({"class": "admin_on_public", "files": ["topology.yaml"], "item": name})
    universe = sorted({(ep, rule["host"], rule["prefix"]) for rule in flat for ep in rule["entrypoints"]})
    seen = set()
    for entrypoint, host, prefix in universe:
        answering = [r for r in flat if entrypoint in r["entrypoints"] and r["host"] == host and r["prefix"] == prefix]
        files = tuple(sorted({r["file"] for r in answering}))
        if len(answering) >= 2 and files not in seen:
            seen.add(files)
            found.append({"class": "route_duplicate_shadow", "files": list(files), "item": None})
    return found
