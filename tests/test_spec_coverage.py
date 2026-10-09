"""What the hand-maintained parts of the editor still lack for the bundled Bot API spec.

The editor itself is generated from api.json, so a new block or inline type works
without code. Only the Russian labels (labels.js) and the preview (render.js) are
written by hand. When a new Bot API version is bundled, these tests list exactly
what to add; make them green and the release is fully supported.
"""

import json
import re
from pathlib import Path

import pytest

from spec.parse import type_const

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
SPEC = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8"))
LABELS = (STATIC / "js" / "labels.js").read_text("utf-8")
RENDER = (STATIC / "render.js").read_text("utf-8")


def consts(base: str) -> dict[str, str]:
    """{const value: type name} for the subtypes of a spec union like InputRichBlock."""
    types = SPEC["types"]
    out = {}
    for name in types[base].get("subtypes", []):
        tc = type_const(types, name)
        if tc:
            out[tc[1]] = name
    return out


def object_keys(source: str, var: str) -> set[str]:
    """Keys of `const VAR = { a: ..., b: ... }` in a JS file (keys only, values may be anything)."""
    m = re.search(rf"\b{var}\s*=\s*\{{(.*?)\}};", source, re.S)
    assert m, f"{var} not found"
    return set(re.findall(r"(?:^|[,{\s])([a-z_]+)\s*:", m.group(1)))


def function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    end = source.index("\n  function ", start + 1)
    return source[start:end]


def missing(names: dict[str, str], present: set[str]) -> list[str]:
    return sorted(f"{const} ({type_name})" for const, type_name in names.items() if const not in present)


BLOCKS = consts("InputRichBlock")
INLINE = consts("RichText")


def test_spec_has_rich_types():
    # guards the tests below against a parser change that silently finds nothing
    assert len(BLOCKS) >= 20
    assert len(INLINE) >= 20


@pytest.mark.parametrize(
    ("what", "names", "present"),
    [
        ("BLOCK_LABELS in app/static/js/labels.js", BLOCKS, object_keys(LABELS, "BLOCK_LABELS")),
        ("RT_LABELS in app/static/js/labels.js", INLINE, object_keys(LABELS, "RT_LABELS")),
        (
            "renderBlock() in app/static/render.js",
            BLOCKS,
            set(re.findall(r"case '([a-z_]+)'", function_body(RENDER, "renderBlock"))),
        ),
        (
            "renderRT() or WRAP in app/static/render.js",
            INLINE,
            set(re.findall(r"'([a-z_]+)'", function_body(RENDER, "renderRT"))) | object_keys(RENDER, "WRAP"),
        ),
    ],
)
def test_hand_maintained_parts_cover_spec(what, names, present):
    gaps = missing(names, present)
    assert not gaps, f"{SPEC['version']}: add to {what}: {', '.join(gaps)}"
