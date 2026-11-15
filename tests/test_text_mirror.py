"""Inline types whose required String field repeats the visible text (email, phone, card, TON address, mention...).

Found from the spec by one rule, not from a list; only the link targets and names are excepted (NOT_TEXT_MIRROR,
mirrored in spec.js). A new RichText type with `text` and one more required String is judged by the rule.
"""

import json
import re
from pathlib import Path

from spec.coverage import consts
from spec.validator import NOT_TEXT_MIRROR, text_mirror_field

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8"))


def mirrors() -> dict[str, str]:
    types = SPEC["types"]
    out = {}
    for const, name in consts(types, "RichText").items():
        field = text_mirror_field(types[name], ("type", const))
        if field:
            out[const] = field
    return out


def test_the_rule_finds_the_entities_from_the_spec():
    m = mirrors()
    assert m["ton_address"] == "address"
    assert m["email_address"] == "email_address"
    assert m["phone_number"] == "phone_number"
    assert m["bank_card_number"] == "bank_card_number"
    assert m["mention"] == "username"
    assert not set(m) & NOT_TEXT_MIRROR
    assert "bold" not in m and "custom_emoji" not in m


def test_the_exception_list_is_the_same_in_the_editor():
    js = (ROOT / "app" / "static" / "js" / "spec.js").read_text("utf-8")
    listed = re.search(r"const NOT_TEXT_MIRROR = \[(.*?)\]", js).group(1)
    assert set(re.findall(r"'(\w+)'", listed)) == set(NOT_TEXT_MIRROR)


def test_an_empty_value_next_to_a_text_is_a_warning_and_the_payload_stays(client):
    rm = {"blocks": [{"type": "paragraph", "text": {"type": "ton_address", "text": "UQ1", "address": ""}}]}
    r = client.post("/api/validate", json={"rich_message": rm}).json()
    assert r["ok"] is True, r
    assert any("address: пустое значение" in w for w in r["warnings"]), r
    ok = {"blocks": [{"type": "paragraph", "text": {"type": "ton_address", "text": "UQ1", "address": "UQ1"}}]}
    assert not client.post("/api/validate", json={"rich_message": ok}).json()["warnings"]
    link = {"blocks": [{"type": "paragraph", "text": {"type": "url", "text": "x", "url": "https://t.me/x"}}]}
    assert not client.post("/api/validate", json={"rich_message": link}).json()["warnings"]
