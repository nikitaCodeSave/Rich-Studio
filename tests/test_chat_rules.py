"""What Telegram refuses in the chat a post goes to (issue #166): the rules come from the descriptions in api.json
(spec/chats.py) and from measurements (validator.REFUSED_IN), they depend on the chat type the studio knows from
«Каналы», and they are warnings. And ephemeral_message_parameters never turns a publication into an ephemeral message."""

import asyncio
import copy
import time

import spec
import store
from services import publisher, scheduler
from spec import chats
from test_mcp import call

TEXT = {"html": "<p>Пост</p>"}
CHANNEL = {"ok": True, "result": {"id": -1001, "type": "channel", "title": "Новости"}}
FORUM = {"ok": True, "result": {"id": -1002, "type": "supergroup", "title": "Форум", "is_forum": True}}
WEB_APP = {"text": "Приложение", "web_app": {"url": "https://example.com/app"}}
INLINE = {"text": "Запрос", "switch_inline_query_current_chat": "x"}
SWITCH = {"text": "Поделиться", "switch_inline_query": "x"}
CHOSEN = {"text": "Выбрать чат", "switch_inline_query_chosen_chat": {"query": "x", "allow_user_chats": True}}
GAME = {"text": "Игра", "callback_game": {}}
LINK = {"text": "Сайт", "url": "https://example.com"}
PRIVATE_ONLY = {"channel", "group", "supergroup"}

# What the descriptions of Bot API 10.4 say, as spec/chats.py reads them: rule → the chat types a sentence of the spec
# rules out. After `make spec-update` a reworded phrase shows here: a rule that lost its sentence keeps only its
# measurement (or goes), and a new one appears. Check the new wording before changing this table.
FROM_SPEC = {
    "sendRichMessage.message_effect_id": PRIVATE_ONLY,
    "sendRichMessage.suggested_post_parameters": {"channel", "group", "private"},
    "InlineKeyboardButton.web_app": PRIVATE_ONLY | {"ephemeral"},
    "InlineKeyboardButton.login_url": {"ephemeral"},
    "InlineKeyboardButton.switch_inline_query_current_chat": {"channel"},
    "RichMessageButton.web_app": PRIVATE_ONLY,
    "RichMessageButton.login_url": {"ephemeral"},
    "RichMessageButton.switch_inline_query_current_chat": {"channel"},
    "KeyboardButton.request_users": PRIVATE_ONLY,
    "KeyboardButton.request_chat": PRIVATE_ONLY,
    "KeyboardButton.request_managed_bot": PRIVATE_ONLY,
    "KeyboardButton.request_contact": PRIVATE_ONLY,
    "KeyboardButton.request_location": PRIVATE_ONLY,
    "KeyboardButton.request_poll": PRIVATE_ONLY,
    "KeyboardButton.web_app": PRIVATE_ONLY,
    "ReplyKeyboardMarkup": {"channel"},
    "ReplyKeyboardRemove": {"channel"},
    "ForceReply": {"channel"},
    "ReplyParameters.chat_id": {"ephemeral"},
}
# what only the measurement says (2026-11-16): no description has it
MEASURED_ONLY = {
    "InlineKeyboardButton.switch_inline_query": {"channel"},
    "InlineKeyboardButton.switch_inline_query_chosen_chat": {"channel"},
    "InlineKeyboardButton.callback_game": {"channel", "supergroup"},
    "RichMessageButton.switch_inline_query": {"channel"},
    "RichMessageButton.switch_inline_query_chosen_chat": {"channel"},
    "InputRichBlockThinking": {"channel", "supergroup"},  # the spec says it as only_in, for every post
}


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
    said = {key: {c for c, s in r.refused.items() if s.why} for key, r in rs.items()}
    assert {key: chats_ for key, chats_ in said.items() if chats_} == FROM_SPEC
    measured = {key: {c for c, s in r.refused.items() if s.answer and not s.why} for key, r in rs.items()}
    assert {key: chats_ for key, chats_ in measured.items() if chats_} == MEASURED_ONLY
    assert rs["InputRichBlockThinking"].only_in == "sendRichMessageDraft"
    assert {key for key, r in rs.items() if r.only_in} == {"InputRichBlockThinking"}
    # where the spec says it and Telegram refused it too, the warning says both
    web_app = rs["InlineKeyboardButton.web_app"].refused["channel"]
    assert web_app.why.startswith("Available only in private chats") and web_app.answer == "BUTTON_TYPE_INVALID"
    assert rs["ReplyKeyboardRemove"].refused["channel"].answer == "inline keyboard expected"
    # the direct link is the way out for an inline or a rich message button; a reply keyboard has no link button
    assert "https://t.me/<бот>/<приложение>?startapp=" in rs["InlineKeyboardButton.web_app"].hint
    assert rs["RichMessageButton.web_app"].hint and not rs["KeyboardButton.web_app"].hint
    # fields with no such phrase and no measurement work everywhere as far as the studio knows
    for key in ("InlineKeyboardButton.url", "InlineKeyboardButton.callback_data", "sendRichMessage.protect_content"):
        assert key not in rs


def _spec_with(edit) -> spec.Spec:
    s = spec.Spec()
    s.data = copy.deepcopy(spec.current.data)
    edit(s.data)
    return s


def _field(data: dict, type_name: str, name: str) -> dict:
    return next(f for f in data["types"][type_name]["fields"] if f["name"] == name)


def test_a_rule_is_the_phrase_of_the_description_not_a_list_in_the_code(client):
    copy_button = {"text": "Код", "copy_text": {"text": "x"}}
    post = {"rich_message": TEXT, "reply_markup": {"inline_keyboard": [[WEB_APP, copy_button]]}}
    where = [chats.place("group", False, "Группа", "@group")]  # nothing measured in a basic group: the spec alone
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
        f["description"] += ". Not supported in channels."

    channel = [chats.place("channel", False, "Новости", "@news")]
    assert not chats.warnings(spec.current, plain, channel)
    assert _about(chats.warnings(_spec_with(protect), plain, channel), "options.protect_content")


def test_only_a_method_makes_a_rule_for_every_post(client):
    """«may be used only in <method>» rules out every post; the same words with something else are not a method."""
    button = {"text": "Код", "copy_text": {"text": "x"}}
    post = {"rich_message": TEXT, "reply_markup": {"inline_keyboard": [[button]]}}

    def only_in(words):
        def edit(data):
            _field(data, "InlineKeyboardButton", "copy_text")["description"] += (
                f" The button may be used only in {words}."
            )

        return _spec_with(edit)

    assert _about(chats.warnings(only_in("sendRichMessageDraft"), post, []), "sendRichMessageDraft")
    for words in ("inline mode", "supergroups", "invoice messages"):
        assert not chats.warnings(only_in(words), post, []), words


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
    assert "Telegram отклоняет пост: «BUTTON_TYPE_INVALID» (замер: Bot API 10.4, 2026-11-16)" in button
    assert "ephemeral" not in button  # only the sentence that rules out this chat
    assert "https://t.me/<бот>/<приложение>?startapp=" in button
    assert _about(w, "rich_message.blocks[0].buttons[0].web_app: не работает в канале «Новости»")
    assert _about(w, "кнопка «Запрос» (ряд 1, №2), switch_inline_query_current_chat")
    [in_block] = _about(w, "rich_message.blocks[0].buttons[1].switch_inline_query_current_chat")
    assert "Not supported in channels" in in_block and "RICH_MESSAGE_BLOCK_INVALID" in in_block
    [switch] = _about(w, "кнопка «Поделиться» (ряд 1, №3), switch_inline_query")
    assert "can't use" in switch and "в спецификации этого нет" in switch
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
    # a chat nobody checked has no type: nothing is guessed; a chat that is not a chat id is left out, not the others
    assert not _validate(client, post, "@never_checked")["warnings"]
    assert _validate(client, post, "not a chat", "@news")["warnings"] == w


def test_measured_where_the_spec_says_nothing(client, tg):
    """2026-11-16: every switch_inline_query button but _current_chat under a channel post, all three in a «Кнопки»
    block, and callback_game in the channel and in the supergroup; the spec says nothing of it."""
    _checked(client, tg, "@news", CHANNEL)
    _checked(client, tg, "@forum", FORUM)
    block = [SWITCH, CHOSEN, INLINE]
    post = {
        "rich_message": {"blocks": [{"type": "buttons", "buttons": block}]},
        "reply_markup": {"inline_keyboard": [[CHOSEN, GAME]]},
    }
    w = _validate(client, post, "@news")["warnings"]
    [chosen] = _about(w, "кнопка «Выбрать чат» (ряд 1, №1), switch_inline_query_chosen_chat")
    assert "can't use switch_inline_query button in a channel chat" in chosen
    for i, b in enumerate(block):
        action = next(k for k in b if k != "text")
        [w_block] = _about(w, f"rich_message.blocks[0].buttons[{i}].{action}: не работает в канале «Новости»")
        assert "RICH_MESSAGE_BLOCK_INVALID" in w_block
    [game] = _about(w, "кнопка «Игра» (ряд 1, №2), callback_game")
    assert "BUTTON_TYPE_INVALID" in game and "в спецификации этого нет" in game
    # the supergroup took the switch buttons; callback_game it refused too
    forum = _validate(client, post, "@forum")["warnings"]
    assert [x.split(":")[0] for x in forum] == ["кнопка «Игра» (ряд 1, №2), callback_game"]
    html = '<p>x</p><tg-button type="switch_inline_query" query="x">Поделиться</tg-button>'
    [tag] = _validate(client, {"rich_message": {"html": html}}, "@news")["warnings"]
    assert '<tg-button type="switch_inline_query"> не работает в канале' in tag and "RICH_MESSAGE_BLOCK_INVALID" in tag


def test_reply_keyboards_and_the_thinking_block(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    _checked(client, tg, "@forum", FORUM)
    for markup, name in (
        ({"keyboard": [[{"text": "Да"}]], "resize_keyboard": True}, "ReplyKeyboardMarkup"),
        ({"force_reply": True}, "ForceReply"),
        ({"remove_keyboard": True}, "ReplyKeyboardRemove"),
    ):
        post = {"rich_message": TEXT, "reply_markup": markup}
        [w] = _validate(client, post, "@news")["warnings"]
        assert f"reply_markup: {name} не работает в канале" in w and "inline keyboard expected" in w
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
            assert where in w and "sendRichMessageDraft" in w and "RICH_MESSAGE_BLOCK_UNSUPPORTED" in w, w
    tagged = {
        "rich_message": {"html": '<p>x</p><tg-button type="web_app" url="https://example.com/app">App</tg-button>'}
    }
    [w] = _validate(client, tagged, "@news")["warnings"]
    assert '<tg-button type="web_app"> не работает в канале «Новости»' in w


def test_first_comment_and_rich_reply_have_their_own_place(client, tg):
    _checked(client, tg, "@news", CHANNEL)
    comment = {"blocks": [{"type": "buttons", "buttons": [WEB_APP]}]}
    login = {"text": "Войти", "login_url": {"url": "https://example.com/login"}}
    in_block = {"blocks": [{"type": "buttons", "buttons": [WEB_APP]}]}
    reply = {"rich_message": in_block, "reply_markup": {"inline_keyboard": [[login]]}}
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
    # and it is shown in the chat of the post: what that chat refuses holds for it too
    [shown] = _about(w, "ответ на кнопку «Жми»: reply.rich_message.blocks[0].buttons[0].web_app")
    assert "не работает в канале «Новости»" in shown and "BUTTON_TYPE_INVALID" in shown
    # with no chat known, only the ephemeral rules
    assert not _about(_validate(client, post, "@never_checked")["warnings"], "reply.rich_message")


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


def test_checked_targets_leave_out_only_what_is_wrong(client):
    assert publisher.checked_targets(["@news", "not a chat", " ", "-1001"]) == ["@news", -1001]
    assert publisher.checked_targets(["not a chat"]) == []
    assert publisher.checked_targets(None) == ["@test_channel"]  # the default channel, as a publish without chat_id
    d = store.save_draft(None, "Черновик", {"rich_message": TEXT}, None, None, ["@news"])
    assert publisher.checked_targets(None, d["id"]) == ["@news"]
    assert publisher.checked_targets(None, "no-such-draft") == []  # not the default channel


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


def test_spec_summary_carries_the_rules_and_their_words(client):
    s = client.get("/api/spec").json()
    assert s["chat_rules"]["places"]["channel"] == "в канале" and s["chat_rules"]["places"]["forum"] == "в форуме"
    rules = s["chat_rules"]["rules"]
    channel = rules["InlineKeyboardButton.web_app"]["refused"]["channel"]
    assert channel["why"] == "Available only in private chats between a user and the bot."
    assert channel["answer"] == "BUTTON_TYPE_INVALID"
    # the same words as a check: the editor shows them on a button's card
    assert channel["text"] == chats.said(
        spec.current, chats.rules(spec.current)["InlineKeyboardButton.web_app"].refused["channel"]
    )
    assert rules["InlineKeyboardButton.web_app"]["hint"]
    assert "ephemeral messages" in rules["InlineKeyboardButton.web_app"]["refused"]["ephemeral"]["why"]
    assert rules["InputRichBlockThinking"]["only_in"] == "sendRichMessageDraft"
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
