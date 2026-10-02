"""Reference 2 of 3 - reachability on the matrix of the world, instant by instant.

Independent of the code under test. It builds the full matrix *entrypoint x service* by sending
every request of the universe through a naive resolver, and then replays the trace second by
second for the last thirty minutes to see what a user would get at every beat.

Definitions applied (the same sentences as docs/FORMAT.md, written again here on purpose):

* a beat every 60 seconds, the last one at ``as_of``, thirty beats;
* ``restart_loop``: three or more restarts of one service inside any 600-second span of the
  window ``[as_of - 1800 s, as_of]``;
* ``health_probes_process``: at two or more consecutive beats a route of the service fails
  (status outside 200-399) while the declared health check of the service says healthy.

Covers: ``health_probes_process``, ``restart_loop``, and the admin-public edges of the matrix.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from corpus import reference_routes as RR

UTC = dt.timezone.utc


def _time(text: str) -> dt.datetime:
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _winner(flat: list, entrypoint: str, host: str, path: str):
    best = None
    for rule in flat:
        prefix = rule["prefix"]
        if entrypoint not in rule["entrypoints"] or rule["host"] != host:
            continue
        if not (prefix == "/" or path == prefix or path.startswith(prefix + "/")):
            continue
        if (best is None or rule["priority"] > best["priority"]
                or (rule["priority"] == best["priority"] and len(prefix) > len(best["prefix"]))
                or (rule["priority"] == best["priority"] and len(prefix) == len(best["prefix"])
                    and rule["order"] < best["order"])):
            best = rule
    return best


def matrix(instance_dir: str) -> dict:
    topology = RR.read_yaml(os.path.join(instance_dir, "topology.yaml"))
    flat = RR.rules(instance_dir)
    reach = {ep: {svc: False for svc in topology["services"]} for ep in topology["entrypoints"]}
    for rule in flat:                       # an edge exists as soon as one enabled router draws it
        for ep in rule["entrypoints"]:
            if ep in reach and rule["service"] in reach[ep]:
                reach[ep][rule["service"]] = True
    edges = sorted([ep, svc] for ep, row in reach.items() for svc, hit in row.items()
                   if hit and topology["entrypoints"][ep]["plane"] == "public"
                   and topology["services"][svc]["plane"] == "admin")
    return {"matrix": reach, "admin_public_edges": edges}


class _Replay:
    def __init__(self, instance_dir: str):
        self.proc, self.restarts, self.serve = {}, {}, {}
        with open(os.path.join(instance_dir, "trace.jsonl"), "r", encoding="utf-8") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        for ev in events:
            if ev["kind"] == "proc":
                self.proc.setdefault(ev["service"], []).append((_time(ev["t"]), ev["state"]))
            elif ev["kind"] == "restart":
                self.restarts.setdefault(ev["service"], []).append((_time(ev["t"]), ev["down_s"]))
            elif ev["kind"] == "serve":
                self.serve.setdefault(ev["service"], []).append((_time(ev["t"]), ev["map"]))

    def up(self, service: str, t: dt.datetime) -> bool:
        state = None
        for when, value in sorted(self.proc.get(service, []), key=lambda pair: pair[0]):
            if when <= t:
                state = value
        if state != "up":
            return False
        return not any(start <= t < start + dt.timedelta(seconds=down) for start, down in self.restarts.get(service, []))

    def status(self, service, path: str, t: dt.datetime) -> int:
        if service is None or service not in self.proc:
            return 502
        if not self.up(service, t):
            return 503
        current = {}
        for when, mapping in sorted(self.serve.get(service, []), key=lambda pair: pair[0]):
            if when <= t:
                current = mapping
        return current.get(path, current.get("*", 404))


def check(instance_dir: str) -> list:
    with open(os.path.join(instance_dir, "instance.json"), "r", encoding="utf-8") as fh:
        as_of = _time(json.load(fh)["as_of"])
    topology = RR.read_yaml(os.path.join(instance_dir, "topology.yaml"))
    flat = RR.rules(instance_dir)
    world = _Replay(instance_dir)
    beats = [as_of - dt.timedelta(seconds=60 * k) for k in range(29, -1, -1)]
    found = []
    for name, spec in topology["services"].items():
        lo = as_of - dt.timedelta(seconds=1800)
        times = sorted(t for t, _ in world.restarts.get(name, []) if lo <= t <= as_of)
        if any(sum(1 for u in times[i:] if (u - t).total_seconds() <= 600) >= 3 for i, t in enumerate(times)):
            found.append({"class": "restart_loop", "files": ["trace.jsonl"], "item": name})
            continue
        silent = 0
        for rule in flat:
            if rule["service"] != name:
                continue
            run = 0
            for ep in rule["entrypoints"]:
                for t in beats:
                    hit = _winner(flat, ep, rule["host"], rule["prefix"])
                    status = world.status(hit["service"] if hit else None, rule["prefix"], t)
                    if spec["health"]["kind"] == "process":
                        healthy = world.up(name, t)
                    else:
                        healthy = 200 <= world.status(name, spec["health"]["path"], t) < 400
                    run = run + 1 if (not 200 <= status < 400) and healthy else 0
                    silent = max(silent, run)
                run = 0
        if silent >= 2:
            found.append({"class": "health_probes_process", "files": ["topology.yaml"], "item": name})
    return found
