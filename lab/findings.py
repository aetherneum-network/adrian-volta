"""Findings and verdicts.

A finding is a fact with an address: ``class`` (what), ``file`` and ``item`` (where), ``rule``
(which rule decided) and ``detail`` (the evidence). Its verdict comes from ``rules/severity.json``;
a class that no rule knows is never OK. The verdict of a run is the most severe verdict among
its findings, in the order written in the same file.
"""
from __future__ import annotations

from lab import rules_engine


def _severity():
    return rules_engine.load("severity.json")


def verdict_of_class(cls: str) -> tuple[str, str | None]:
    then, rid = _severity().decide("rules", {"class": cls})
    return then.get("verdict", "FAILED"), rid


def make(cls: str, rule: str, file: str, item: str | None = None, also=(), detail: dict | None = None) -> dict:
    verdict, severity_rule = verdict_of_class(cls)
    return {"class": cls, "verdict": verdict, "rule": rule, "severity_rule": severity_rule, "file": file,
            "item": item, "also": sorted(also), "detail": detail or {}}


def ordered(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (f["file"], f["item"] or "", f["class"], f["rule"]))


def overall(findings: list[dict]) -> str:
    precedence = _severity().params["precedence"]
    present = {f["verdict"] for f in findings}
    for verdict in precedence:
        if verdict in present:
            return verdict
    return "OK"


def exit_code(verdict: str) -> int:
    return int(_severity().params["exit_codes"][verdict])
