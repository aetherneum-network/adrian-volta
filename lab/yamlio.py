"""YAML reading with two guards the default loader does not have.

* A mapping that repeats a key is rejected (the default loader silently keeps the last value,
  which is exactly how one rule can hide another inside a single file).
* A file must contain exactly one document.

A file that cannot be read is reported with a reason; the caller abstains, it never guesses.
"""
from __future__ import annotations

import yaml

from lab import fsx

_MERGE_TAG = "tag:yaml.org,2002:merge"


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys written in the same mapping."""


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode):
    seen = set()
    for key_node, _ in node.value:
        if key_node.tag == _MERGE_TAG:
            continue
        key = loader.construct_object(key_node, deep=True)
        try:
            hash(key)
        except TypeError:
            raise yaml.constructor.ConstructorError(None, None, "unhashable mapping key", key_node.start_mark)
        if key in seen:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    loader.flatten_mapping(node)
    out = {}
    for key_node, value_node in node.value:
        out[loader.construct_object(key_node, deep=True)] = loader.construct_object(value_node, deep=True)
    return out


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def loads(text: str):
    """Parse one YAML document. Returns ``(document, None)`` or ``(None, reason)``."""
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader), None
    except yaml.YAMLError as exc:
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        return None, f"{type(exc).__name__}: {problem}"


def load_file(path):
    """Read one YAML file (UTF-8, BOM tolerated). Returns ``(document, None)`` or ``(None, reason)``."""
    try:
        text = fsx.read_bytes(path).decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"{type(exc).__name__}: unreadable"
    return loads(text)
