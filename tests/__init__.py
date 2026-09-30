"""Test suite of the proof pack (stdlib ``unittest``; offline).

Sockets are blocked before any test module is imported: a test, or the code it exercises, that
reaches for the network fails with ``OfflineViolation`` instead of passing by accident.

    python -m unittest discover -s tests -t .
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lab import offline  # noqa: E402

offline.enforce()
