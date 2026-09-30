"""Helpers shared by the tests: scratch folders, small synthetic instances, seeded trees."""
from __future__ import annotations

import os
import random
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIOS = os.path.join(ROOT, "scenarios")
for _path in (ROOT, SCENARIOS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _common as SC  # noqa: E402  (scenario helpers: deterministic byte streams, one night of the chain)
from corpus import yamlout  # noqa: E402
from lab import backup, fsx, jsonio  # noqa: E402

FULL_CHAIN = ["export", "backup_a", "backup_b", "verify", "prune"]
ACCENTED = ["Relazione qualità.txt", "Città e sedi.csv", "Verbale più recente.md", "così com'è.txt", "perché.log.txt"]
LONG_DIR = "/".join(["cartella-con-un-nome-volutamente-molto-lungo-per-superare-il-limite-storico-%02d" % i for i in range(5)])


class TempCase(unittest.TestCase):
    """A test case with a scratch folder that is removed afterwards (long paths included)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="av2-")
        self.addCleanup(fsx.rmtree, self.tmp)

    def path(self, *parts: str) -> str:
        return os.path.join(self.tmp, *parts)


def write_yaml(path: str, doc) -> None:
    fsx.write_bytes(path, yamlout.emit(doc))


def topology_doc(public=("shop",), admin=("console",), health=None, admin_networks=None) -> dict:
    services = {}
    for i, name in enumerate(public):
        services[name] = {"plane": "public", "port": 8080 + i, "networks": ["edge-public", "app-1"],
                          "restart": {"policy": "always"}, "health": (health or {}).get(name, {"kind": "http", "path": "/healthz"})}
    for i, name in enumerate(admin):
        services[name] = {"plane": "admin", "port": 9000 + i, "networks": admin_networks or ["edge-admin", "app-1"],
                          "restart": {"policy": "always"}, "health": (health or {}).get(name, {"kind": "process"})}
    return {"company": "Cartiera Valdora S.p.A.", "domain": "valdora.example",
            "entrypoints": {"public": {"plane": "public", "network": "edge-public"},
                            "admin": {"plane": "admin", "network": "edge-admin"}},
            "networks": {"edge-public": {"plane": "public"}, "edge-admin": {"plane": "admin"}, "app-1": {"plane": "internal"}},
            "services": services}


def route_doc(service: str, port: int, entrypoint="public", prefix="/", host=None, name=None, **extra) -> dict:
    router = {"name": name or f"{service}-main", "entrypoint": entrypoint, "host": host or f"{service}.valdora.example",
              "path_prefix": prefix, "backend": f"{service}-backend"}
    router.update(extra)
    return {"service": service, "routers": [router], "backends": {f"{service}-backend": {"target": f"{service}:{port}"}}}


def routing_instance(root: str, topology: dict, routes: dict, scope=("routing",), as_of="2026-09-14T06:00:00Z",
                     trace: list | None = None) -> str:
    jsonio.write(os.path.join(root, "instance.json"), {"instance_id": "test-instance", "as_of": as_of, "scope": list(scope)})
    write_yaml(os.path.join(root, "topology.yaml"), topology)
    for rel, doc in routes.items():
        if isinstance(doc, (bytes, str)):
            fsx.write_bytes(fsx.join(root, rel), doc if isinstance(doc, bytes) else doc.encode("utf-8"))
        else:
            write_yaml(fsx.join(root, rel), doc)
    if trace is not None:
        jsonio.write_jsonl(os.path.join(root, "trace.jsonl"), trace)
    return root


def up_events(services, t="2026-09-14T00:00:00Z", serve=None) -> list:
    events = []
    for name in services:
        events.append({"kind": "proc", "t": t, "service": name, "state": "up"})
        events.append({"kind": "serve", "t": t, "service": name, "map": (serve or {}).get(name, {"/": 200, "/healthz": 200})})
    return events


def policy_doc(chain=None, keep_last=7, at="02:30", tz="UTC", max_lag=None) -> dict:
    b = {"path": "repo_b"}
    if max_lag is not None:
        b["max_lag_hours"] = max_lag
    return {"schedule": {"at": at, "tz": tz}, "chain": chain or FULL_CHAIN, "source": "source",
            "export_path": "source/export/orders-export.csv", "repos": {"a": {"path": "repo_a"}, "b": b},
            "retention": {"keep_last": keep_last}}


def tree_spec(tag: str, count: int = 6) -> list:
    files = [{"path": "export/orders-export.csv", "size": 1500, "seed": f"{tag}:export:0"}]
    for i in range(count):
        name = ACCENTED[i % len(ACCENTED)]
        files.append({"path": f"dati/{i:02d}/{name}", "size": 300 + 700 * i, "seed": f"{tag}:{i}"})
    return files


def backup_instance(root: str, tag: str = "t", days=("2026-09-11", "2026-09-12"), as_of="2026-09-12T06:10:00Z",
                    policy: dict | None = None, files: list | None = None, per_day: dict | None = None) -> dict:
    """A small backup-scope instance built with the real backup code, night by night."""
    jsonio.write(os.path.join(root, "instance.json"), {"instance_id": f"test-{tag}", "as_of": as_of, "scope": ["backup"]})
    write_yaml(os.path.join(root, "policy", "backup.yaml"), policy or policy_doc())
    parsed, problems = backup.load_policy(root)
    assert parsed is not None, problems
    SC.write_tree(os.path.join(root, "source"), files or tree_spec(tag))
    trace: list = []
    ids = []
    for n, day in enumerate(days):
        spec = {"run": day, "start": f"{day}T02:30:00Z", "export_seed": f"{tag}:export:{day}",
                "changes": [{"path": "dati/00/" + ACCENTED[0], "size": 300 + n, "seed": f"{tag}:change:{day}"}]}
        spec.update((per_day or {}).get(day, {}))
        ids.append(SC.night(root, parsed, spec, trace))
    jsonio.write_jsonl(os.path.join(root, "trace.jsonl"), trace)
    return {"policy": parsed, "nights": ids, "trace": trace}


def random_tree(root: str, rng: random.Random, long_paths: bool = False) -> dict:
    """Write a seeded tree; returns ``{relative path: bytes}``. Sizes straddle the block size on purpose."""
    files = {}
    for i in range(rng.randint(1, 9)):
        depth = rng.randint(0, 3)
        parts = [rng.choice(["dati", "archivio", "qualità", "export", "più-recenti"]) for _ in range(depth)]
        if long_paths and i == 0:
            parts = LONG_DIR.split("/") + parts
        name = f"{i:02d}-{rng.choice(ACCENTED)}"
        size = rng.choice([0, 1, 1023, 1024, 1025, 2048, rng.randint(2, 5000)])
        files["/".join(parts + [name])] = rng.randbytes(size)
    for rel, data in files.items():
        fsx.write_bytes(fsx.join(root, rel), data)
    return files


def load_module(rel: str):
    """Import a script of the repository by its path (``tools/scan.py``, ``eval/score.py``, ...)."""
    import importlib.util
    name = "av2_" + rel.replace("/", "_").replace(".py", "")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, *rel.split("/")))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def python_files(*folders: str) -> list:
    """Repository-relative POSIX paths of the Python sources under the given top-level folders."""
    out = []
    for folder in folders:
        out += [f"{folder}/{rel}" for rel in fsx.walk_files(os.path.join(ROOT, folder))
                if rel.endswith(".py") and "__pycache__" not in rel]
    return sorted(out)
