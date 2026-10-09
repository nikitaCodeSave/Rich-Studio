"""What the hand-maintained parts of the editor lack for a given Bot API spec.

The block palette, forms and validation come from the spec and need no code. Only the Russian
labels (static/js/labels.js) and the preview (static/render.js) are written by hand. This module
reads those two files and lists the block and inline types of the spec they do not know yet.
Without a label the editor shows the spec's own name; without a renderer the preview shows a stub
with the block's JSON. Telegram gets the post either way.

Used by GET /api/spec (the editor shows the list), by tests/test_spec_coverage.py and by
scripts/update_spec.py.
"""

import functools
import re
from pathlib import Path

from .parse import type_const

STATIC = Path(__file__).resolve().parent.parent / "static"

# (what the gap means for the user, file to edit, block or inline union in the spec)
PARTS = (
    ("labels", "js/labels.js", "InputRichBlock", "BLOCK_LABELS"),
    ("labels", "js/labels.js", "RichText", "RT_LABELS"),
    ("preview", "render.js", "InputRichBlock", "renderBlock"),
    ("preview", "render.js", "RichText", "renderRT"),
)


def consts(types: dict, base: str) -> dict[str, str]:
    """{const value: type name} for the subtypes of a spec union like InputRichBlock."""
    out = {}
    for name in types.get(base, {}).get("subtypes", []):
        tc = type_const(types, name)
        if tc:
            out[tc[1]] = name
    return out


def object_keys(source: str, var: str) -> set[str]:
    """Keys of `const VAR = { a: ..., b: ... }` in a JS file (keys only, values may be anything)."""
    m = re.search(rf"\b{var}\s*=\s*\{{(.*?)\}};", source, re.S)
    if not m:
        raise ValueError(f"{var} not found")
    return set(re.findall(r"(?:^|[,{\s])([a-z_]+)\s*:", m.group(1)))


def function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index("\n  function ", start + 1)
    return source[start:end]


def known(part: str, source: str) -> set[str]:
    """Names a labels table or a render function handles."""
    if part == "renderBlock":
        return set(re.findall(r"case '([a-z_]+)'", function_body(source, "renderBlock")))
    if part == "renderRT":
        return set(re.findall(r"'([a-z_]+)'", function_body(source, "renderRT"))) | object_keys(source, "WRAP")
    return object_keys(source, part)


@functools.cache
def _read(rel: str, mtime: float) -> str:
    return (STATIC / rel).read_text("utf-8")


def read_static(rel: str) -> str:
    path = STATIC / rel
    return _read(rel, path.stat().st_mtime)


def gaps(types: dict) -> list[dict]:
    """[{"part": "labels"|"preview", "file", "kind": "block"|"inline", "name", "type"}] sorted."""
    out = []
    for part, rel, base, where in PARTS:
        have = known(where, read_static(rel))
        for const, type_name in consts(types, base).items():
            if const not in have:
                kind = "block" if base == "InputRichBlock" else "inline"
                out.append({"part": part, "file": rel, "where": where, "kind": kind, "name": const, "type": type_name})
    return sorted(out, key=lambda g: (g["part"], g["kind"], g["name"]))
