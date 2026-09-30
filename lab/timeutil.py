"""Timestamps: everything is UTC, written ``YYYY-MM-DDTHH:MM:SSZ``.

``classify`` says what a timestamp *is* before anything is computed from it:
``utc`` (explicit Z), ``offset`` (explicit numeric offset, convertible), ``naive`` (no zone:
the instant is unknown and is never guessed) or ``invalid``.
"""
from __future__ import annotations

import datetime as dt
import re

UTC = dt.timezone.utc
_RE_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RE_OFFSET = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-])(\d{2}):(\d{2})$")
_RE_NAIVE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}$")
_RE_AT = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def classify(value) -> tuple[str, dt.datetime | None]:
    if not isinstance(value, str):
        return "invalid", None
    try:
        if _RE_UTC.match(value):
            return "utc", dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        m = _RE_OFFSET.match(value)
        if m:
            base = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
            delta = dt.timedelta(hours=int(m.group(3)), minutes=int(m.group(4)))
            if m.group(2) == "-":
                delta = -delta
            return "offset", (base - delta).replace(tzinfo=UTC)
        if _RE_NAIVE.match(value):
            dt.datetime.strptime(value.replace(" ", "T"), "%Y-%m-%dT%H:%M:%S")
            return "naive", None
    except ValueError:
        return "invalid", None
    return "invalid", None


def parse_utc(value) -> dt.datetime:
    kind, instant = classify(value)
    if kind != "utc" or instant is None:
        raise ValueError(f"not a UTC timestamp (expected YYYY-MM-DDTHH:MM:SSZ): {value!r}")
    return instant


def fmt(instant: dt.datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_id(instant: dt.datetime) -> str:
    return instant.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_at(value) -> tuple[int, int] | None:
    """``HH:MM`` as written in a schedule; anything else (a YAML sexagesimal integer included) is None."""
    if not isinstance(value, str):
        return None
    m = _RE_AT.match(value)
    return (int(m.group(1)), int(m.group(2))) if m else None
