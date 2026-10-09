"""How Telegram parsed a post: blocks from the preview send, and their conversion back to input blocks."""

import json
from pathlib import Path

import pytest

import spec

CAPTURED = json.loads((Path(__file__).parent / "telegram_parse" / "captured.json").read_text("utf-8"))


@pytest.fixture(scope="module")
def current():
    spec.current.load_initial()
    return spec.current


@pytest.mark.parametrize("name", [k for k, v in CAPTURED.items() if v.get("blocks")])
def test_parsed_blocks_become_valid_input_blocks(current, name):
    blocks = spec.to_input_blocks(current, CAPTURED[name]["blocks"])
    r = spec.validate_rich_message(current, {"blocks": blocks})
    assert not r["errors"] and not r["warnings"], (r["errors"], r["warnings"])


def test_sent_media_reuses_the_file_id(current):
    sent = [
        {
            "type": "photo",
            "photo": [
                {"file_id": "small", "width": 90, "height": 60},
                {"file_id": "big", "width": 1280, "height": 720},
            ],
            "has_spoiler": True,
            "caption": {"text": "Обложка"},
        },
        {
            "type": "list",
            "items": [{"label": "•", "blocks": [{"type": "video", "video": {"file_id": "v1", "duration": 3}}]}],
        },
    ]
    photo, lst = spec.to_input_blocks(current, sent)
    assert photo == {
        "type": "photo",
        "photo": {"type": "photo", "media": "big", "has_spoiler": True},
        "caption": {"text": "Обложка"},
    }
    assert lst["items"] == [{"blocks": [{"type": "video", "video": {"type": "video", "media": "v1"}}]}]


def test_unknown_block_is_kept(current):
    assert spec.to_input_blocks(current, [{"type": "hologram", "x": 1}]) == [{"type": "hologram", "x": 1}]


def test_preview_send_returns_how_telegram_parsed_the_post(client, tg):
    case = CAPTURED["md-math"]
    r = client.post("/api/preview-send", json={"post": {"rich_message": {"markdown": case["source"]}}})
    assert r.status_code == 200, r.text
    parsed = r.json()["parsed"]
    assert parsed["blocks"] == case["blocks"]
    assert parsed["input_blocks"][1] == {"type": "mathematical_expression", "expression": "E=mc^2"}


def test_odd_block_shapes_do_not_break_the_converter(current):
    odd = [
        {"type": "blockquote", "blocks": None},
        {"type": "blockquote", "blocks": "x"},
        {"type": "photo", "photo": [1, 2]},
        {"type": "video", "video": "v"},
    ]
    quote_none, quote_str, photo, video = spec.to_input_blocks(current, odd)
    assert quote_none["blocks"] is None and quote_str["blocks"] == "x"
    assert photo["photo"]["media"] == "" and video["video"]["media"] == ""


def test_preview_answers_even_if_the_conversion_fails(client, tg, monkeypatch):
    def broken(*_):
        raise TypeError("unexpected shape")

    monkeypatch.setattr(spec, "to_input_blocks", broken)
    post = {"rich_message": {"markdown": "x"}, "callbacks": {"go": "ok"}, "comment": {"html": "<p>c</p>"}}
    r = client.post("/api/preview-send", json={"post": post})
    assert r.status_code == 200, r.text
    assert "blocks" in r.json()["parsed"] and "input_blocks" not in r.json()["parsed"]
    # the steps after the send still ran: the first comment went out as a reply
    assert tg.last("sendRichMessage")["body"]["rich_message"] == {"html": "<p>c</p>"}
