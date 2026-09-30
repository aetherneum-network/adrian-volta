"""Topology of one synthetic instance: planes, entrypoints, networks, services.

``load`` validates strictly. Anything outside the documented schema (docs/FORMAT.md) is a
problem and the caller abstains: an unknown key could change the meaning of the file.
Keys that start with ``x-`` are annotations and are ignored.
"""
from __future__ import annotations

from dataclasses import dataclass

from lab import fsx, yamlio

FILE = "topology.yaml"
ENTRY_PLANES = ("public", "admin")
NETWORK_PLANES = ("public", "admin", "internal")
RESTART_POLICIES = ("always", "on-failure", "unless-stopped")
HEALTH_KINDS = ("process", "http")


@dataclass(frozen=True)
class Service:
    name: str
    plane: str
    port: int
    networks: tuple
    restart_policy: str
    health_kind: str
    health_path: str | None


@dataclass(frozen=True)
class Topology:
    company: str
    domain: str
    entrypoints: dict   # name -> plane
    networks: dict      # name -> plane
    services: dict      # name -> Service


def as_int(value):
    """An integer, or a string made only of digits. Booleans and everything else are rejected."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _keys(mapping: dict, allowed: tuple, where: str, problems: list) -> None:
    for key in mapping:
        if not isinstance(key, str):
            problems.append(f"{where}: non-string key {key!r}")
        elif key not in allowed and not key.startswith("x-"):
            problems.append(f"{where}: unknown key {key!r}")


def parse(doc) -> tuple[Topology | None, list[str]]:
    problems: list[str] = []
    if not isinstance(doc, dict):
        return None, ["topology: top level is not a mapping"]
    _keys(doc, ("company", "domain", "entrypoints", "networks", "services"), "topology", problems)
    company, domain = doc.get("company"), doc.get("domain")
    if not isinstance(company, str) or not isinstance(domain, str):
        problems.append("topology: company and domain must be strings")
    networks: dict = {}
    raw_nets = doc.get("networks")
    if not isinstance(raw_nets, dict) or not raw_nets:
        problems.append("topology: networks must be a non-empty mapping")
        raw_nets = {}
    for name, spec in raw_nets.items():
        if not isinstance(name, str) or not isinstance(spec, dict) or spec.get("plane") not in NETWORK_PLANES:
            problems.append(f"network {name!r}: plane must be one of {NETWORK_PLANES}")
            continue
        _keys(spec, ("plane",), f"network {name}", problems)
        networks[name] = spec["plane"]
    entrypoints: dict = {}
    raw_eps = doc.get("entrypoints")
    if not isinstance(raw_eps, dict) or not raw_eps:
        problems.append("topology: entrypoints must be a non-empty mapping")
        raw_eps = {}
    for name, spec in raw_eps.items():
        if not isinstance(name, str) or not isinstance(spec, dict) or spec.get("plane") not in ENTRY_PLANES:
            problems.append(f"entrypoint {name!r}: plane must be one of {ENTRY_PLANES}")
            continue
        _keys(spec, ("plane", "network"), f"entrypoint {name}", problems)
        if spec.get("network") not in networks:
            problems.append(f"entrypoint {name}: unknown network {spec.get('network')!r}")
        entrypoints[name] = spec["plane"]
    services: dict = {}
    raw_svcs = doc.get("services")
    if not isinstance(raw_svcs, dict) or not raw_svcs:
        problems.append("topology: services must be a non-empty mapping")
        raw_svcs = {}
    for name, spec in raw_svcs.items():
        where = f"service {name}"
        if not isinstance(name, str) or not isinstance(spec, dict):
            problems.append(f"{where}: not a mapping")
            continue
        _keys(spec, ("plane", "port", "networks", "restart", "health"), where, problems)
        plane, port = spec.get("plane"), as_int(spec.get("port"))
        if plane not in ENTRY_PLANES:
            problems.append(f"{where}: plane must be one of {ENTRY_PLANES}")
        if port is None or not 1 <= port <= 65535:
            problems.append(f"{where}: port must be an integer between 1 and 65535")
        nets = spec.get("networks")
        if not isinstance(nets, list) or not nets or any(n not in networks for n in nets):
            problems.append(f"{where}: networks must be a non-empty list of declared networks")
            nets = []
        restart = spec.get("restart")
        if not isinstance(restart, dict) or restart.get("policy") not in RESTART_POLICIES:
            problems.append(f"{where}: restart.policy must be one of {RESTART_POLICIES}")
            restart = {}
        else:
            _keys(restart, ("policy", "max_restarts"), f"{where}.restart", problems)
        health = spec.get("health")
        if not isinstance(health, dict) or health.get("kind") not in HEALTH_KINDS:
            problems.append(f"{where}: health.kind must be one of {HEALTH_KINDS}")
            health = {}
        else:
            _keys(health, ("kind", "path", "interval_s"), f"{where}.health", problems)
            if health["kind"] == "http" and not (isinstance(health.get("path"), str) and health["path"].startswith("/")):
                problems.append(f"{where}: health.path is required for kind http")
        services[name] = Service(name, plane, port or 0, tuple(nets), restart.get("policy", ""),
                                 health.get("kind", ""), health.get("path"))
    if problems:
        return None, problems
    return Topology(company, domain, entrypoints, networks, services), []


def load(instance_dir) -> tuple[Topology | None, list[str]]:
    doc, err = yamlio.load_file(fsx.join(instance_dir, FILE))
    if err:
        return None, [err]
    return parse(doc)
