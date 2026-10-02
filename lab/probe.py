"""End-to-end probe: asks each route what a user would ask, beat after beat, on the virtual clock.

A healthcheck that only looks at the process can say *healthy* while the route answers 404.
The probe goes through the route table (entrypoint, host, path prefix), reaches the backend the
table resolves to and reads the status the backend gives at that instant. Facts per service go
to ``rules/health.json``; a failure is declared at the second consecutive failing beat.
"""
from __future__ import annotations

import datetime as dt

from lab import findings as F
from lab import routes, rules_engine, timeutil
from lab.sim import World
from lab.topology import Topology

RULES = "health.json"


def beats(as_of: dt.datetime, params: dict) -> list[dt.datetime]:
    n, step = int(params["probe_window_beats"]), int(params["beat_s"])
    return [as_of - dt.timedelta(seconds=step * (n - k)) for k in range(1, n + 1)]


def health_says(world: World, svc, t: dt.datetime) -> bool:
    """What the *declared* healthcheck reports: the process, or a direct request that bypasses the route."""
    if svc.health_kind == "process":
        return world.proc_up(svc.name, t)
    return 200 <= world.status(svc.name, svc.health_path, t) < 400


def _longest(flags: list) -> tuple[int, int]:
    """``(length, start_index)`` of the longest run of true values (the first one on ties)."""
    best, best_start, run, start = 0, -1, 0, 0
    for i, flag in enumerate(flags):
        if not flag:
            run = 0
            continue
        if run == 0:
            start = i
        run += 1
        if run > best:
            best, best_start = run, start
    return best, best_start


def max_restarts(times: list[dt.datetime], lo: dt.datetime, hi: dt.datetime, window_s: int) -> int:
    inside = sorted(t for t in times if lo <= t <= hi)
    best = 0
    for i, t in enumerate(inside):
        best = max(best, sum(1 for u in inside[i:] if u - t <= dt.timedelta(seconds=window_s)))
    return best


def evaluate(topo: Topology, entries: list[dict], world: World, as_of: dt.datetime) -> dict:
    rules = rules_engine.load(RULES)
    ticks = beats(as_of, rules.params)
    window_start = ticks[0] - dt.timedelta(seconds=int(rules.params["beat_s"]))
    per_service: dict = {}
    found: list[dict] = []
    for name in sorted(topo.services):
        svc = topo.services[name]
        healthy = [health_says(world, svc, t) for t in ticks]
        worst = None
        for e in entries:
            if e["target_service"] != name:
                continue
            hit = routes.resolve(entries, e["entrypoint"], e["host"], e["path_prefix"])
            statuses = [world.status(hit["target_service"] if hit else None, e["path_prefix"], t) for t in ticks]
            failing = [not 200 <= s < 400 for s in statuses]
            silent = _longest([f and h for f, h in zip(failing, healthy)])
            any_run = _longest(failing)
            rank = (silent[0], any_run[0])
            if worst is None or rank > worst["rank"]:
                worst = {"rank": rank, "silent": silent, "any": any_run, "statuses": statuses, "entry": e}
        facts = {
            "restarts_in_window": max_restarts(world.restart_times(name), window_start, as_of,
                                               int(rules.params["restart_window_s"])),
            "probe_consecutive_failures": worst["any"][0] if worst else 0,
            "silent_consecutive_failures": worst["silent"][0] if worst else 0,
        }
        per_service[name] = dict(facts, routes_probed=sum(1 for e in entries if e["target_service"] == name))
        then, rid = rules.decide("rules", facts)
        if rid is None:
            continue
        detail: dict = {"facts": facts, "health_kind": svc.health_kind, "health_path": svc.health_path,
                        "beat_s": int(rules.params["beat_s"])}
        timeline = []
        if then["class"] == "restart_loop":
            times = [t for t in world.restart_times(name) if window_start <= t <= as_of]
            detail["restarts"] = [timeutil.fmt(t) for t in times]
            timeline = [[timeutil.fmt(t), "trace.jsonl", f"service {name} restarted"] for t in times]
        elif worst:
            length, start = worst["silent"] if then["class"] == "health_probes_process" else worst["any"]
            run = [(ticks[i], worst["statuses"][i], healthy[i]) for i in range(start, start + length)]
            e = worst["entry"]
            first, declared = run[0][0], run[1][0]
            detail.update({"router": e["router"], "route_file": e["file"], "entrypoint": e["entrypoint"],
                           "host": e["host"], "path_prefix": e["path_prefix"],
                           "statuses": sorted({s for _, s, _ in run}),
                           "first_failing_beat": timeutil.fmt(first), "declared_at": timeutil.fmt(declared),
                           "beats_to_declare": 2, "failing_beats": length,
                           "last_failing_beat": timeutil.fmt(run[-1][0])})
            said = "healthy" if run[0][2] else "unhealthy"
            timeline = [
                [timeutil.fmt(first), "probe", f"{e['entrypoint']} {e['host']}{e['path_prefix']} answered {run[0][1]} (first failing beat)"],
                [timeutil.fmt(first), "healthcheck", f"{svc.health_kind} check of service {name} reported {said}"],
                [timeutil.fmt(declared), "probe", f"second consecutive failing beat ({run[1][1]}): declared by rule {rid}"],
                [timeutil.fmt(run[-1][0]), "probe", f"last failing beat of the run ({length} consecutive)"],
            ]
        detail["timeline"] = timeline
        found.append(F.make(then["class"], rid, then["file"], name, detail=detail))
    return {"findings": F.ordered(found), "services": per_service, "beats": len(ticks),
            "window": [timeutil.fmt(ticks[0]), timeutil.fmt(ticks[-1])]}
