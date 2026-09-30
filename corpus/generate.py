"""Generate a suite of synthetic instances with planted faults and gold labels.

    python corpus/generate.py --suite dev --out build/corpus/dev --gold build/corpus/dev.labels.jsonl
    python corpus/generate.py --check            # dev suite against corpus/MANIFEST.sha256 and the committed gold
    python corpus/generate.py --seed N --out DIR --gold FILE [--styles a,b] [--faults-per-instance 2]

Every instance is drawn from ``random.Random("av2:<seed>:<index>")`` and nothing else: no clock,
no environment, no network. The gold label of an instance is the fault plan that built it - it
is written before any file of the instance exists and never derived from reading the files.

Three steps per instance: (1) a clean plan with decoys, (2) the planted fault(s) change the
plan, (3) the plan is materialised into files. Decoys are near-misses that must *not* be
reported (a disabled duplicate, one failing beat, a late run inside the grace period, ...).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import os
import random
import sys
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus import _fs, world as W, yamlout  # noqa: E402
from corpus.repo_writer import MemRepo, canonical, compact, registry as registry_bytes  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
UTC = dt.timezone.utc
COUNT = 240
FAULTY_SHARE = 0.7

# Seeds fixed before the first run. Only ``dev`` is ever inspected by the author; ``holdout`` is
# scored in aggregate and never opened; ``stress`` is diagnostic and declared as such.
SUITES = {
    "dev": {"seed": 20260930, "faults": 1, "styles": []},
    "holdout": {"seed": 20261001, "faults": 1, "styles": []},
    "stress": {"seed": 20261002, "faults": 2,
               "styles": ["flow", "double-quoted", "comments", "key-order", "indent-4", "crlf", "yml-ext",
                          "host-case", "prefix-slash", "entrypoints-list"]},
}

CLASSES = ["route_missing_service", "route_duplicate_shadow", "admin_on_public", "health_probes_process",
           "restart_loop", "repo_b_stale", "backup_truncated", "block_corrupt", "job_order_inverted",
           "window_missed", "retention_deletes_only_valid", "tz_mixed"]
AREA = {"route_missing_service": "routing", "route_duplicate_shadow": "routing", "admin_on_public": "routing",
        "health_probes_process": "health", "restart_loop": "health",
        "backup_truncated": "data", "block_corrupt": "data", "job_order_inverted": "data",
        "repo_b_stale": "ops", "window_missed": "ops", "retention_deletes_only_valid": "ops", "tz_mixed": "ops"}
VERDICT = {"route_missing_service": "BLOCKED", "route_duplicate_shadow": "BLOCKED", "admin_on_public": "BLOCKED",
           "retention_deletes_only_valid": "BLOCKED", "health_probes_process": "FAILED", "restart_loop": "FAILED",
           "backup_truncated": "FAILED", "block_corrupt": "FAILED", "job_order_inverted": "FAILED",
           "repo_b_stale": "ALERT", "window_missed": "ALERT", "tz_mixed": "ALERT"}
PRECEDENCE = ["FAILED", "BLOCKED", "ALERT", "OK"]
_APPLY_ORDER = {"routing": 0, "health": 1, "ops": 2, "data": 3}
POLICY_FILE = "policy/backup.yaml"
TRACE_FILE = "trace.jsonl"
TOPOLOGY_FILE = "topology.yaml"


def z(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def sid(t: dt.datetime) -> str:
    return t.strftime("%Y%m%dT%H%M%SZ")


def secs(n: int) -> dt.timedelta:
    return dt.timedelta(seconds=n)


# ----------------------------------------------------------------------------- clean plan

def base_plan(rng: random.Random, iid: str, styles: list) -> SimpleNamespace:
    p = SimpleNamespace(id=iid, styles=styles, decoys=[], touched=set(), loop_services=set(), health_services=set())
    p.company = rng.choice(W.COMPANIES)
    dom = p.company["domain"]
    p.ep_public, p.ep_admin = rng.choice([("public", "admin"), ("web", "ops"), ("edge", "mgmt")])
    n_app = rng.randint(1, 3)
    p.networks = {"edge-public": "public", "edge-admin": "admin"}
    for k in range(1, n_app + 1):
        p.networks[f"app-{k}"] = "internal"
    p.networks["data-1"] = "internal"
    p.services, p.routes, p.svc_file, p.extras, p.serve, p.sim_extra = {}, {}, {}, {}, {}, []
    names = ([(n, "public") for n in rng.sample(W.PUBLIC_POOL, rng.randint(4, 7))]
             + [(n, "admin") for n in rng.sample(W.ADMIN_POOL, rng.randint(2, 5))])
    for name, plane in names:
        port = rng.choice(W.PORTS)
        nets = ["edge-public" if plane == "public" else "edge-admin", f"app-{rng.randint(1, n_app)}"]
        if rng.random() < 0.3:
            nets.append("data-1")
        restart = {"policy": rng.choice(W.RESTART)}
        if rng.random() < 0.4:
            restart["max_restarts"] = rng.choice([3, 5, 10])
        if rng.random() < 0.5:
            health = {"kind": "http", "path": rng.choice(W.HEALTH_PATHS)}
            if rng.random() < 0.5:
                health["interval_s"] = rng.choice([10, 30, 60])
        else:
            health = {"kind": "process"}
        p.services[name] = {"plane": plane, "port": port, "networks": nets, "restart": restart, "health": health}
        ep = p.ep_public if plane == "public" else p.ep_admin
        host = f"{name}.{dom}" if plane == "public" or rng.random() < 0.5 else f"{name}.admin.{dom}"
        backend = f"{name}-backend"
        routers = [{"name": f"{name}-main", "entrypoint": ep, "host": host, "path_prefix": "/", "backend": backend}]
        if rng.random() < 0.35:
            prefix = rng.choice(W.EXTRA_PREFIXES)
            extra = {"name": f"{name}-{prefix[1:]}", "entrypoint": ep, "host": host, "path_prefix": prefix,
                     "backend": backend}
            if rng.random() < 0.5:
                extra["priority"] = rng.choice([5, 10, 20])
            routers.append(extra)
        group = rng.choice(W.ROUTE_GROUPS) if plane == "admin" and rng.random() < 0.3 else None
        ext = ".yml" if "yml-ext" in styles and rng.random() < 0.5 else ".yaml"
        rel = f"routes/{group}/{name}{ext}" if group else f"routes/{name}{ext}"
        p.routes[rel] = {"service": name, "routers": routers, "backends": {backend: {"target": f"{name}:{port}"}}}
        p.svc_file[name] = rel
        p.serve[name] = {r["path_prefix"]: 200 for r in routers}
        if health["kind"] == "http":
            p.serve[name][health["path"]] = 200

    p.day = dt.date(2026, 9, 1) + dt.timedelta(days=rng.randint(0, 27))
    p.as_of = (dt.datetime(p.day.year, p.day.month, p.day.day, 5, 30, tzinfo=UTC)
               + secs(rng.randint(0, 3 * 3600 + 29 * 60 + 59)))
    p.at = (rng.randint(0, 3), rng.choice([0, 15, 30, 45]))
    p.at_written = f"{p.at[0]:02d}:{p.at[1]:02d}"
    p.tz = rng.choice(["UTC", "UTC", "Etc/UTC"] + (["Z"] if "tz-alias" in styles else []))
    p.nights = [{"date": p.day - dt.timedelta(days=2 - k), "ran": True, "late_s": rng.randint(0, 240)}
                for k in range(3)]
    if rng.random() < 0.15:
        p.nights[rng.randrange(3)]["late_s"] = rng.randint(1800, 4800)
        p.decoys.append("late_run_within_grace")
    _retention(p, rng, rng.choice(["no_prune", "keep_all", "keep_all", "vbp_small", "pbv_nightly"]))
    p.max_lag = rng.choice([None, 26, 26, 30, 48, 12])
    p.b_mode = ("fresh",)
    p.b_log_status = "ok"
    p.inverted = None
    p.ts = None
    p.damage = None
    p.missed_reason = None
    p.repo_paths = rng.choice([("repo_a", "repo_b"), ("backups/primary", "backups/secondary")])
    p.export_kind = rng.choice(W.EXPORT_KINDS)
    p.long_paths = rng.random() < 0.3
    return p


def _retention(p, rng, mode: str) -> None:
    """Retention settings that are *not* a fault (see the fault ``retention_deletes_only_valid`` for the others)."""
    p.retention_mode = mode
    p.registry_failed = []
    if mode == "no_prune":
        p.chain = ["export", "backup_a", "backup_b", "verify"]
        p.keep_last = rng.choice([3, 7, 14])
        p.registry = rng.choice(["nightly", "weekly", "none"])
    elif mode == "keep_all":
        p.chain = ["export", "backup_a", "backup_b"] + rng.choice([["verify", "prune"], ["prune", "verify"]])
        p.keep_last = rng.choice([3, 5, 7, 14])
        p.registry = rng.choice(["nightly", "weekly", "none"])
    elif mode == "vbp_small":
        p.chain = ["export", "backup_a", "backup_b", "verify", "prune"]
        p.keep_last = rng.choice([1, 2])
        p.registry = rng.choice(["nightly", "weekly", "none"])
        p.decoys.append("small_retention_verify_before_prune")
    elif mode == "pbv_nightly":
        p.chain = ["export", "backup_a", "backup_b", "prune", "verify"]
        p.keep_last = rng.choice([1, 2])
        p.registry = "nightly"
        p.decoys.append("prune_before_verify_with_full_registry")
    elif mode == "safe_two":
        p.chain = ["export", "backup_a", "backup_b"] + rng.choice([["verify", "prune"], ["prune", "verify"]])
        p.keep_last = 2
        p.registry = "nightly"
    else:
        raise ValueError(mode)


# ----------------------------------------------------------------------------- faults

def _pick_service(p, rng, plane=None, avoid=()):
    names = [n for n, s in p.services.items() if (plane is None or s["plane"] == plane) and n not in avoid]
    return rng.choice(names) if names else None


def f_route_missing_service(p, rng) -> dict:
    svc = _pick_service(p, rng, avoid=p.touched)
    rel = p.svc_file[svc]
    doc = p.routes[rel]
    port = p.services[svc]["port"]
    variant = rng.choice(["ghost_target", "undefined_backend", "renamed_service"])
    if variant == "undefined_backend":
        router = rng.choice(doc["routers"])
        router["backend"] = router["backend"].replace("backend", rng.choice(["backnd", "back", "upstream"]))
        items = [router["name"]]
    else:
        if variant == "ghost_target":
            ghost = svc + rng.choice(W.GHOST_SUFFIXES)
        else:
            ghost = rng.choice([n for n in W.PUBLIC_POOL + W.ADMIN_POOL if n not in p.services])
        for spec in doc["backends"].values():
            spec["target"] = f"{ghost}:{port}"
        items = [r["name"] for r in doc["routers"]]
    p.touched.add(svc)
    return {"variant": variant, "file": rel, "items": items}


def f_route_duplicate_shadow(p, rng) -> dict:
    svc = _pick_service(p, rng, avoid=p.touched)
    rel = p.svc_file[svc]
    doc = copy.deepcopy(p.routes[rel])
    plane = p.services[svc]["plane"]
    base, ext = rel.rsplit("/", 1)[1].rsplit(".", 1)
    where = rng.choice(["legacy", "legacy", "sibling"])
    if where == "legacy":
        copy_rel = f"routes/{rng.choice(W.LEGACY_DIRS)}/{base}.{ext}"
    else:
        copy_rel = f"{rel.rsplit('/', 1)[0]}/{base}-{rng.choice(['copy', 'bak2', 'migrated'])}.{ext}"
    variant = rng.choice(["identical", "identical", "other_target", "other_priority"])
    alt = _pick_service(p, rng, plane=plane, avoid=p.touched | {svc})
    if variant == "other_target" and alt is not None:
        doc["routers"] = doc["routers"][:1]   # only the "/" router: every service answers "/"
        for spec in doc["backends"].values():
            spec["target"] = f"{alt}:{p.services[alt]['port']}"
        p.touched.add(alt)
    elif variant == "other_priority":
        for router in doc["routers"]:
            router["priority"] = rng.choice([1, 50, 100])
    else:
        variant = "identical"
    if rng.random() < 0.5:
        for router in doc["routers"]:
            router["name"] = router["name"] + "-legacy"
    p.routes[copy_rel] = doc
    p.touched.add(svc)
    files = sorted([rel, copy_rel])
    return {"variant": f"{where}_{variant}", "file": files[0], "also": files[1:], "items": None}


def f_admin_on_public(p, rng) -> dict:
    svc = _pick_service(p, rng, plane="admin", avoid=p.touched)
    variant = rng.choice(["entrypoint", "both_entrypoints", "network"])
    p.touched.add(svc)
    if variant == "network":
        nets = p.services[svc]["networks"]
        if rng.random() < 0.5:
            nets.append("edge-public")
        else:
            nets[0] = "edge-public"
        return {"variant": variant, "file": TOPOLOGY_FILE, "items": [svc]}
    rel = p.svc_file[svc]
    routers = p.routes[rel]["routers"]
    pos = rng.randrange(len(routers))
    old = routers[pos]
    new = {}
    for key, value in old.items():
        if key == "entrypoint":
            if variant == "entrypoint":
                new["entrypoint"] = p.ep_public
            else:
                new["entrypoints"] = [p.ep_admin, p.ep_public]
        else:
            new[key] = value
    routers[pos] = new
    return {"variant": variant, "file": rel, "items": [new["name"]]}


def f_health_probes_process(p, rng) -> dict:
    svc = _pick_service(p, rng, avoid=p.touched | p.loop_services)
    doc = p.routes[p.svc_file[svc]]
    prefix = rng.choice(doc["routers"])["path_prefix"]
    status = rng.choice([404, 404, 404, 500, 503])
    since = rng.choice(["start", "start", "change"])
    broken = dict(p.serve[svc])
    if status == 404 and rng.random() < 0.5:
        del broken[prefix]
    else:
        broken[prefix] = status
    if since == "start":
        p.serve[svc] = broken
    else:
        t0 = p.as_of - secs(rng.randint(180, 1500))
        p.sim_extra.append({"kind": "serve", "t": z(t0), "service": svc, "map": broken})
    p.health_services.add(svc)
    p.touched.add(svc)
    return {"variant": f"{since}_{status}_{p.services[svc]['health']['kind']}", "file": TOPOLOGY_FILE, "items": [svc]}


def f_restart_loop(p, rng) -> dict:
    svc = _pick_service(p, rng, avoid=p.touched | p.health_services)
    count = rng.randint(4, 8)
    first = p.as_of - secs(rng.randint(560, 1700))
    offsets = sorted(rng.sample(range(0, 480), count))
    for off in offsets:
        p.sim_extra.append({"kind": "restart", "t": z(first + secs(off)), "service": svc, "down_s": rng.randint(3, 40)})
    p.loop_services.add(svc)
    p.touched.add(svc)
    return {"variant": f"restarts_{count}", "file": TRACE_FILE, "items": [svc]}


def f_repo_b_stale(p, rng) -> dict:
    variant = rng.choice(["days_behind", "days_behind", "one_night_strict", "empty"])
    if variant == "days_behind":
        p.b_mode = ("behind", rng.randint(2, 5))
        p.max_lag = rng.choice([None, 26, 30])
    elif variant == "one_night_strict":
        p.b_mode = ("skip_last",)
        p.max_lag = rng.choice([6, 12])
    else:
        p.b_mode = ("empty",)
    p.b_log_status = rng.choice(["ok", "ok", "ok", "failed"])
    return {"variant": variant, "file": p.repo_paths[1], "items": "B_NEWEST"}


def f_backup_truncated(p, rng) -> dict:
    p.damage = rng.choice(["pack_tail", "listing_short", "blocks_cut"])
    _make_safe(p, rng)
    return {"variant": p.damage, "file": p.repo_paths[0], "items": "A_NEWEST", "mismatched_files": "DAMAGE"}


def f_block_corrupt(p, rng) -> dict:
    p.damage = "corrupt"
    _make_safe(p, rng)
    return {"variant": "byte_flip", "file": p.repo_paths[0], "items": "A_NEWEST", "mismatched_files": "DAMAGE"}


def f_job_order_inverted(p, rng) -> dict:
    _make_safe(p, rng)
    variant = rng.choice(["policy", "trace_overlap", "trace_before"])
    if variant == "policy":
        i, j = p.chain.index("export"), p.chain.index("backup_a")
        p.chain[i], p.chain[j] = p.chain[j], p.chain[i]
        p.inverted = ("policy", "stale")
        return {"variant": variant, "file": POLICY_FILE, "items": ["chain"], "mismatched_files": "EXPORT"}
    p.inverted = ("trace", "overlap" if variant == "trace_overlap" else "before",
                  rng.choice(["stale", "partial"]) if variant == "trace_overlap" else "stale")
    return {"variant": f"{variant}_{p.inverted[2]}", "file": TRACE_FILE, "items": "LATEST_RUN",
            "mismatched_files": "EXPORT"}


def f_window_missed(p, rng) -> dict:
    variant = rng.choice(["latest", "middle", "last_two"])
    missed = {"latest": [2], "middle": [1], "last_two": [1, 2]}[variant]
    for k in missed:
        p.nights[k]["ran"] = False
    p.missed_reason = rng.choice(["host_off", "runner_disabled"])
    return {"variant": variant, "file": TRACE_FILE, "items": [p.nights[missed[0]]["date"].isoformat()]}


def f_retention_deletes_only_valid(p, rng) -> dict:
    variant = rng.choice(["prune_first_stale_registry", "prune_first_stale_registry", "keep_zero",
                          "latest_drill_failed"])
    p.registry_failed = []
    if variant == "keep_zero":
        p.chain = ["export", "backup_a", "backup_b"] + rng.choice([["verify", "prune"], ["prune", "verify"]])
        p.keep_last = 0
        p.registry = rng.choice(["nightly", "weekly"])
    else:
        p.chain = ["export", "backup_a", "backup_b", "prune", "verify"]
        p.keep_last = rng.choice([1, 2])
        if variant == "prune_first_stale_registry":
            p.registry = "superseded_only"
        else:
            p.registry = "nightly"
            p.registry_failed = "KEPT"
    p.retention_mode = "fault_" + variant
    return {"variant": variant, "file": POLICY_FILE, "items": ["retention"]}


def f_tz_mixed(p, rng) -> dict:
    variant = rng.choice(["schedule_local", "job_offsets", "job_naive"])
    if variant == "schedule_local":
        p.tz = rng.choice(["Europe/Rome", "Europe/Rome", "CET", "local"])
        p.at_written = f"{p.at[0] + 2:02d}:{p.at[1]:02d}"
        return {"variant": variant, "file": POLICY_FILE, "items": ["schedule"]}
    subset = rng.choice(["all", "one_step", "one_run"])
    p.ts = ("offset" if variant == "job_offsets" else "naive", subset, rng.choice(["T", "T", " "]))
    return {"variant": f"{variant}_{subset}", "file": TRACE_FILE, "items": ["job-timestamps"]}


def _make_safe(p, rng) -> None:
    """Data faults make the newest drill fail: keep retention out of the way unless it is itself the fault."""
    if p.retention_mode.startswith("fault_"):
        return
    ran = sum(1 for n in p.nights if n["ran"])
    modes = ["no_prune", "keep_all"] + (["safe_two"] if ran >= 3 else [])
    if p.retention_mode not in ("no_prune", "keep_all"):
        p.decoys = [d for d in p.decoys if "retention" not in d and "prune" not in d]
        _retention(p, rng, rng.choice(modes))


FAULTS = {"route_missing_service": f_route_missing_service, "route_duplicate_shadow": f_route_duplicate_shadow,
          "admin_on_public": f_admin_on_public, "health_probes_process": f_health_probes_process,
          "restart_loop": f_restart_loop, "repo_b_stale": f_repo_b_stale, "backup_truncated": f_backup_truncated,
          "block_corrupt": f_block_corrupt, "job_order_inverted": f_job_order_inverted,
          "window_missed": f_window_missed, "retention_deletes_only_valid": f_retention_deletes_only_valid,
          "tz_mixed": f_tz_mixed}


# ----------------------------------------------------------------------------- decoys

def decoys(p, rng) -> None:
    free = [n for n in p.services if n not in p.touched]
    if free and rng.random() < 0.3:
        svc = rng.choice(free)
        rel = p.svc_file[svc]
        main = p.routes[rel]["routers"][0]
        if rng.random() < 0.5:
            p.routes[rel]["routers"].append(dict(main, name=f"{svc}-old", enabled=False))
        else:
            legacy = f"routes/{rng.choice(W.LEGACY_DIRS)}/{rel.rsplit('/', 1)[1]}"
            if legacy not in p.routes:
                doc = copy.deepcopy(p.routes[rel])
                for router in doc["routers"]:
                    router["enabled"] = False
                p.routes[legacy] = doc
        p.decoys.append("disabled_duplicate")
    if free and rng.random() < 0.35:
        svc = rng.choice(free)
        rel = p.svc_file[svc]
        text = yamlout.emit(p.routes[rel])
        name = rng.choice([f"{rel}.disabled", f"{rel}.orig", f"routes/{rng.choice(W.LEGACY_DIRS)}/{svc}.yaml.txt"])
        p.extras[name] = text
        p.extras["routes/README.txt"] = ("One file per service. Files that do not end in .yaml or .yml are not loaded.\n"
                                         "Synthetic lab: fictitious company, invented topology.\n").encode("utf-8")
        p.decoys.append("not_a_route_file")
    calm = [n for n in p.services if n not in p.loop_services]
    if calm and rng.random() < 0.3:
        svc = rng.choice(calm)
        t1 = p.as_of - secs(rng.randint(700, 1700))
        p.sim_extra.append({"kind": "restart", "t": z(t1), "service": svc, "down_s": rng.randint(5, 55)})
        if rng.random() < 0.5:
            p.sim_extra.append({"kind": "restart", "t": z(t1 + secs(rng.randint(300, 640))), "service": svc,
                                "down_s": rng.randint(5, 55)})
        p.decoys.append("isolated_restarts")
        calm = [n for n in calm if n != svc]
    quiet = [n for n in calm if n not in p.health_services]
    if quiet and rng.random() < 0.2:
        svc = rng.choice(quiet)
        t1 = p.as_of - secs(rng.randint(120, 1600))
        blip = dict(p.serve[svc])
        blip["/"] = 503
        p.sim_extra.append({"kind": "serve", "t": z(t1), "service": svc, "map": blip})
        p.sim_extra.append({"kind": "serve", "t": z(t1 + secs(rng.randint(20, 50))), "service": svc,
                            "map": dict(p.serve[svc])})
        p.decoys.append("one_failing_beat")


# ----------------------------------------------------------------------------- materialise

def _text(rng, size: int) -> bytes:
    out, n = [], 0
    while n < size:
        piece = rng.choice(W.WORDS) + rng.choice([" ", " ", "; ", "\n", " è ", " più ", " qualità "])
        out.append(piece)
        n += len(piece.encode("utf-8"))
    return "".join(out).encode("utf-8")[:size]


def _export(rng, kind: str, day: dt.date) -> bytes:
    rows = [f"{kind};data;riga;quantità\n"]
    for i in range(rng.randint(30, 120)):
        rows.append(f"{kind};{day.isoformat()};{i + 1};{rng.randint(1, 99999)}\n")
    return "".join(rows).encode("utf-8")


def _tree(rng, long_paths: bool) -> dict:
    files = {}
    for i in range(rng.randint(8, 18)):
        rel = f"{rng.choice(W.DIRS)}/{i + 1:02d} {rng.choice(W.FILE_NAMES)}"
        files[rel] = _text(rng, rng.choice([rng.randint(40, 900), rng.randint(1100, 5200)]))
    if long_paths:
        for i in range(rng.randint(1, 2)):
            parts = rng.sample(W.LONG_PARTS, rng.randint(3, 4))
            files[f"archivio/{'/'.join(parts)}/{i + 1:02d} {rng.choice(W.FILE_NAMES)}"] = _text(rng, rng.randint(300, 3000))
    names = sorted(files)
    if rng.random() < 0.3:
        files["archivio/copia di riserva " + names[0].rsplit("/", 1)[1]] = files[names[0]]
    if rng.random() < 0.2:
        files["documenti/vuoto.txt"] = b""
    return files


def _day_changes(state: dict, rng, export_rel: str) -> None:
    names = sorted(n for n in state if n != export_rel)
    for name in rng.sample(names, min(len(names), rng.randint(1, 3))):
        state[name] = state[name] + _text(rng, rng.randint(20, 1500))
    if rng.random() < 0.3:
        state[f"{rng.choice(W.DIRS)}/nuovo {len(state):02d} {rng.choice(W.FILE_NAMES)}"] = _text(rng, rng.randint(100, 2500))


def _digest(files: dict) -> str:
    return _fs.digest((rel, _fs.sha(data)) for rel, data in files.items())


def materialise(p, rng, faults: list, emit_seed: str) -> tuple[dict, list]:
    """Turn the plan into files. Returns ``(files, gold faults)`` with every placeholder resolved."""
    export_rel = f"export/{p.export_kind}-export.csv"
    state = _tree(rng, p.long_paths)
    state[export_rel] = _export(rng, p.export_kind, p.nights[0]["date"] - dt.timedelta(days=1))
    repo_a, repo_b = MemRepo(), MemRepo()
    jobs, reg, events = [], [], []
    ran = [n for n in p.nights if n["ran"]]
    last = ran[-1]

    def window(day: dt.date) -> dt.datetime:
        return dt.datetime(day.year, day.month, day.day, p.at[0], p.at[1], tzinfo=UTC)

    if p.b_mode[0] == "behind":
        for back in (p.b_mode[1] + 1, p.b_mode[1]):
            t = window(p.day - dt.timedelta(days=back)) + secs(rng.randint(200, 700))
            repo_b.add_snapshot(sid(t), z(t), dict(state))
    if p.registry in ("weekly", "superseded_only"):
        t = window(p.day - dt.timedelta(days=rng.choice([7, 8, 9]))) + secs(rng.randint(600, 900))
        reg.append({"as_of": z(t + secs(300)), "repo": "a", "snapshot": sid(t), "result": "ok", "source_count": len(state),
                    "restored_count": len(state), "mismatched": [], "problems": 0,
                    "source_manifest_sha256": _digest(state)})

    a_ids = []
    for pos, night in enumerate(ran):
        if pos > 0:
            _day_changes(state, rng, export_rel)
        fresh = _export(rng, p.export_kind, night["date"])
        run_id = night["date"].isoformat()
        t = window(night["date"]) + secs(night["late_s"])
        durations = {"export": rng.randint(40, 240), "backup_a": rng.randint(20, 200), "backup_b": rng.randint(20, 200),
                     "verify": rng.randint(20, 120), "prune": rng.randint(1, 10)}
        times = {}
        chain = list(p.chain)
        trace_inv = p.inverted is not None and p.inverted[0] == "trace" and night is last
        if trace_inv:
            start = t + secs(rng.randint(1, 20))
            if p.inverted[1] == "overlap":
                times["export"] = (start, start + secs(durations["export"]))
                a_start = start + secs(rng.randint(5, durations["export"] - 5))
                times["backup_a"] = (a_start, a_start + secs(durations["backup_a"]))
            else:
                times["backup_a"] = (start, start + secs(durations["backup_a"]))
                e_start = times["backup_a"][1] + secs(rng.randint(1, 20))
                times["export"] = (e_start, e_start + secs(durations["export"]))
            t = max(times["export"][1], times["backup_a"][1])
            chain = [s for s in chain if s not in ("export", "backup_a")]
        for step in chain:
            start = t + secs(rng.randint(1, 20))
            times[step] = (start, start + secs(durations[step]))
            t = times[step][1]
        inverted_here = trace_inv or (p.inverted is not None and p.inverted[0] == "policy")
        a_state = dict(state)
        if not inverted_here:
            a_state[export_rel] = fresh
        elif trace_inv and p.inverted[2] == "partial":
            a_state[export_rel] = fresh[:rng.randint(1, len(fresh) - 1)]
        state[export_rel] = fresh
        a_id = sid(times["backup_a"][0])
        repo_a.add_snapshot(a_id, z(times["backup_a"][0]), a_state)
        a_ids.append(a_id)
        b_here = p.b_mode[0] == "fresh" or (p.b_mode[0] == "skip_last" and night is not last)
        if b_here:
            repo_b.add_snapshot(sid(times["backup_b"][0]), z(times["backup_b"][0]), dict(state))
        verified_here = p.registry == "nightly" or (p.registry == "weekly" and pos == 0 and rng.random() < 0.5)
        night.update({"a_id": a_id, "times": times, "verified": verified_here, "count": len(a_state),
                      "digest": _digest(state), "run": run_id})
        for step in sorted(times, key=lambda s: times[s][0]):
            if step == "verify" and not verified_here and p.registry != "none":
                continue
            status = p.b_log_status if step == "backup_b" and not b_here else "ok"
            jobs.append({"kind": "job", "run": run_id, "step": step, "start": times[step][0], "end": times[step][1],
                         "status": status})
        if verified_here:
            reg.append({"as_of": z(times["verify"][1]), "repo": "a", "snapshot": a_id, "result": "ok",
                        "source_count": len(a_state), "restored_count": len(a_state), "mismatched": [], "problems": 0,
                        "source_manifest_sha256": night["digest"]})

    if p.registry == "superseded_only":
        kept = a_ids[len(a_ids) - p.keep_last:]
        for night in ran:
            if night["a_id"] not in kept and "verify" in night["times"]:
                reg.append({"as_of": z(night["times"]["verify"][1]), "repo": "a", "snapshot": night["a_id"],
                            "result": "ok", "source_count": night["count"], "restored_count": night["count"],
                            "mismatched": [], "problems": 0, "source_manifest_sha256": night["digest"]})
                jobs.append({"kind": "job", "run": night["run"], "step": "verify", "start": night["times"]["verify"][0],
                             "end": night["times"]["verify"][1], "status": "ok"})
    if p.registry_failed == "KEPT":
        kept = a_ids[len(a_ids) - p.keep_last:]
        for night in ran:
            if night["a_id"] in kept:
                t = night["times"]["verify"][1] + secs(rng.randint(600, 3000))
                reg.append({"as_of": z(t), "repo": "a", "snapshot": night["a_id"], "result": "failed",
                            "source_count": night["count"], "restored_count": night["count"] - 1,
                            "mismatched": [export_rel], "problems": 1, "source_manifest_sha256": night["digest"]})

    # ---- damage to repository A (after it is complete, before anything is written)
    newest = repo_a.newest()
    mismatched = None
    if p.damage == "blocks_cut" and not any(len(e["blocks"]) >= 2 for e in newest["files"]):
        p.damage = "listing_short"
    if p.damage == "pack_tail":
        end = max(repo_a.blocks[b][0] + repo_a.blocks[b][1] for e in newest["files"] for b in e["blocks"])
        new_len = end - rng.randint(1, min(end - 1, 3000))
        del repo_a.pack[new_len:]
        mismatched = [e["path"] for e in newest["files"]
                      if any(repo_a.blocks[b][0] + repo_a.blocks[b][1] > new_len for b in e["blocks"])]
    elif p.damage == "listing_short":
        drop = rng.randint(1, min(3, len(newest["files"]) - 1))
        gone = rng.sample(range(len(newest["files"])), drop)
        mismatched = [newest["files"][i]["path"] for i in gone]
        newest["files"] = [e for i, e in enumerate(newest["files"]) if i not in gone]
    elif p.damage == "blocks_cut":
        entry = rng.choice([e for e in newest["files"] if len(e["blocks"]) >= 2])
        entry["blocks"] = entry["blocks"][:len(entry["blocks"]) - rng.randint(1, len(entry["blocks"]) - 1)]
        mismatched = [entry["path"]]
    elif p.damage == "corrupt":
        addresses = sorted({b for e in newest["files"] for b in e["blocks"]})
        chosen = rng.sample(addresses, min(len(addresses), rng.randint(1, 2)))
        for address in chosen:
            off, length = repo_a.blocks[address]
            pos = rng.randrange(length)
            for i in range(rng.randint(1, min(8, length - pos))):
                repo_a.pack[off + pos + i] ^= rng.randint(1, 255)
        mismatched = [e["path"] for e in newest["files"] if any(b in chosen for b in e["blocks"])]

    # ---- job timestamps as written (the instants above stay the truth)
    def written(job: dict, field: str) -> str:
        t = job[field]
        if p.ts is not None:
            kind, subset, sep = p.ts
            hit = (subset == "all" or (subset == "one_step" and job["step"] == ts_step)
                   or (subset == "one_run" and job["run"] == ts_run))
            if hit and not (protect and job["step"] in ("export", "backup_a")):
                local = (t + dt.timedelta(hours=2)).strftime(f"%Y-%m-%d{sep if kind == 'naive' else 'T'}%H:%M:%S")
                return local + ("+02:00" if kind == "offset" else "")
        return z(t)

    protect = p.inverted is not None and p.inverted[0] == "trace"
    steps_present = sorted({j["step"] for j in jobs} - ({"export", "backup_a"} if protect else set()))
    ts_step = rng.choice(steps_present) if steps_present else None
    ts_run = rng.choice(sorted({j["run"] for j in jobs}))

    # ---- simulation events
    t0 = p.as_of - dt.timedelta(hours=rng.randint(20, 40))
    for i, name in enumerate(p.services):
        events.append((t0 + secs(i), {"kind": "proc", "t": z(t0 + secs(i)), "service": name, "state": "up"}))
        events.append((t0 + secs(i), {"kind": "serve", "t": z(t0 + secs(i)), "service": name, "map": p.serve[name]}))
    if p.missed_reason == "host_off":
        missed = [n for n in p.nights if not n["ran"]]
        off = window(missed[0]["date"]) - secs(rng.randint(1800, 9000))
        on = window(missed[-1]["date"]) + secs(5400 + rng.randint(300, 1200))
        if on <= p.as_of - secs(2400):
            events.append((off, {"kind": "host", "t": z(off), "state": "off"}))
            events.append((on, {"kind": "host", "t": z(on), "state": "on"}))
            for i, name in enumerate(p.services):
                events.append((off, {"kind": "proc", "t": z(off), "service": name, "state": "down"}))
                events.append((on + secs(5 + i), {"kind": "proc", "t": z(on + secs(5 + i)), "service": name,
                                                  "state": "up"}))
        else:
            p.missed_reason = "runner_disabled"
    for ev in p.sim_extra:
        events.append((dt.datetime.strptime(ev["t"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC), ev))
    for job in jobs:
        events.append((job["start"], dict(job, start=written(job, "start"), end=written(job, "end"))))
    events.sort(key=lambda pair: pair[0])
    trace = "".join(compact(ev) + "\n" for _, ev in events).encode("utf-8")

    # ---- documents
    def emit(rel: str, doc) -> bytes:
        return yamlout.emit(doc, p.styles, random.Random(f"{emit_seed}:{rel}"))

    style_rng = random.Random(f"{emit_seed}:model")
    topology = {"company": p.company["name"], "domain": p.company["domain"],
                "entrypoints": {p.ep_public: {"plane": "public", "network": "edge-public"},
                                p.ep_admin: {"plane": "admin", "network": "edge-admin"}},
                "networks": {name: {"plane": plane} for name, plane in p.networks.items()},
                "services": copy.deepcopy(p.services)}
    if "port-string" in p.styles:
        for spec in topology["services"].values():
            if style_rng.random() < 0.5:
                spec["port"] = str(spec["port"])
    b_spec = {"path": p.repo_paths[1]}
    if p.max_lag is not None:
        b_spec["max_lag_hours"] = p.max_lag
    policy = {"schedule": {"at": p.at_written, "tz": p.tz}, "chain": p.chain, "source": "source",
              "export_path": f"source/{export_rel}", "repos": {"a": {"path": p.repo_paths[0]}, "b": b_spec},
              "retention": {"keep_last": p.keep_last}}
    files = {"instance.json": canonical({"instance_id": p.id, "as_of": z(p.as_of), "company": p.company["name"],
                                        "synthetic": True}),
             TOPOLOGY_FILE: emit(TOPOLOGY_FILE, topology), POLICY_FILE: emit(POLICY_FILE, policy), TRACE_FILE: trace}
    for rel, doc in p.routes.items():
        files[rel] = emit(rel, _styled_route(doc, p.styles, style_rng))
    files.update(p.extras)
    for rel, data in state.items():
        files[f"source/{rel}"] = data
    for rel, data in repo_a.files().items():
        files[f"{p.repo_paths[0]}/{rel}"] = data
    for rel, data in repo_b.files().items():
        files[f"{p.repo_paths[1]}/{rel}"] = data
    if reg:
        files["drills.jsonl"] = registry_bytes(reg)

    # ---- gold: resolve the placeholders left by the fault functions
    resolved = []
    for cls, gold in faults:
        gold = dict(gold)
        if gold["items"] == "A_NEWEST":
            gold["items"] = [newest["id"]]
        elif gold["items"] == "B_NEWEST":
            gold["items"] = [max(repo_b.snapshots)] if repo_b.snapshots else [None]
        elif gold["items"] == "LATEST_RUN":
            gold["items"] = [last["run"]]
        if gold.get("mismatched_files") == "DAMAGE":
            gold["mismatched_files"] = sorted(mismatched)
        elif gold.get("mismatched_files") == "EXPORT":
            gold["mismatched_files"] = [export_rel]
        resolved.append({"class": cls, "verdict": VERDICT[cls], "variant": gold["variant"], "file": gold["file"],
                         "also": gold.get("also", []), "items": gold["items"],
                         "mismatched_files": gold.get("mismatched_files", [])})
    return files, resolved


def _styled_route(doc: dict, styles: list, rng) -> dict:
    if not {"host-case", "prefix-slash", "entrypoints-list", "port-string"} & set(styles):
        return doc
    out = copy.deepcopy(doc)
    routers = []
    for router in out["routers"]:
        new = {}
        for key, value in router.items():
            if key == "host" and "host-case" in styles and rng.random() < 0.6:
                value = ".".join(part.capitalize() for part in value.split("."))
            elif key == "path_prefix" and "prefix-slash" in styles and value != "/" and rng.random() < 0.7:
                value = value + "/"
            elif key == "priority" and "port-string" in styles and rng.random() < 0.5:
                value = str(value)
            if key == "entrypoint" and "entrypoints-list" in styles and rng.random() < 0.7:
                new["entrypoints"] = [value]
            else:
                new[key] = value
        routers.append(new)
    out["routers"] = routers
    return out


# ----------------------------------------------------------------------------- suites

def fault_plan(seed: int, count: int, per_instance: int) -> list:
    rng = random.Random(f"av2:{seed}:plan")
    faulty = round(count * FAULTY_SHARE)
    plan = []
    for i in range(faulty):
        first = CLASSES[i % len(CLASSES)]
        classes = [first]
        if per_instance == 2:
            classes.append(rng.choice([c for c in CLASSES if AREA[c] != AREA[first]]))
        plan.append(sorted(classes, key=lambda c: (_APPLY_ORDER[AREA[c]], c)))
    plan.extend([] for _ in range(count - faulty))
    rng.shuffle(plan)
    return plan


def instance(seed: int, index: int, prefix: str, classes: list, styles: list) -> tuple[dict, dict]:
    """Build instance number ``index`` (1-based). Returns ``(files, gold record)``."""
    rng = random.Random(f"av2:{seed}:{index}")
    iid = f"{prefix}-{index:04d}"
    mine = []
    if styles:
        srng = random.Random(f"av2:{seed}:{index}:styles")
        mine = sorted(srng.sample(styles, min(len(styles), srng.randint(1, 3))))
    p = base_plan(rng, iid, mine)
    faults = [(cls, FAULTS[cls](p, rng)) for cls in classes]
    decoys(p, rng)
    files, gold_faults = materialise(p, rng, faults, f"av2:{seed}:{index}:emit")
    verdict = next((v for v in PRECEDENCE if v in {g["verdict"] for g in gold_faults}), "OK")
    gold = {"instance": iid, "as_of": z(p.as_of), "company": p.company["slug"], "clean": not gold_faults,
            "verdict": verdict, "faults": gold_faults, "styles": mine, "decoys": sorted(p.decoys),
            "files": len(files), "sha256": _digest(files)}
    return files, gold


def generate(seed: int, prefix: str, count: int = COUNT, per_instance: int = 1, styles=(), out=None):
    """Yield gold records; write instances under ``out`` when given (the directory is rebuilt)."""
    plan = fault_plan(seed, count, per_instance)
    if out is not None:
        _fs.wipe(out)
    golds = []
    for index in range(1, count + 1):
        files, gold = instance(seed, index, prefix, plan[index - 1], list(styles))
        if out is not None:
            for rel, data in files.items():
                _fs.put(os.path.join(out, gold["instance"]), rel, data)
        golds.append(gold)
    return golds


def gold_bytes(golds: list) -> bytes:
    return "".join(compact(g) + "\n" for g in golds).encode("utf-8")


def manifest_bytes(suite: str, golds: list) -> bytes:
    lines = [f"{g['sha256']}  {suite}/{g['instance']}\n" for g in golds]
    total = _fs.digest((f"{suite}/{g['instance']}", g["sha256"]) for g in golds)
    return ("".join(lines) + f"{total}  {suite}/TOTAL\n").encode("utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--suite", choices=sorted(SUITES), help="a named suite (seed, faults per instance and styles are fixed)")
    ap.add_argument("--seed", type=int, help="seed of an ad-hoc suite (blind runs); named-suite seeds are refused")
    ap.add_argument("--prefix", help="instance id prefix of an ad-hoc suite (default: s<seed>)")
    ap.add_argument("--count", type=int, default=COUNT)
    ap.add_argument("--faults-per-instance", type=int, choices=(1, 2), default=1)
    ap.add_argument("--styles", default="", help="comma-separated writing/model styles (see corpus/yamlout.py)")
    ap.add_argument("--out", help="directory to write the instances into (rebuilt from scratch)")
    ap.add_argument("--gold", help="file to write the gold labels into (JSON Lines)")
    ap.add_argument("--check", action="store_true", help="regenerate dev in memory; compare with MANIFEST.sha256 and gold")
    ap.add_argument("--write-manifest", action="store_true", help="(re)write corpus/MANIFEST.sha256 and corpus/gold/dev.labels.jsonl")
    args = ap.parse_args(argv)

    if args.check or args.write_manifest:
        golds = generate(SUITES["dev"]["seed"], "dev")
        manifest, labels = manifest_bytes("dev", golds), gold_bytes(golds)
        if args.write_manifest:
            _fs.put(HERE, "MANIFEST.sha256", manifest)
            _fs.put(HERE, "gold/dev.labels.jsonl", labels)
            print(f"written: MANIFEST.sha256 ({len(golds)} instances), gold/dev.labels.jsonl")
            return 0
        ok = _fs.get(HERE, "MANIFEST.sha256") == manifest and _fs.get(HERE, "gold/dev.labels.jsonl") == labels
        print(f"corpus dev: {len(golds)} instances regenerated - " + ("identical to MANIFEST.sha256 and gold" if ok else "DIFFERENT"))
        return 0 if ok else 1

    if args.suite:
        spec = SUITES[args.suite]
        seed, prefix, per, styles = spec["seed"], args.suite, spec["faults"], spec["styles"]
    else:
        if args.seed is None:
            ap.error("give --suite, --seed or --check")
        if args.seed in {s["seed"] for s in SUITES.values()}:
            ap.error("this seed belongs to a named suite: a blind run needs a seed never used before")
        styles = [s for s in args.styles.split(",") if s]
        unknown = [s for s in styles if s not in yamlout.ALL_STYLES + ["tz-alias"]]
        if unknown:
            ap.error(f"unknown style(s): {', '.join(unknown)}")
        seed, prefix, per = args.seed, args.prefix or f"s{args.seed}", args.faults_per_instance
    golds = generate(seed, prefix, args.count, per, styles, args.out)
    if args.gold:
        _fs.put(os.path.dirname(os.path.abspath(args.gold)), os.path.basename(args.gold), gold_bytes(golds))
    clean = sum(1 for g in golds if g["clean"])
    print(f"seed {seed}: {len(golds)} instances ({len(golds) - clean} with planted faults, {clean} clean)"
          + (f" -> {args.out}" if args.out else " (not written)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
