"""Virtual-clock world built from ``trace.jsonl``: processes, restarts, what each backend answers.

The trace is a list of facts with a UTC timestamp. Nothing here reads the wall clock.

Event kinds (one JSON object per line):

* ``{"kind": "proc", "t": ..., "service": s, "state": "up" | "down"}``
* ``{"kind": "restart", "t": ..., "service": s, "down_s": n}`` - the process is down for ``n`` seconds
* ``{"kind": "serve", "t": ..., "service": s, "map": {"/path": status, ...}}`` - exact paths the backend
  answers from ``t`` on (``"*"`` is a default); any other path is 404
* ``{"kind": "job", "run": "YYYY-MM-DD", "step": name, "start": ..., "end": ..., "status": "ok" | "failed"}``
* ``{"kind": "host", "t": ..., "state": "off" | "on"}``

Simulation events must carry explicit-UTC timestamps; a line that cannot be read is a problem
and health is not asserted from a trace that is partly unknown. Job timestamps are classified,
not rejected: their nature (utc / offset / naive) is a fact the alert rules decide on.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json

from lab import fsx, timeutil
from lab.routes import norm_prefix

FILE = "trace.jsonl"
SIM_KINDS = ("proc", "restart", "serve", "host")


def load_trace(instance_dir) -> tuple[dict, list[str]]:
    """Return ``({"sim": [...], "jobs": [...]}, problems)``."""
    problems: list[str] = []
    sim: list[dict] = []
    jobs: list[dict] = []
    try:
        text = fsx.read_bytes(fsx.join(instance_dir, FILE)).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"sim": [], "jobs": []}, [f"{type(exc).__name__}: trace unreadable"]
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            problems.append(f"line {number}: not JSON")
            continue
        if not isinstance(ev, dict):
            problems.append(f"line {number}: not an object")
            continue
        kind = ev.get("kind")
        if kind in SIM_KINDS:
            try:
                ev["_t"] = timeutil.parse_utc(ev.get("t"))
            except ValueError:
                problems.append(f"line {number}: simulation event without an explicit-UTC timestamp")
                continue
            if kind != "host" and not isinstance(ev.get("service"), str):
                problems.append(f"line {number}: service missing")
                continue
            if kind == "restart" and (isinstance(ev.get("down_s"), bool) or not isinstance(ev.get("down_s"), int)
                                      or ev["down_s"] < 0):
                problems.append(f"line {number}: restart without down_s")
                continue
            if kind == "proc" and ev.get("state") not in ("up", "down"):
                problems.append(f"line {number}: proc state must be up or down")
                continue
            if kind == "serve" and not (isinstance(ev.get("map"), dict)
                                        and all(isinstance(v, int) and not isinstance(v, bool) for v in ev["map"].values())):
                problems.append(f"line {number}: serve map must map paths to integer statuses")
                continue
            ev["_line"] = number
            sim.append(ev)
        elif kind == "job":
            if not all(isinstance(ev.get(k), str) for k in ("run", "step", "status")):
                problems.append(f"line {number}: job event needs run, step and status")
                continue
            ev["_line"] = number
            for field in ("start", "end"):
                ev[f"_{field}_kind"], ev[f"_{field}"] = timeutil.classify(ev.get(field))
            jobs.append(ev)
        else:
            problems.append(f"line {number}: unknown kind {kind!r}")
    sim.sort(key=lambda e: (e["_t"], e["_line"]))
    return {"sim": sim, "jobs": jobs}, problems


class World:
    """What each service does over time, as far as the trace says."""

    def __init__(self, sim_events: list[dict]):
        self._proc: dict = {}
        self._restarts: dict = {}
        self._serve: dict = {}
        for ev in sim_events:
            svc = ev.get("service")
            if ev["kind"] == "proc":
                self._proc.setdefault(svc, []).append((ev["_t"], ev["state"] == "up"))
            elif ev["kind"] == "restart":
                self._restarts.setdefault(svc, []).append((ev["_t"], ev["down_s"]))
            elif ev["kind"] == "serve":
                self._serve.setdefault(svc, []).append((ev["_t"], {norm_prefix(k) if k != "*" else k: v
                                                                    for k, v in ev["map"].items()}))

    @staticmethod
    def _last(series: list, t: dt.datetime):
        idx = bisect.bisect_right([s[0] for s in series], t) - 1
        return series[idx][1] if idx >= 0 else None

    def proc_up(self, service: str, t: dt.datetime) -> bool:
        """Up only if the trace says so: no statement means not up."""
        if self._last(self._proc.get(service, []), t) is not True:
            return False
        for start, down_s in self._restarts.get(service, []):
            if start <= t < start + dt.timedelta(seconds=down_s):
                return False
        return True

    def status(self, service: str | None, path: str, t: dt.datetime) -> int:
        """HTTP status the backend gives at ``t``: 502 unknown backend, 503 process down, else the serve map."""
        if service is None or service not in self._proc:
            return 502
        if not self.proc_up(service, t):
            return 503
        mapping = self._last(self._serve.get(service, []), t) or {}
        path = norm_prefix(path)
        if path in mapping:
            return mapping[path]
        return mapping.get("*", 404)

    def restart_times(self, service: str) -> list[dt.datetime]:
        return [t for t, _ in self._restarts.get(service, [])]
