"""Heartbeat of the nightly chain: which windows were due, which ran, what is stale.

Scheduled work is anchored to UTC. One chained runner executes the steps in order; when a
window was missed (host off, for example) the next run recovers the *latest* window once and
declares it - earlier missed windows are recorded as missed, they are not silently back-filled.

Nothing here converts a local time: a schedule that is not in UTC, or a timestamp without a
zone, is reported and the window evaluation abstains.
"""
from __future__ import annotations

import datetime as dt

from lab import findings as F
from lab import rules_engine, timeutil
from lab.backup import POLICY_FILE, Repo, RepoError
from lab.sim import FILE as TRACE_FILE

RULES = "alerts.json"


def due_windows(at: tuple[int, int], first_day: dt.date, as_of: dt.datetime, grace_s: int) -> list[dt.datetime]:
    """Windows (UTC instants) from ``first_day`` whose grace period has fully elapsed at ``as_of``."""
    out = []
    day = first_day
    while day <= as_of.date():
        window = dt.datetime(day.year, day.month, day.day, at[0], at[1], tzinfo=timeutil.UTC)
        if window + dt.timedelta(seconds=grace_s) <= as_of:
            out.append(window)
        day += dt.timedelta(days=1)
    return out


def run_starts(jobs: list[dict]) -> dict:
    """Run id -> earliest known start instant of its steps (runs without a known instant are left out)."""
    starts: dict = {}
    for job in jobs:
        if job["_start"] is not None:
            starts[job["run"]] = min(starts.get(job["run"], job["_start"]), job["_start"])
    return starts


def evaluate(policy: dict, jobs: list[dict], as_of: dt.datetime) -> dict:
    rules = rules_engine.load(RULES)
    params = rules.params
    found: list[dict] = []
    abstain = False

    at = timeutil.parse_at(policy["schedule"]["at"])
    tz = policy["schedule"]["tz"]
    schedule_facts = {"at_valid": at is not None, "tz_is_utc": isinstance(tz, str) and tz in params["utc_names"]}
    then, rid = rules.decide("schedule_rules", schedule_facts)
    if rid:
        abstain = abstain or then.get("windows") == "abstain"
        found.append(F.make(then["class"], rid, POLICY_FILE, "schedule",
                            detail={"at": policy["schedule"]["at"] if isinstance(policy["schedule"]["at"], (str, int)) else None,
                                    "tz": tz if isinstance(tz, str) else None, "as_of": timeutil.fmt(as_of)}))

    kinds = {"utc": 0, "offset": 0, "naive": 0, "invalid": 0}
    offending = []
    for job in jobs:
        for field in ("start", "end"):
            kind = job[f"_{field}_kind"]
            kinds[kind] += 1
            if kind != "utc":
                offending.append({"line": job["_line"], "run": job["run"], "step": job["step"], "field": field,
                                  "kind": kind, "value": job.get(field) if isinstance(job.get(field), str) else None})
    then, rid = rules.decide("timestamp_rules", kinds)
    if rid:
        abstain = abstain or then.get("windows") == "abstain"
        found.append(F.make(then["class"], rid, TRACE_FILE, "job-timestamps",
                            detail={"counts": kinds, "entries": offending, "as_of": timeutil.fmt(as_of)}))

    windows: dict = {"evaluable": not abstain, "due": [], "covered": [], "missed": []}
    catch_up = None
    if not abstain:
        starts = run_starts(jobs)
        grace = int(params["grace_s"])
        lookback_first = as_of.date() - dt.timedelta(days=int(params["lookback_days"]) - 1)
        first_day = lookback_first
        if starts:
            earliest = min(starts.values())
            first_window_day = earliest.date()
            if (earliest.hour, earliest.minute) < at:
                first_window_day -= dt.timedelta(days=1)
            first_day = max(lookback_first, first_window_day)
        due = due_windows(at, first_day, as_of, grace)
        covered, missed = [], []
        for window in due:
            hit = any(window <= s <= window + dt.timedelta(seconds=grace) for s in starts.values())
            (covered if hit else missed).append(window)
        windows.update({"due": [timeutil.fmt(w) for w in due], "covered": [timeutil.fmt(w) for w in covered],
                        "missed": [timeutil.fmt(w) for w in missed]})
        then, rid = rules.decide("window_rules", {"evaluable": True, "missed": len(missed)})
        if rid:
            catch_up = {"declared": True, "as_of": timeutil.fmt(as_of), "recover_window": timeutil.fmt(missed[-1]),
                        "recorded_as_missed": [timeutil.fmt(w) for w in missed[:-1]], "runs": 1,
                        "note": "one chained run at as_of recovers the latest missed window; earlier windows stay recorded as missed"}
            found.append(F.make(then["class"], rid, TRACE_FILE, missed[0].strftime("%Y-%m-%d"),
                                detail={"missed": windows["missed"], "catch_up": catch_up, "as_of": timeutil.fmt(as_of),
                                        "timeline": [[timeutil.fmt(w), "schedule", "window due, no run within the grace period"]
                                                     for w in missed]}))
    return {"findings": found, "schedule": schedule_facts, "timestamps": kinds, "windows": windows,
            "catch_up": catch_up}


def secondary(policy: dict, repo_a: Repo, repo_b: Repo, jobs: list[dict], as_of: dt.datetime) -> dict:
    """Is the secondary repository as fresh as the primary? Reads the repositories, not the job log."""
    rules = rules_engine.load(RULES)
    max_lag = policy["repos"]["b"].get("max_lag_hours") or int(rules.params["default_max_lag_hours"])
    out: dict = {"max_lag_hours": max_lag, "as_of": timeutil.fmt(as_of), "finding": None}
    ids_a, ids_b = repo_a.snapshot_ids(), repo_b.snapshot_ids()
    newest_a = newest_b = None
    facts: dict = {"secondary_readable": True, "secondary_has_snapshots": bool(ids_b)}
    try:
        newest_a = repo_a.created(ids_a[-1]) if ids_a else None
    except RepoError as exc:
        out["primary_unreadable"] = str(exc)      # the restore drill reports the primary; no lag is computed here
    try:
        newest_b = repo_b.created(ids_b[-1]) if ids_b else None
    except RepoError as exc:
        out["unreadable"] = str(exc)              # age unknown: reported by rule, never assumed fresh
        facts["secondary_readable"] = False
    out.update({"a_newest": ids_a[-1] if ids_a else None, "b_newest": ids_b[-1] if ids_b else None})
    if newest_a is not None and newest_b is not None:
        lag_h = (newest_a - newest_b).total_seconds() / 3600
        facts["lag_exceeds_max"] = lag_h > max_lag
        out["lag_hours"] = round(lag_h, 2)
        out["b_age_hours_at_as_of"] = round((as_of - newest_b).total_seconds() / 3600, 2)
    claims = sorted({j["status"] for j in jobs if j["step"] == "backup_b"
                     and j["run"] == max((x["run"] for x in jobs), default=None)})
    out["job_log_claims"] = claims
    out["facts"] = facts
    then, rid = rules.decide("repo_rules", facts)
    if rid:
        detail = {k: v for k, v in out.items() if k != "finding"}
        state = ("newest snapshot of the secondary repository" if newest_b
                 else "newest snapshot of the secondary repository cannot be read" if "unreadable" in out
                 else "no snapshot in the secondary repository")
        detail["timeline"] = [[timeutil.fmt(newest_b) if newest_b else out["as_of"], "repo_b", state],
                              [out["as_of"], "audit", f"job log claims {claims or ['nothing']} for backup_b; repository read instead"]]
        out["finding"] = F.make(then["class"], rid, policy["repos"]["b"]["path"], out["b_newest"], detail=detail)
    return out
