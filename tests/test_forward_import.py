"""A post the owner forwards to the bot becomes a draft with the same formatting; nothing goes to a channel.

Each file in tests/forward_import/ is exactly one update: the owner's forward of a post from a public channel, with
the owner's own id, name and username replaced by made-up ones. Never a whole getUpdates answer: the repository is
public, and that answer also holds other people's clicks, joins, comments and private messages.
rich_channel_post.json is built from app/bundled/api.json (a test checks it against the spec); a real forward is
recorded by the steps in PR #120. Every file there must become a draft without validation errors.
"""

import asyncio
import json
from pathlib import Path

import pytest

import settings
import spec
import store
from services import updates
from test_mcp import call_list

FIXTURES = Path(__file__).parent / "forward_import"
OWNER = {"id": 777, "is_bot": False, "first_name": "Никита"}
DM = {"id": 777, "type": "private", "first_name": "Никита"}
FROM_CHANNEL = {
    "type": "channel",
    "date": 1763100000,
    "chat": {"id": -1009, "type": "channel", "title": "Канал друга", "username": "friend"},
    "message_id": 7,
}


def _update(path: Path) -> dict:
    """One saved update: the owner's forward and nothing else (see the docstring above)."""
    u = json.loads(path.read_text("utf-8"))
    assert set(u) <= {"update_id", "message"}, (
        f"{path.name}: one update with a message per file, not a getUpdates answer"
    )
    m = u["message"]
    assert m["chat"]["type"] == "private" and m["chat"]["id"] == m["from"]["id"], f"{path.name}: the owner's own chat"
    assert (m.get("forward_origin") or {"type": "channel"})["type"] == "channel", f"{path.name}: forward from a channel"
    return u


def _forward(message_id=10, **content) -> dict:
    return {
        "message": {
            "message_id": message_id,
            "date": 1763200000,
            "from": OWNER,
            "chat": DM,
            "forward_origin": FROM_CHANNEL,
            **content,
        }
    }


def _handle(u: dict):
    return asyncio.run(updates.handle_update(u))


def _calls(tg) -> list[str]:
    return [c["method"] for c in tg.calls]


def test_the_rich_fixture_is_a_message_by_the_spec(client):
    v = spec.Validator(spec.current)
    v.check(_update(FIXTURES / "rich_channel_post.json"), ["Update"], "update")
    assert not v.errors and not v.warnings, (v.errors, v.warnings)


def test_a_forwarded_rich_post_becomes_a_draft(client, tg):
    assert _handle(_update(FIXTURES / "rich_channel_post.json")) == "forward_import"
    [d] = store.list_drafts()
    assert d["chat_ids"] is None  # no channel: it goes nowhere until the person picks one
    assert "Чужой канал" in d["title"] and "Итоги недели" in d["title"]
    payload = store.get_draft(d["id"])["payload"]
    assert set(payload) == {"rich_message", "reply_markup", "_studio"}
    blocks = payload["rich_message"]["blocks"]
    # media by Telegram's file_id (the largest photo), nothing is downloaded or uploaded
    assert blocks[2]["photo"] == {"type": "photo", "media": "AgAC-big", "has_spoiler": True}
    assert blocks[5]["video"] == {"type": "video", "media": "BAAC-video"}
    assert "label" not in blocks[3]["items"][0]  # output-only field of Telegram's answer
    # buttons answered by the bot of the original are gone, links stay
    assert blocks[6]["buttons"] == [{"text": "Читать", "url": "https://example.com/read"}]
    assert [b["type"] for b in blocks].count("buttons") == 1
    assert blocks[7]["text"] == [
        "Нажмите тут или ",
        {"type": "button", "button": {"text": "здесь", "url": "https://example.com/here"}},
    ]
    assert payload["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Сайт", "url": "https://example.com"}],
            [{"text": "Скопировать", "copy_text": {"text": "промокод"}}],
        ]
    }
    # where it came from is the studio's own note, it never goes to Telegram
    source = payload["_studio"]["source"]
    assert source["link"] == "https://t.me/other_channel/42"
    assert source["forward_origin"]["chat"]["title"] == "Чужой канал"
    assert any("кнопк" in w for w in payload["_studio"]["warnings"])
    v = client.post("/api/validate", json=payload).json()
    assert v["ok"] and not v["errors"], v
    # the person sees it: in the editor's list, in MCP and as a preview page
    assert [x["id"] for x in client.get("/api/drafts").json()] == [d["id"]]
    assert [x["id"] for x in call_list(client, "list_drafts")] == [d["id"]]
    assert client.get(f"/render/draft/{d['id']}").status_code == 200
    # the only answer is a reaction on the forwarded message; nothing is sent anywhere
    assert _calls(tg) == ["setMessageReaction"]
    body = tg.last("setMessageReaction")["body"]
    assert body["chat_id"] == 777 and body["message_id"] == 501
    assert body["reaction"][0]["type"] == "emoji"


def test_text_and_entities_of_a_plain_post_keep_their_bounds(client, tg):
    # UTF-16: 😀 and 👍🏽 take 2 and 4 code units, so the offsets are not Python's string indices
    text = "😀 жирный 👍🏽 ссылка"
    entities = [
        {"type": "bold", "offset": 3, "length": 6},
        {"type": "custom_emoji", "offset": 10, "length": 4, "custom_emoji_id": "123"},
        {"type": "text_link", "offset": 15, "length": 6, "url": "https://example.com"},
    ]
    assert _handle(_forward(text=text, entities=entities)) == "forward_import"
    [d] = store.list_drafts()
    payload = store.get_draft(d["id"])["payload"]
    assert payload["rich_message"]["blocks"] == [
        {
            "type": "paragraph",
            "text": [
                "😀 ",
                {"type": "bold", "text": "жирный"},
                " ",
                {"type": "custom_emoji", "custom_emoji_id": "123", "alternative_text": "👍🏽"},
                " ",
                {"type": "url", "text": "ссылка", "url": "https://example.com"},
            ],
        }
    ]
    assert payload["_studio"]["warnings"] == []
    assert client.post("/api/validate", json=payload).json()["ok"]


def _blocks(text, entities):
    warnings: list[str] = []
    return spec.text_blocks(spec.current, text, entities, warnings), warnings


def test_nested_and_crossing_entities(client):
    blocks, warnings = _blocks(
        "abcdef xy",
        [
            {"type": "bold", "offset": 0, "length": 4},
            {"type": "italic", "offset": 2, "length": 4},  # crosses the end of bold: split, no text lost
            {"type": "underline", "offset": 7, "length": 2},
            {"type": "spoiler", "offset": 7, "length": 2},  # the same range: the first one is outside
        ],
    )
    assert blocks == [
        {
            "type": "paragraph",
            "text": [
                {"type": "bold", "text": ["ab", {"type": "italic", "text": "cd"}]},
                {"type": "italic", "text": "ef"},
                " ",
                {"type": "underline", "text": {"type": "spoiler", "text": "xy"}},
            ],
        }
    ]
    assert warnings == []


def test_entities_telegram_finds_again_unknown_ones_and_broken_ranges(client):
    text = "#тег @user https://t.me 😀a новое 4111 1111 1111 1111"
    blocks, warnings = _blocks(
        text,
        [
            {"type": "hashtag", "offset": 0, "length": 4},
            {"type": "mention", "offset": 5, "length": 5},
            {"type": "url", "offset": 11, "length": 12},
            {"type": "bold", "offset": 25, "length": 2},  # starts inside the surrogate pair of 😀
            {"type": "sparkle", "offset": 28, "length": 5},  # a type the spec does not know
            {"type": "bank_card_number", "offset": 34, "length": 19},
            {"type": "date_time", "offset": 0, "length": 4, "unix_time": 1},  # no format: RichText needs one
        ],
    )
    # hashtags, mentions and links stay text: Telegram finds them again when the post is sent
    assert blocks == [{"type": "paragraph", "text": text}]
    assert len(warnings) == 3
    assert any("sparkle" in w for w in warnings) and any("date_time" in w for w in warnings)


def test_pre_and_quotes_become_blocks_and_blank_lines_paragraphs(client):
    text = "Вступление\n\nкод\nещё\nЦитата строка\n\nХвост\nвторая строка\nСвёрнутая"
    blocks, warnings = _blocks(
        text,
        [
            {"type": "pre", "offset": 12, "length": 7, "language": "python"},
            {"type": "blockquote", "offset": 20, "length": 13},
            {"type": "italic", "offset": 27, "length": 6},
            {"type": "expandable_blockquote", "offset": 55, "length": 9},
        ],
    )
    assert blocks == [
        {"type": "paragraph", "text": "Вступление"},
        {"type": "pre", "text": "код\nещё", "language": "python"},
        {
            "type": "blockquote",
            "blocks": [{"type": "paragraph", "text": ["Цитата ", {"type": "italic", "text": "строка"}]}],
        },
        {"type": "paragraph", "text": "Хвост\nвторая строка"},
        {"type": "expandable_blockquote", "text": "Свёрнутая"},
    ]
    assert warnings == []
    assert spec.validate_rich_message(spec.current, {"blocks": blocks})["ok"]


def test_a_photo_with_a_caption(client, tg):
    photo = [
        {"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
        {"file_id": "large", "file_unique_id": "l", "width": 1280, "height": 1280},
    ]
    caption = "Подпись 🔥 с кодом"
    ents = [{"type": "code", "offset": 11, "length": 7}]
    _handle(_forward(photo=photo, caption=caption, caption_entities=ents, has_media_spoiler=True))
    [first] = store.list_drafts()
    assert store.get_draft(first["id"])["payload"]["rich_message"]["blocks"] == [
        {"type": "photo", "photo": {"type": "photo", "media": "large", "has_spoiler": True}},
        {"type": "paragraph", "text": ["Подпись 🔥 ", {"type": "code", "text": "с кодом"}]},
    ]
    # the caption above the photo stays above it
    _handle(_forward(11, photo=photo, caption="Сверху", show_caption_above_media=True))
    [second] = [d for d in store.list_drafts() if d["id"] != first["id"]]
    above = store.get_draft(second["id"])["payload"]["rich_message"]["blocks"]
    assert [b["type"] for b in above] == ["paragraph", "photo"]


def test_buttons_that_work_only_for_the_original_bot_are_removed(client, tg):
    # each of these acts for the bot that sent the post: its callbacks, its web app, its login, its inline mode
    bot_only = [
        {"text": "a", "callback_data": "x"},
        {"text": "b", "web_app": {"url": "https://app.example.com"}},
        {"text": "c", "login_url": {"url": "https://example.com/login"}},
        {"text": "d", "switch_inline_query": "q"},
        {"text": "e", "switch_inline_query_current_chat": "q"},
        {"text": "f", "switch_inline_query_chosen_chat": {"query": "q", "allow_user_chats": True}},
        {"text": "g", "callback_game": {}},
        {"text": "h", "pay": True},
    ]
    kept = [{"text": "Сайт", "url": "https://example.com"}, {"text": "Код", "copy_text": {"text": "PROMO"}}]
    rich = {"blocks": [{"type": "paragraph", "text": "Пост"}, {"type": "buttons", "buttons": [*bot_only[:6], *kept]}]}
    markup = {"inline_keyboard": [bot_only, kept]}
    assert _handle(_forward(rich_message=rich, reply_markup=markup)) == "forward_import"
    payload = store.get_draft(store.list_drafts()[0]["id"])["payload"]
    assert payload["rich_message"]["blocks"][1]["buttons"] == kept
    assert payload["reply_markup"] == {"inline_keyboard": [kept]}
    assert any("(14)" in w for w in payload["_studio"]["warnings"])


def test_content_a_rich_post_has_no_block_for_is_named(client, tg):
    still = [{"file_id": "still", "file_unique_id": "s", "width": 720, "height": 960}]
    live = {"file_id": "lp", "file_unique_id": "lp", "width": 720, "height": 960, "duration": 2}
    _handle(_forward(50, live_photo=live, photo=still, caption="Живое фото"))  # Telegram also sends the still photo
    paid = {"star_count": 5, "paid_media": [{"type": "preview", "width": 10, "height": 10}]}
    _handle(_forward(51, paid_media=paid, caption="За звёзды"))
    by_title = {d["title"]: store.get_draft(d["id"])["payload"] for d in store.list_drafts()}
    live_draft = by_title["Канал друга: Живое фото"]
    assert [b["type"] for b in live_draft["rich_message"]["blocks"]] == ["photo", "paragraph"]
    assert any("live_photo" in w for w in live_draft["_studio"]["warnings"])
    paid_draft = by_title["Канал друга: За звёзды"]
    assert paid_draft["rich_message"]["blocks"] == [{"type": "paragraph", "text": "За звёзды"}]
    assert any("paid_media" in w for w in paid_draft["_studio"]["warnings"])
    # nothing but such content: a refusal that names it
    _handle(_forward(52, paid_media=paid))
    assert len(store.list_drafts()) == 2 and "paid_media" in tg.last("sendMessage")["body"]["text"]


def test_an_album_is_one_draft(client, tg):
    def item(mid, file_id, **extra):
        size = {"file_id": file_id, "file_unique_id": file_id, "width": 800, "height": 600}
        return _forward(mid, media_group_id="album-1", photo=[size], **extra)

    _handle(item(20, "p1", caption="Три фото"))
    _handle(item(21, "p2"))
    _handle(item(22, "p3"))
    [d] = store.list_drafts()
    blocks = store.get_draft(d["id"])["payload"]["rich_message"]["blocks"]
    assert blocks[0]["type"] == "collage"
    assert [b["photo"]["media"] for b in blocks[0]["blocks"]] == ["p1", "p2", "p3"]
    assert blocks[1:] == [{"type": "paragraph", "text": "Три фото"}]
    assert _calls(tg) == ["setMessageReaction"] * 3


def test_an_album_draft_edited_meanwhile_is_not_overwritten(client, tg):
    size = {"file_id": "a1", "file_unique_id": "a1", "width": 800, "height": 600}
    _handle(_forward(40, media_group_id="album-2", photo=[size], caption="Было"))
    [d] = store.list_drafts()
    mine = {"rich_message": {"blocks": [{"type": "paragraph", "text": "Моя правка"}]}}
    store.save_draft(d["id"], d["title"], mine, d["version"])  # the person edits it before the next item comes
    _handle(_forward(41, media_group_id="album-2", photo=[{**size, "file_id": "a2", "file_unique_id": "a2"}]))
    assert store.get_draft(d["id"])["payload"] == mine
    [rest] = [x for x in store.list_drafts() if x["id"] != d["id"]]
    assert store.get_draft(rest["id"])["payload"]["rich_message"]["blocks"][0]["photo"]["media"] == "a2"


@pytest.mark.parametrize(
    "change",
    [
        {"chat": {"id": 555, "type": "private", "first_name": "Чужой"}, "from": {**OWNER, "id": 555}},
        {"chat": {"id": -100500, "type": "supergroup", "title": "Группа"}},
        {"from": {**OWNER, "is_bot": True}},
        {"forward_origin": None},  # an ordinary message from the owner (/start, a question) is not a post
    ],
    ids=["stranger", "group", "bot", "not-a-forward"],
)
def test_only_a_forward_from_the_owner_in_the_private_chat_counts(client, tg, change):
    u = _forward(text="Пост")
    u["message"].update({k: v for k, v in change.items() if v is not None})
    for k in [k for k, v in change.items() if v is None]:
        del u["message"][k]
    assert _handle(u) is None
    assert store.list_drafts() == [] and tg.calls == []  # not even an answer


@pytest.mark.parametrize("owner", ["", "@nikita", "-1001234"])
def test_without_a_personal_preview_chat_nothing_is_imported(client, tg, monkeypatch, owner):
    monkeypatch.setattr(settings.get(), "preview_chat", owner)
    assert _handle(_forward(text="Пост")) is None
    assert store.list_drafts() == [] and tg.calls == []


@pytest.mark.parametrize(
    "content, reason",
    [
        ({"text": "Секрет", "has_protected_content": True}, "защищ"),
        ({"sticker": {"file_id": "s", "file_unique_id": "s", "type": "regular", "width": 1, "height": 1,
                      "is_animated": False, "is_video": False}}, "нечего"),
        ({"rich_message": {"blocks": [{"type": "buttons", "buttons": [{"text": "Да", "callback_data": "yes"}]}]}},
         "только кнопки"),
    ],
    ids=["protected", "nothing-to-take", "only-bot-buttons"],
)  # fmt: skip
def test_a_refusal_is_a_short_reply_to_the_owner(client, tg, content, reason):
    assert _handle(_forward(30, **content)) == "forward_refused"
    assert store.list_drafts() == []
    assert _calls(tg) == ["sendMessage"]
    body = tg.last("sendMessage")["body"]
    assert body["chat_id"] == 777 and reason in body["text"].lower()
    assert body["reply_parameters"]["message_id"] == 30


def test_a_failed_reaction_keeps_the_draft(client, tg):
    tg.fail["setMessageReaction"] = "Bad Request: REACTION_INVALID"
    assert _handle(_forward(text="Пост")) == "forward_import"
    assert len(store.list_drafts()) == 1 and _calls(tg) == ["setMessageReaction"]


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.json")), ids=lambda p: p.stem)
def test_every_saved_forward_becomes_a_valid_draft(client, tg, monkeypatch, path):
    u = _update(path)
    monkeypatch.setattr(settings.get(), "preview_chat", str(u["message"]["from"]["id"]))
    assert _handle(u) == "forward_import"
    payload = store.get_draft(store.list_drafts()[0]["id"])["payload"]
    v = client.post("/api/validate", json=payload).json()
    assert v["ok"], v["errors"]
