"""What the hand-maintained parts of the editor still lack for the bundled Bot API spec.

The editor itself is generated from api.json, so a new block or inline type works
without code. Only the Russian labels (labels.js), the preview (render.js) and the
«В HTML» export tables (html-export.js) are written by hand. When a new Bot API version is bundled, these tests list exactly
what to add or remove, down to a field of a block they already know; make them green and the release is fully
supported. The same check runs in the studio (spec/coverage.py) and is shown in the spec dialog.
"""

import copy
import json
from pathlib import Path

from spec.coverage import consts, gaps, todo

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8"))


def test_spec_has_rich_types():
    # guards the test below against a parser change that silently finds nothing
    assert len(consts(SPEC["types"], "InputRichBlock")) >= 20
    assert len(consts(SPEC["types"], "RichText")) >= 20


def test_hand_maintained_parts_cover_spec():
    missing = [todo(g) for g in gaps(SPEC["types"])]
    assert not missing, f"{SPEC['version']}:\n" + "\n".join(missing)


def test_a_new_block_in_the_spec_is_reported():
    types = copy.deepcopy(SPEC["types"])
    types["InputRichBlockHologram"] = {
        "name": "InputRichBlockHologram",
        "fields": [
            {
                "name": "type",
                "types": ["String"],
                "required": True,
                "description": "Type of the block, always “hologram”",
            }
        ],
    }
    types["InputRichBlock"]["subtypes"].append("InputRichBlockHologram")
    found = {(g["part"], g["name"]) for g in gaps(types)}
    assert found == {("labels", "hologram"), ("preview", "hologram"), ("export", "hologram")}


def test_a_new_inline_type_is_reported_for_the_export_too():
    types = copy.deepcopy(SPEC["types"])
    types["RichTextGlow"] = {
        "name": "RichTextGlow",
        "fields": [
            {
                "name": "type",
                "types": ["String"],
                "required": True,
                "description": "Type of the rich text, always “glow”",
            },
            {"name": "text", "types": ["RichText"], "required": True, "description": "Text"},
        ],
    }
    types["RichText"]["subtypes"].append("RichTextGlow")
    found = {(g["part"], g["where"]) for g in gaps(types) if g["name"] == "glow"}
    assert found == {("labels", "RT_LABELS"), ("preview", "renderRT"), ("export", "INLINE_FIELDS")}


def _field(name: str, kind: str = "RichText") -> dict:
    return {"name": name, "types": [kind], "required": False, "description": "Optional. New in this version"}


def _field_gaps(types: dict) -> set:
    return {(g["kind"], g["name"], g["field"], g["gone"]) for g in gaps(types) if g["part"] == "fields"}


def test_a_field_the_spec_replaced_in_a_known_block_is_reported_both_ways():
    """Bot API 10.5 did this to the expandable quote (text became blocks) and the type-only check saw nothing."""
    types = copy.deepcopy(SPEC["types"])
    quote = types["InputRichBlockPullQuotation"]
    quote["fields"] = [f for f in quote["fields"] if f["name"] != "text"] + [
        _field("blocks", "Array of InputRichBlock")
    ]
    assert _field_gaps(types) == {("block", "pullquote", "blocks", False), ("block", "pullquote", "text", True)}
    said = [todo(g) for g in gaps(types)]
    assert any("blocks" in s and "BLOCK_FIELDS" in s for s in said) and any("text" in s and "нет" in s for s in said)


def test_a_new_field_of_an_inline_type_and_of_an_object_inside_a_block_is_reported():
    types = copy.deepcopy(SPEC["types"])
    types["RichTextUrl"]["fields"].append(_field("is_trusted", "Boolean"))
    types["InputRichBlockListItem"]["fields"].append(_field("icon", "String"))
    types["RichBlockTableCell"]["fields"].append(_field("background", "String"))
    types["RichBlockCaption"]["fields"].append(_field("align", "String"))
    assert _field_gaps(types) == {
        ("inline", "url", "is_trusted", False),
        ("object", "item", "icon", False),
        ("object", "cell", "background", False),
        ("object", "caption", "align", False),
    }


def test_a_field_html_cannot_say_is_not_a_gap():
    """The map's width and height are in the spec and not in the export's table on purpose (NOT_IN_HTML)."""
    fields = {f["name"] for f in SPEC["types"]["InputRichBlockMap"]["fields"]}
    assert {"width", "height"} <= fields
    assert not [g for g in gaps(SPEC["types"]) if g["name"] == "map"]


def test_a_block_missing_whole_is_not_listed_field_by_field():
    types = copy.deepcopy(SPEC["types"])
    types["InputRichBlockHologram"] = {
        "name": "InputRichBlockHologram",
        "fields": [
            {
                "name": "type",
                "types": ["String"],
                "required": True,
                "description": "Type of the block, always “hologram”",
            },
            _field("text"),
        ],
    }
    types["InputRichBlock"]["subtypes"].append("InputRichBlockHologram")
    assert not _field_gaps(types)


def test_skill_is_where_claude_code_finds_it():
    """Claude Code loads project skills only from .claude/skills/<name>/SKILL.md, with a matching name."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    skill = root / ".claude" / "skills" / "telegram-rich-posts" / "SKILL.md"
    head = skill.read_text("utf-8").split("---")[1]
    assert re.search(r"^name: telegram-rich-posts$", head, re.M)
    assert re.search(r"^description: .{20,}", head, re.M)
