"""What the hand-maintained parts of the editor lack for a given Bot API spec.

The block palette, forms and validation come from the spec and need no code. Only the Russian
labels (static/js/labels.js), the preview (static/render.js) and the «В HTML» export
(static/js/html-export.js) are written by hand. This module reads those files and lists what they do not
know of the spec. Telegram gets the post either way.

- A block or inline type. Without a label the editor shows the spec's own name; without a renderer the
  preview shows a stub with the block's JSON; without an export entry «В HTML» refuses a post that has it.
- A field of a type they do know (part "fields"). The export's tables (BLOCK_FIELDS, INLINE_FIELDS, PARTS)
  name the fields the hand-written code reads and must name exactly the spec's, but for the fields HTML has
  no way to say (NOT_IN_HTML, next to the tables). A field the spec added is not drawn and refuses the
  export; a field the spec dropped ("gone") is dead code. The preview has no table of its own: it is written
  in pair with the export, so one gap stands for both.

Used by GET /api/spec (the editor shows the list), by tests/test_spec_coverage.py and by
scripts/update_spec.py.
"""

import functools
import re
from pathlib import Path

from .parse import type_const

STATIC = Path(__file__).resolve().parent.parent / "static"
EXPORT = "js/html-export.js"
KINDS = {"InputRichBlock": "block", "RichText": "inline"}

# (what the gap means for the user, file to edit, block or inline union in the spec, where in the file)
TYPE_PARTS = (
    ("labels", "js/labels.js", "InputRichBlock", "BLOCK_LABELS"),
    ("labels", "js/labels.js", "RichText", "RT_LABELS"),
    ("preview", "render.js", "InputRichBlock", "renderBlock"),
    ("preview", "render.js", "RichText", "renderRT"),
    ("export", EXPORT, "InputRichBlock", "BLOCK_FIELDS"),
    ("export", EXPORT, "RichText", "INLINE_FIELDS"),
)
# the export's tables of fields, by the type's constant: (table, block or inline union in the spec)
FIELD_TABLES = (("BLOCK_FIELDS", "InputRichBlock"), ("INLINE_FIELDS", "RichText"))
# the objects inside blocks: their key in the export's PARTS → their type in the spec
OBJECTS = {"item": "InputRichBlockListItem", "cell": "RichBlockTableCell", "caption": "RichBlockCaption"}


def consts(types: dict, base: str) -> dict[str, str]:
    """{const value: type name} for the subtypes of a spec union like InputRichBlock."""
    out = {}
    for name in types.get(base, {}).get("subtypes", []):
        tc = type_const(types, name)
        if tc:
            out[tc[1]] = name
    return out


def _object(source: str, var: str) -> str:
    m = re.search(rf"\b{var}\s*=\s*\{{(.*?)\}};", source, re.S)
    if not m:
        raise ValueError(f"{var} not found")
    return m.group(1)


def object_keys(source: str, var: str) -> set[str]:
    """Keys of `const VAR = { a: ..., b: ... }` in a JS file (keys only, values may be anything)."""
    return set(re.findall(r"(?:^|[,{\s])([a-z_]+)\s*:", _object(source, var)))


def object_lists(source: str, var: str) -> dict[str, set[str]]:
    """{key: the strings of its list} of `const VAR = { a: ['x', 'y'], b: [] }` in a JS file."""
    rows = re.findall(r"(\w+)\s*:\s*\[(.*?)\]", _object(source, var), re.S)
    return {key: set(re.findall(r"'(\w+)'", items)) for key, items in rows}


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


def _rows(types: dict, source: str):
    """(table, kind, key, spec type, fields listed, fields of the spec) for each row of the export's tables whose
    type the spec has. `type` names the union member and is in no table; a list item's own `type` is a field."""
    for table, base in FIELD_TABLES:
        rows = object_lists(source, table)
        for const, type_name in consts(types, base).items():
            if const in rows:
                yield table, KINDS[base], const, type_name, rows[const], _fields(types, type_name) - {"type"}
    parts = object_lists(source, "PARTS")
    for key, type_name in OBJECTS.items():
        if type_name in types:
            yield "PARTS", "object", key, type_name, parts[key], _fields(types, type_name)


def _fields(types: dict, type_name: str) -> set[str]:
    return {f["name"] for f in types[type_name].get("fields", [])}


def gaps(types: dict) -> list[dict]:
    """[{"part": "labels"|"preview"|"export"|"fields", "file", "where", "kind": "block"|"inline"|"object", "name",
    "type"}] sorted; a "fields" gap also has "field" and "gone" (true: the spec no longer has the field)."""
    out: list[dict] = []
    for part, rel, base, where in TYPE_PARTS:
        have = known(where, read_static(rel))
        for const, type_name in consts(types, base).items():
            if const not in have:
                out.append(
                    {"part": part, "file": rel, "where": where, "kind": KINDS[base], "name": const, "type": type_name}
                )
    export = read_static(EXPORT)
    not_in_html = object_lists(export, "NOT_IN_HTML")
    for table, kind, key, type_name, listed, of_spec in _rows(types, export):
        in_spec = of_spec - not_in_html.get(type_name, set())
        for field in in_spec ^ listed:
            gap = {"part": "fields", "file": EXPORT, "where": table, "kind": kind, "name": key, "type": type_name}
            out.append({**gap, "field": field, "gone": field not in in_spec})
    return sorted(out, key=lambda g: (g["part"], g["kind"], g["name"], g.get("field", "")))


def todo(gap: dict) -> str:
    """What to do about a gap, one line: for `make spec-update` and the failing test."""
    where = f"{gap['where']} в app/static/{gap['file']}"
    if gap["part"] != "fields":
        return f"{gap['kind']} {gap['name']} ({gap['type']}): добавьте в {where}"
    what = f"{gap['kind']} {gap['name']}, поле {gap['field']} ({gap['type']})"
    if gap["gone"]:
        return f"{what}: в спецификации его больше нет, уберите из {where} и из render.js"
    return f"{what}: добавьте в {where} и в render.js; если HTML выразить его не может, в NOT_IN_HTML"
