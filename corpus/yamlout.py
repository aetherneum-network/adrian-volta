"""Deterministic YAML writer with switchable styles.

The development corpus uses the default style (block, two-space indent, plain scalars where
safe). The stress and blind suites rewrite the *same data* in styles the lab was not developed
against. A style changes how a file is written, never what it means.

Writing styles (``emit``): ``flow``, ``double-quoted``, ``single-quoted``, ``comments``,
``doc-markers``, ``key-order``, ``indent-4``, ``crlf``, ``bom``, ``anchors``.
Model styles, applied by the generator before writing: ``yml-ext``, ``host-case``,
``prefix-slash``, ``port-string``, ``entrypoints-list``.
"""
from __future__ import annotations

import json
import re

WRITING_STYLES = ["flow", "double-quoted", "single-quoted", "comments", "doc-markers", "key-order", "indent-4",
                  "crlf", "bom", "anchors"]
MODEL_STYLES = ["yml-ext", "host-case", "prefix-slash", "port-string", "entrypoints-list"]
ALL_STYLES = WRITING_STYLES + MODEL_STYLES

_PLAIN = re.compile(r"^[A-Za-z_/][A-Za-z0-9_./-]*$")
_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}
_NOTES = ["reviewed", "synthetic", "see policy", "generated", "lab only"]


class _Ctx:
    def __init__(self, styles, rng, doc):
        self.styles = set(styles)
        self.rng = rng
        self.indent = 4 if "indent-4" in self.styles else 2
        self.anchors: dict = {}
        self.repeats: dict = {}
        if "anchors" in self.styles:
            self._count(doc)

    def _count(self, node):
        if isinstance(node, dict):
            for v in node.values():
                self._count(v)
        elif isinstance(node, list):
            for v in node:
                self._count(v)
        elif isinstance(node, str):
            self.repeats[node] = self.repeats.get(node, 0) + 1


def _quote(text: str, ctx: _Ctx, force: bool = False) -> str:
    if "single-quoted" in ctx.styles and "\n" not in text:
        return "'" + text.replace("'", "''") + "'"
    if force or "double-quoted" in ctx.styles or not _PLAIN.match(text) or text.lower() in _RESERVED:
        return json.dumps(text, ensure_ascii=False)
    return text


def _key(key, ctx: _Ctx) -> str:
    return _quote(str(key), ctx)


def _scalar(value, ctx: _Ctx) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if ctx.repeats.get(text, 0) > 1:
        if text in ctx.anchors:
            return "*" + ctx.anchors[text]
        ctx.anchors[text] = f"a{len(ctx.anchors) + 1}"
        return f"&{ctx.anchors[text]} {_quote(text, ctx)}"
    return _quote(text, ctx)


def _is_scalar(value) -> bool:
    return not isinstance(value, (dict, list))


def _keys(node: dict, ctx: _Ctx) -> list:
    keys = list(node)
    if "key-order" in ctx.styles:
        ctx.rng.shuffle(keys)
    return keys


def _flow(node, ctx: _Ctx) -> str:
    if isinstance(node, dict):
        return "{" + ", ".join(f"{_key(k, ctx)}: {_flow(node[k], ctx)}" for k in _keys(node, ctx)) + "}"
    if isinstance(node, list):
        return "[" + ", ".join(_flow(v, ctx) for v in node) + "]"
    return _scalar(node, ctx)


def _note(ctx: _Ctx) -> str:
    if "comments" in ctx.styles and ctx.rng.random() < 0.25:
        return "  # " + ctx.rng.choice(_NOTES)
    return ""


def _block(node, indent: int, depth: int, ctx: _Ctx, lines: list) -> None:
    pad = " " * indent
    use_flow = "flow" in ctx.styles and depth >= 1
    if isinstance(node, dict):
        for key in _keys(node, ctx):
            value = node[key]
            if depth == 0 and "comments" in ctx.styles:
                lines.append("")
                lines.append(f"{pad}# {key}")
            if _is_scalar(value):
                lines.append(f"{pad}{_key(key, ctx)}: {_scalar(value, ctx)}{_note(ctx)}")
            elif not value or use_flow:
                lines.append(f"{pad}{_key(key, ctx)}: {_flow(value, ctx)}")
            else:
                lines.append(f"{pad}{_key(key, ctx)}:")
                _block(value, indent + ctx.indent, depth + 1, ctx, lines)
    else:
        for item in node:
            if _is_scalar(item):
                lines.append(f"{pad}- {_scalar(item, ctx)}")
            elif not item or use_flow:
                lines.append(f"{pad}- {_flow(item, ctx)}")
            else:
                sub: list = []
                _block(item, indent + 2, depth + 1, ctx, sub)
                sub[0] = pad + "- " + sub[0][indent + 2:]
                lines.extend(sub)


def emit(doc, styles=(), rng=None) -> bytes:
    """Serialise ``doc`` (dicts, lists, strings, integers, booleans) as YAML bytes."""
    ctx = _Ctx(styles, rng, doc)
    lines: list = []
    if "comments" in ctx.styles:
        lines.append("# Synthetic lab file. Fictitious company, invented topology.")
    if "doc-markers" in ctx.styles:
        lines.append("---")
    _block(doc, 0, 0, ctx, lines)
    if "doc-markers" in ctx.styles:
        lines.append("...")
    text = "\n".join(lines) + "\n"
    if "crlf" in ctx.styles:
        text = text.replace("\n", "\r\n")
    data = text.encode("utf-8")
    if "bom" in ctx.styles:
        data = b"\xef\xbb\xbf" + data
    return data
