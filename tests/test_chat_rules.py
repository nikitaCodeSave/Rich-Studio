"""What Telegram refuses in the chat a post goes to (issue #166): the rules come from the descriptions in api.json
(spec/chats.py), they depend on the chat type the studio knows from «Каналы», and they are warnings. And
ephemeral_message_parameters never turns a publication into an ephemeral message."""

import asyncio
import copy
import time

import spec
import store
from services import scheduler
from spec import chats
from test_mcp import call

TEXT = {"html": "<p>Пост</p>"}
CHANNEL = {"ok": True, "result": {"id": -1001, "type": "channel", "title": "Новости"}}
FORUM = {"ok": True, "result": {"id": -1002, "type": "supergroup", "title": "Форум", "is_forum": True}}
WEB_APP = {"text": "Приложение", "web_app": {"url": "https://example.com/app"}}
INLINE = {"text": "Запрос", "switch_inline_query_current_chat": "x"}
SWITCH = {"text": "Поделиться", "switch_inline_query": "x"}
LINK = {"text": "Сайт", "url": "https://example.com"}


def _checked(client, tg, chat_id: str, answer: dict) -> None:
    tg.queue["getChat"] = [answer]
    assert client.post("/api/channels", json={"chat_id": chat_id}).status_code == 200


def _validate(client, post: dict, *chat_ids: str) -> dict:
    r = client.post("/api/validate", params=[("chat_ids", c) for c in chat_ids], json=post)
    assert r.status_code == 200, r.text
    return r.json()


def _about(warnings: list[str], what: str) -> list[str]:
    return [w for w in warnings if what in w]


def test_the_rules_of_bot_api_10_4_are_read_from_its_descriptions(client):  # client: the spec is loaded
    rs = chats.rules(spec.current)
    channel_group = {"channel", "group", "supergroup"}
    for key in ("InlineKeyboardButton.web_app", "RichMessageButton.web_app", "sendRichMessage.message_effect_id"):
        assert channel_group <= rs[key].refused.keys() and "private" not in rs[key].refused, key
        assert not any(said.measured for said in rs[key].refused.values())
    for key in ("InlineKeyboardButton.switch_inline_query_current_chat", "ReplyKeyboardMarkup", "ForceReply"):
        assert set(rs[key].refused) == {"channel"} and "Not supported in channels" in rs[key].refused["channel"].why
    assert rs["InputRichBlockThinking"].only_in == "sendRichMessageDraft"
    assert "https://t.me/<бот>/<приложение>?startapp=" in rs["InlineKeyboardButton.web_app"].hint
    # the description of switch_inline_query says nothing of channels: the measurement does (validator.REFUSED_IN)
    measured = rs["InlineKeyboardButton.switch_inline_query"]
    assert set(measured.refused) == {"channel"} and measured.refused["channel"].measured
    # fields with no such phrase work everywhere as far as the spec says
    for key in ("InlineKeyboardButton.url", "InlineKeyboardButton.callback_data", "sendRichMessage.protect_content"):
        assert key not in rs
    # the summary the editor reads is the same rules
    summary = chats.summary(spec.current)
    assert set(summary["InlineKeyboardButton.web_app"]["refused"]) == set(rs["InlineKeyboardButton.web_app"].refused)


def _spec_with(edit) -> spec.Spec:
    s = spec.Spec()
    s.data = copy.deepcopy(spec.current.data)
    edit(s.data)
    return s


def _field(data: dict, type_name: str, name: str) -> dict:
    return next(f for f in data["types"][type_name]["fields"] if f["name"] == name)


def test_a_rule_is_the_phrase_of_the_description_not_a_list_in_the_code(client):
    post = {"rich_message": TEXT, "reply_markup": {"inline_keyboard": [[WEB_APP, {"text": "Код", "copy_text": {}}]]}}
    where = [chats.place("channel", False, "Новости", "@news")]
    assert _about(chats.warnings(spec.current, post, where), "web_app")

    def reword(data):
        web_app = _field(data, "InlineKeyboardButton", "web_app")
        web_app["description"] = web_app["description"].replace("Available only in private chats", "Shown")
        copy_text = _field(data, "InlineKeyboardButton", "copy_text")
        copy_text["description"] += " Available only in private chats."

    other = _spec_with(reword)
    said = chats.warnings(other, post, where)
    # the field without the phrase gives no warning any more, the one that got it gives one
    assert not _about(said, "web_app") and _about(said, "copy_text")
    # the same phrase on a method parameter or a type works the same way
    plain = {"rich_message": TEXT, "options": {"protect_content": True}}
    assert not chats.warnings(other, plain, where)

    def protect(data):
        f = next(x for x in data["methods"]["sendRichMessage"]["fields"] if x["name"] == "protect_content")
        f["description"] += "; not supported in channels"

    assert _about(chats.warnings(_spec_with(protect), plain, where), "options.protect_content")


def test_warnings_depend_on_the_chat_type(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    _checked(client, tg, "@forum", FORUM)
    post = {
        "rich_message": {"blocks": [{"type": "buttons", "buttons": [WEB_APP, INLINE]}]},
        "reply_markup": {"inline_keyboard": [[WEB_APP, INLINE, SWITCH, LINK]]},
        "options": {"message_effect_id": "5104841245755180586"},
    }
    channel = _validate(client, post, "@news")
    assert channel["ok"] and not channel["errors"], channel  # warnings, not errors: the post still goes as it is
    w = channel["warnings"]
    [button] = _about(w, "кнопка «Приложение» (ряд 1, №1), web_app")
    assert "в канале «Новости»" in button and "Available only in private chats" in button
    assert "ephemeral" not in button  # only the sentence that rules out this chat
    assert "https://t.me/<бот>/<приложение>?startapp=" in button
    assert _about(w, "rich_message.blocks[0].buttons[0].web_app: не работает в канале «Новости»")
    assert _about(w, "кнопка «Запрос» (ряд 1, №2), switch_inline_query_current_chat")
    assert _about(w, "rich_message.blocks[0].buttons[1].switch_inline_query_current_chat")
    [switch] = _about(w, "кнопка «Поделиться» (ряд 1, №3), switch_inline_query")
    assert "замер" in switch and "can't use" in switch
    assert _about(w, "options.message_effect_id: не работает в канале «Новости»")
    assert not _about(w, "«Сайт»")
    # in a forum supergroup an inline query button works; web_app and the message effect do not
    forum = _validate(client, post, "@forum")["warnings"]
    assert _about(forum, "web_app: не работает в форуме «Форум»")
    assert _about(forum, "options.message_effect_id: не работает в форуме «Форум»")
    assert not _about(forum, "switch_inline_query")
    # several chats: each thing once, with every chat that refuses it
    both = _validate(client, post, "@news", "@forum")["warnings"]
    [effect] = _about(both, "options.message_effect_id")
    assert "в канале «Новости», в форуме «Форум»" in effect
    assert len(_about(both, "switch_inline_query_current_chat")) == 2
    # a chat nobody checked has no type: nothing is guessed
    assert not _validate(client, post, "@never_checked")["warnings"]


def test_reply_keyboards_and_the_thinking_block(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    _checked(client, tg, "@forum", FORUM)
    for markup, name in (
        ({"keyboard": [[{"text": "Да"}]], "resize_keyboard": True}, "ReplyKeyboardMarkup"),
        ({"force_reply": True}, "ForceReply"),
        ({"remove_keyboard": True}, "ReplyKeyboardRemove"),
    ):
        post = {"rich_message": TEXT, "reply_markup": markup}
        assert _about(_validate(client, post, "@news")["warnings"], f"reply_markup: {name} не работает в канале")
        assert not _validate(client, post, "@forum")["warnings"]
    # an inline keyboard has a force_reply field of its own, with nothing said about channels
    inline = {"rich_message": TEXT, "reply_markup": {"inline_keyboard": [[LINK]], "force_reply": True}}
    assert not _validate(client, inline, "@news")["warnings"]
    # the thinking block is for sendRichMessageDraft only: a warning in any chat, and with no chat known at all
    blocks = {"rich_message": {"blocks": [{"type": "thinking", "text": "Думаю"}, {"type": "paragraph", "text": "x"}]}}
    html = {"rich_message": {"html": "<tg-thinking><p>Думаю</p></tg-thinking><p>Ответ</p>"}}
    for post, where in ((blocks, "rich_message.blocks[0]: InputRichBlockThinking"), (html, "<tg-thinking>")):
        for chat in ("@forum", "@never_checked"):
            [w] = _validate(client, post, chat)["warnings"]
            assert where in w and "sendRichMessageDraft" in w, w
    tagged = {
        "rich_message": {"html": '<p>x</p><tg-button type="web_app" url="https://example.com/app">App</tg-button>'}
    }
    [w] = _validate(client, tagged, "@news")["warnings"]
    assert '<tg-button type="web_app"> не работает в канале «Новости»' in w


def test_first_comment_and_rich_reply_have_their_own_place(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    comment = {"blocks": [{"type": "buttons", "buttons": [WEB_APP]}]}
    login = {"text": "Войти", "login_url": {"url": "https://example.com/login"}}
    reply = {"rich_message": TEXT, "reply_markup": {"inline_keyboard": [[login]]}}
    post = {
        "rich_message": TEXT,
        "reply_markup": {"inline_keyboard": [[{"text": "Жми", "callback_data": "go"}]]},
        "comment": comment,
        "callbacks": {"go": {"text": "ок", "reply": reply}},
    }
    w = _validate(client, post, "@news")["warnings"]
    # the first comment goes to the discussion group: a supergroup, whatever the post's chat
    assert _about(w, "комментарий: rich_message.blocks[0].buttons[0].web_app: не работает в группе обсуждения")
    # a rich reply is an ephemeral message: «Not supported for ephemeral messages»
    [eph] = _about(w, "ответ на кнопку «Жми»: reply.reply_markup.inline_keyboard[0][0].login_url")
    assert "в rich-ответе на нажатие" in eph and "ephemeral messages" in eph


def test_publish_and_drafts_say_it_too(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    post = {"rich_message": TEXT, "reply_markup": {"inline_keyboard": [[WEB_APP]]}}
    dry = client.post("/api/send", json={"post": post, "chat_ids": ["@news"], "dry_run": True}).json()
    assert _about(dry["warnings"], "web_app: не работает в канале «Новости»")
    sent = client.post("/api/send", json={"post": post, "chat_ids": ["@news"]})
    assert sent.status_code == 200, sent.text  # Telegram decides: the mock takes it
    assert _about(sent.json()["warnings"], "web_app: не работает в канале «Новости»")
    # MCP: save_draft checks against the draft's chats, validate_post against the chats given
    _, saved, _ = call(client, "save_draft", post=post, title="Кнопка", chat_ids=["@news"])
    assert _about(saved["validation"]["warnings"], "в канале «Новости»")
    _, shown, _ = call(client, "preview_post", draft_id=saved["id"])
    assert _about(shown["validation"]["warnings"], "в канале «Новости»")
    _, checked, _ = call(client, "validate_post", post=post, chat_ids=["@news"])
    assert _about(checked["warnings"], "в канале «Новости»")
    # the default channel (@test_channel) was never checked: no type, no warning
    _, plain, _ = call(client, "validate_post", post=post)
    assert not plain["warnings"]


def test_a_broken_payload_gives_no_warning_and_no_500(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    odd = [
        {"rich_message": {"blocks": [1, "x", {"type": "buttons", "buttons": [None, "y", {"web_app": 5}]}]}},
        {
            "rich_message": {"html": "<tg-button type>x</tg-button><tg-thinking"},
            "reply_markup": {"inline_keyboard": "x"},
        },
        {"rich_message": TEXT, "reply_markup": {"keyboard": [["Да", 1]]}, "options": {"message_effect_id": None}},
    ]
    for post in odd:
        assert client.post("/api/validate", params={"chat_ids": "@news"}, json=post).status_code == 200
    deep: dict = {"blocks": []}
    for _ in range(300):  # deeper than any post, as validate_rich_message refuses it: no RecursionError here either
        deep = {"type": "blockquote", "blocks": [deep]}
    assert chats.warnings(spec.current, {"rich_message": {"blocks": [deep]}}, []) == []


def test_spec_summary_carries_the_rules(client):
    s = client.get("/api/spec").json()
    rule = s["chat_rules"]["InlineKeyboardButton.web_app"]
    assert rule["refused"]["channel"] == {
        "why": "Available only in private chats between a user and the bot.",
        "measured": None,
    }
    assert rule["hint"] and "ephemeral messages" in rule["refused"]["ephemeral"]["why"]
    assert s["chat_rules"]["InputRichBlockThinking"]["only_in"] == "sendRichMessageDraft"
    # options cannot hold ephemeral_message_parameters, so the editor does not suggest it
    options = {f["name"] for f in s["post"]["types"]["PostOptions"]["fields"]}
    assert "ephemeral_message_parameters" not in options and "message_effect_id" in options


EPHEMERAL = {"rich_message": TEXT, "options": {"ephemeral_message_parameters": {"callback_query_id": "123"}}}


def test_ephemeral_parameters_never_turn_a_post_into_an_ephemeral_message(client, tg):
    """With callback_query_id Telegram ignores chat_id and shows the message to whoever pressed some button, and the
    studio would record it as a post of the channel; with receiver_user_id it is one member's. Refused in options,
    as chat_id is: a rich reply to a press is callbacks[…].reply."""
    before = len(tg.calls)
    for route, extra in (
        ("/api/send", {}),
        ("/api/schedule", {"at": int(time.time()) + 3600}),
        ("/api/preview-send", {}),
    ):
        for eph in ({"callback_query_id": "123"}, {"receiver_user_id": 5}):
            post = {**EPHEMERAL, "options": {"ephemeral_message_parameters": eph}}
            r = client.post(route, json={"post": post, **extra})
            assert r.status_code == 400, (route, r.text)
            assert "ephemeral_message_parameters" in r.text and "callbacks[…].reply" in r.text
    assert [c for c in tg.calls[before:] if c["method"] == "sendRichMessage"] == []
    assert store.list_posts() == []
    # the check says so before anyone publishes: validate_post, save_draft, the editor
    report = _validate(client, EPHEMERAL)
    assert not report["ok"] and _about(report["errors"], "в options нельзя задавать ephemeral_message_parameters")
    _, saved, _ = call(client, "save_draft", post=EPHEMERAL, title="Эфемерный")
    assert not saved["validation"]["ok"]


def test_a_stored_post_with_ephemeral_parameters_is_not_sent_by_the_scheduler(client, tg):
    """A post scheduled before the rule (or written to the database by hand) does not go out either."""
    [rec] = store.add_scheduled(["@test_channel"], EPHEMERAL, int(time.time()) - 5)
    before = len(tg.calls)
    asyncio.run(scheduler.run_due_posts())
    assert [c for c in tg.calls[before:] if c["method"] == "sendRichMessage"] == []
    p = store.get_post(rec["id"])
    assert p["status"] == "failed" and "ephemeral_message_parameters" in p["error"]
