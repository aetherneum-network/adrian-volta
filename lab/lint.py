"""Route lint: computes facts about files, routers and services; ``rules/route_lint.json`` decides.

The result carries two tables. ``provisional`` is what the file provider would load as things
are (used by the end-to-end probe). ``accepted`` is the table the lint lets through: it is
``None`` as soon as one finding exists - a refused table is not published in part.
"""
from __future__ import annotations

from lab import findings as F
from lab import routes, rules_engine, topology

RULES = "route_lint.json"


def router_facts(r: routes.Router, topo: topology.Topology, dup_keys: set) -> dict:
    known = all(ep in topo.entrypoints for ep in r.entrypoints)
    svc = topo.services.get(r.target_service) if r.target_service else None
    facts = {
        "enabled": r.enabled,
        "entrypoints_known": known,
        "on_public_entrypoint": known and any(topo.entrypoints[ep] == "public" for ep in r.entrypoints),
        "backend_defined": r.target_service is not None,
        "target_exists": svc is not None,
        "duplicate": any((ep, r.host, r.path_prefix) in dup_keys for ep in r.entrypoints),
    }
    if svc is not None:
        facts["target_plane"] = svc.plane
        facts["port_matches"] = r.target_port == svc.port
    return facts


def duplicate_groups(routers: list) -> dict:
    """``(entrypoint, host, path_prefix)`` -> enabled routers sharing it, for keys with two or more."""
    by_key: dict = {}
    for r in routers:
        if not r.enabled:
            continue
        for ep in r.entrypoints:
            by_key.setdefault((ep, r.host, r.path_prefix), []).append(r)
    return {k: v for k, v in by_key.items() if len(v) > 1}


def run(instance_dir, topo: topology.Topology) -> dict:
    rules = rules_engine.load(RULES)
    routers, problems, files = routes.load(instance_dir)
    found: list[dict] = []
    for rel, reason in problems:
        then, rid = rules.decide("file_rules", {"readable": False})
        found.append(F.make(then["class"], rid, rel, None, detail={"reason": reason}))
    groups = duplicate_groups(routers)
    dup_routers: dict = {}
    for r in routers:
        then, rid = rules.decide("router_rules", router_facts(r, topo, set(groups)))
        cls = then.get("class")
        if rid is None or cls is None:
            continue
        if cls == "route_duplicate_shadow":
            dup_routers[(r.file, r.name)] = rid
            continue
        detail = {"entrypoints": list(r.entrypoints), "host": r.host, "path_prefix": r.path_prefix,
                  "backend": r.backend, "target": None if r.target_service is None else f"{r.target_service}:{r.target_port}"}
        found.append(F.make(cls, rid, r.file, r.name, detail=detail))
    # one finding per duplicate group that still has a router decided by the duplicate rule
    seen_groups = set()
    for key in sorted(groups):
        members = groups[key]
        rids = [dup_routers.get((m.file, m.name)) for m in members]
        if not any(rids):
            continue
        ident = tuple(sorted((m.file, m.name) for m in members))
        if ident in seen_groups:
            continue
        seen_groups.add(ident)
        files_in_group = sorted({m.file for m in members})
        detail = {"entrypoint": key[0], "host": key[1], "path_prefix": key[2],
                  "routers": [{"file": m.file, "router": m.name, "priority": m.priority,
                               "target": f"{m.target_service}:{m.target_port}"} for m in sorted(members, key=lambda m: m.order)],
                  "winner": max(members, key=lambda m: (m.priority, -m.order)).name}
        found.append(F.make("route_duplicate_shadow", next(r for r in rids if r), files_in_group[0],
                            None, also=files_in_group[1:], detail=detail))
    for name in sorted(topo.services):
        svc = topo.services[name]
        facts = {"plane": svc.plane, "on_public_network": any(topo.networks[n] == "public" for n in svc.networks)}
        then, rid = rules.decide("service_rules", facts)
        if rid and then.get("class"):
            found.append(F.make(then["class"], rid, topology.FILE, name,
                                detail={"networks": list(svc.networks),
                                        "public_networks": [n for n in svc.networks if topo.networks[n] == "public"]}))
    provisional = routes.table(routers)
    return {"findings": F.ordered(found), "routers": routers, "files": files, "provisional": provisional,
            "accepted": None if found else provisional}


def reach_matrix(entries: list[dict] | None, topo: topology.Topology) -> dict:
    """Entrypoint-to-service reachability of a table, plus the admin-public edges it contains."""
    matrix = {ep: {svc: False for svc in sorted(topo.services)} for ep in sorted(topo.entrypoints)}
    for e in entries or []:
        if e["entrypoint"] in matrix and e["target_service"] in topo.services:
            matrix[e["entrypoint"]][e["target_service"]] = True
    edges = sorted([ep, svc] for ep, row in matrix.items() for svc, hit in row.items()
                   if hit and topo.entrypoints[ep] == "public" and topo.services[svc].plane == "admin")
    return {"matrix": matrix, "admin_public_edges": edges}
