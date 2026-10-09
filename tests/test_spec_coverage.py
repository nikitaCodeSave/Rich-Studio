"""What the hand-maintained parts of the editor still lack for the bundled Bot API spec.

The editor itself is generated from api.json, so a new block or inline type works
without code. Only the Russian labels (labels.js) and the preview (render.js) are
written by hand. When a new Bot API version is bundled, these tests list exactly
what to add; make them green and the release is fully supported. The same check
runs in the studio (spec/coverage.py) and is shown in the spec dialog.
"""

import copy
import json
from pathlib import Path

from spec.coverage import consts, gaps

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8"))


def test_spec_has_rich_types():
    # guards the test below against a parser change that silently finds nothing
    assert len(consts(SPEC["types"], "InputRichBlock")) >= 20
    assert len(consts(SPEC["types"], "RichText")) >= 20


def test_hand_maintained_parts_cover_spec():
    missing = [
        f"add {g['kind']} {g['name']} ({g['type']}) to {g['where']} in app/static/{g['file']}"
        for g in gaps(SPEC["types"])
    ]
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
    assert found == {("labels", "hologram"), ("preview", "hologram")}
