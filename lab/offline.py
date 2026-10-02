"""Offline guard: after ``enforce()`` any attempt to open a socket raises ``OfflineViolation``.

The lab, the generator, the scenarios, the evaluation and the tests call ``enforce()`` on start,
so a code path that reaches for the network fails loudly instead of silently succeeding.
"""
from __future__ import annotations

import socket


class OfflineViolation(RuntimeError):
    """Raised when code in this pack tries to use the network."""


_installed = False


def _deny(*_args, **_kwargs):
    raise OfflineViolation("network access is disabled in this proof pack")


class _DeniedSocket(socket.socket):
    def __init__(self, *_args, **_kwargs):  # never constructs a real socket
        _deny()


def enforce() -> None:
    """Replace the socket constructors with functions that raise. Idempotent."""
    global _installed
    if _installed:
        return
    socket.socket = _DeniedSocket  # type: ignore[misc]
    socket.create_connection = _deny  # type: ignore[assignment]
    socket.getaddrinfo = _deny  # type: ignore[assignment]
    _installed = True


def is_enforced() -> bool:
    return _installed
