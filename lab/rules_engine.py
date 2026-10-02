"""Ordered rule files: the first rule that matches wins, exceptions are written on top.

A rule file is JSON with a ``version`` and one or more ordered sections. Every rule has an
``id``, a ``when`` (conditions on facts), a ``then`` (the decision), a ``rationale`` and its own
``tests``. The code computes facts and applies the decision; it never patches an outcome by
hand. To change a decision, change the rule (and its tests), not the output.

Conditions: ``{"fact": value}`` means equality; ``{"fact": {"ge": 3}}`` supports ``eq``, ``ne``,
``ge``, ``gt``, ``le``, ``lt``, ``in`` and ``not_in``. A fact that is missing never matches.
"""
from __future__ import annotations

import json
import os
from typing import Any

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rules")
_MISSING = object()
_OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "ge": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "le": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


def _same(a: Any, b: Any) -> bool:
    """Equality that does not let ``True`` pass for ``1`` (nor ``False`` for ``0``)."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def matches(when: dict, facts: dict) -> bool:
    for name, cond in when.items():
        value = facts.get(name, _MISSING)
        if value is _MISSING:
            return False
        if isinstance(cond, dict):
            for op, ref in cond.items():
                if op not in _OPS:
                    raise ValueError(f"unknown operator {op!r} in rule condition")
                try:
                    if not _OPS[op](value, ref):
                        return False
                except TypeError:
                    return False
        elif not _same(value, cond):
            return False
    return True


class RuleFile:
    def __init__(self, name: str, data: dict):
        self.name = name
        self.data = data
        self.version = data["version"]
        self.params = data.get("params", {})

    @classmethod
    def load(cls, name: str, rules_dir: str | None = None) -> "RuleFile":
        with open(os.path.join(rules_dir or RULES_DIR, name), "r", encoding="utf-8") as fh:
            return cls(name, json.load(fh))

    def sections(self) -> list[str]:
        return [k for k, v in self.data.items() if isinstance(v, list) and v and isinstance(v[0], dict) and "when" in v[0]]

    def rules(self, section: str) -> list[dict]:
        return self.data[section]

    def decide(self, section: str, facts: dict) -> tuple[dict, str | None]:
        """Return ``(then, rule_id)`` of the first matching rule, or ``({}, None)``."""
        for rule in self.data[section]:
            if matches(rule["when"], facts):
                return rule["then"], rule["id"]
        return {}, None

    def run_inline_tests(self) -> list[str]:
        """Run every rule's own tests through ``decide``. Returns human-readable failures."""
        failures = []
        for section in self.sections():
            for rule in self.data[section]:
                for test in rule.get("tests", []):
                    then, rid = self.decide(section, test["input"])
                    if rid != rule["id"]:
                        failures.append(f"{self.name}:{test['id']}: decided by {rid}, expected {rule['id']}")
                        continue
                    for key, want in test["expect"].items():
                        if then.get(key, _MISSING) != want:
                            failures.append(f"{self.name}:{test['id']}: {key}={then.get(key)!r}, expected {want!r}")
        return failures


_cache: dict[str, RuleFile] = {}


def load(name: str) -> RuleFile:
    if name not in _cache:
        _cache[name] = RuleFile.load(name)
    return _cache[name]


def versions() -> dict[str, str]:
    names = sorted(n for n in os.listdir(RULES_DIR) if n.endswith(".json"))
    return {n[:-5]: load(n).version for n in names}
