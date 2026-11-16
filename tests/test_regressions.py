"""Regressions found in the architecture review (rich-studio-arch/architecture-review.md, P0/P1)."""

import asyncio
import time
from urllib.parse import unquote

import httpx
import pytest

import settings
from services import scheduler, updates
from test_api import BLOCKS_POST
from test_callback_reply import REPLY


def test_preview_never_falls_back_to_the_channel(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "preview_chat", "")
    r = client.post("/api/preview-send", json={"post": BLOCKS_POST})
    assert r.status_code == 400 and "TG_PREVIEW_CHAT" in r.json()["detail"]
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_scheduled_post_stays_sent_when_pin_hits_network_error(client, tg, monkeypatch):
    import telegram

    real = telegram.call

    async def flaky(method, params=None, timeout=60):
        if method == "pinChatMessage":
            raise httpx.ReadTimeout("timeout")
        return await real(method, params, timeout)

    p = client.post("/api/schedule", json={"post": {**BLOCKS_POST, "pin": True}, "at": int(time.time())}).json()
    monkeypatch.setattr(telegram, "call", flaky)
    asyncio.run(scheduler.run_due_posts())
    rec = client.get("/api/posts/" + p["id"]).json()
    assert rec["status"] == "sent" and rec["message_id"]
    assert "закрепить" in rec["error"]


def test_network_error_in_one_channel_does_not_stop_the_rest(client, tg, monkeypatch):
    import telegram

    real = telegram.call
    n = {"sends": 0}

    async def flaky(method, params=None, timeout=60):
        if method == "sendRichMessage":
            n["sends"] += 1
            if n["sends"] == 2:
                raise httpx.ConnectError("down")
        return await real(method, params, timeout)

    monkeypatch.setattr(telegram, "call", flaky)
    r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_ids": ["@a", "@b", "@c"]})
    assert r.status_code == 200
    oks = [x["ok"] for x in r.json()["results"]]
    assert oks == [True, False, True]


def test_single_channel_network_error_is_a_clear_502(client, tg, monkeypatch):
    import telegram

    async def down(method, params=None, timeout=60):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(telegram, "call", down)
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 502 and "мог уйти" in r.json()["detail"]


def test_scheduler_reports_missing_chat_without_scaring(client, tg, monkeypatch):
    import store

    p = store.add_post("", BLOCKS_POST, None, "scheduled", scheduled_at=int(time.time()))
    monkeypatch.setattr(settings.get(), "default_chat", "")
    asyncio.run(scheduler.run_due_posts())
    rec = store.get_post(p["id"])
    assert rec["status"] == "failed" and "TG_DEFAULT_CHAT" in rec["error"] and "мог уйти" not in rec["error"]


def test_healthcheck_works_behind_api_key(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/config").status_code == 401


def test_favicon_is_not_a_404(client):
    assert client.get("/favicon.ico").status_code == 204


def test_poll_is_checked_like_a_poll_after_a_post(client, tg):
    """A scheduled poll Telegram would refuse is refused now, not at its time (prod review #8)."""
    import store

    poll = {"question": "Какой?", "options": [f"вариант {i}" for i in range(13)]}
    for body in ({"poll": poll}, {"poll": poll, "at": int(time.time()) + 3600}):
        r = client.post("/api/poll", json=body)
        assert r.status_code == 422 and "больше 12 вариантов" in str(r.json()["detail"])
    assert store.list_posts() == []
    assert not [c for c in tg.calls if c["method"] == "sendPoll"]


def test_error_inside_rich_text_array_names_the_missing_field():
    import spec

    spec.current.load_initial()

    def errors(text):
        post = {"blocks": [{"type": "paragraph", "text": text}]}
        return spec.validate_rich_message(spec.current, post)["errors"]

    # a link without url inside mixed text: the error names the field, not "expected a string"
    (err,) = errors(["Адрес ", {"type": "url", "text": "x"}])
    assert "text[1]" in err and "нет обязательного поля url" in err
    assert "ожидалась строка" not in err
    # the same element alone, and a plain wrong value, keep their messages
    assert "нет обязательного поля url" in errors({"type": "url", "text": "x"})[0]
    assert "ожидалась строка" in errors(5)[0]


# #171: a " in callback_data made a bad JSON path in SQLite (bump_click), and the press failed before it was answered
@pytest.mark.parametrize("ch", ['"', "\\", ".", "[", "$"])
def test_a_press_is_counted_and_answered_whatever_its_callback_data_holds(client, tg, ch):
    data = f"a{ch}b"
    kb = {"inline_keyboard": [[{"text": "Кнопка", "callback_data": data}]]}
    post = {**BLOCKS_POST, "reply_markup": kb, "callbacks": {data: {"text": "Принято", "reply": REPLY}}}
    sent = client.post("/api/send", json={"post": post}).json()
    refused = {"ok": False, "error_code": 400, "description": "Bad Request: query is too old"}
    tg.queue["sendRichMessage"] = [refused, refused]
    for cq in ("cq1", "cq2"):
        click = {
            "id": cq,
            "data": data,
            "message": {"message_id": sent["message"]["message_id"], "chat": {"id": -1001}},
        }
        asyncio.run(updates.handle_update({"update_id": 1, "callback_query": click}))
        assert tg.last("answerCallbackQuery")["body"] == {"callback_query_id": cq, "text": "Принято"}
    p = client.get(f"/api/posts/{sent['post']['id']}").json()
    assert p["clicks"] == {data: 2}
    assert p["reply_errors"][data]["n"] == 2 and p["reply_errors"][data]["error"] == "Bad Request: query is too old"


# #174: upload:<name> in a link went to Telegram as it is (the studio puts the file in only in a file field), and
# /api/validate said nothing
@pytest.mark.parametrize(
    "rich_message",
    [
        {"blocks": [{"type": "buttons", "buttons": [{"text": "Файл", "url": "upload:a.jpg"}]}]},
        {"html": '<p><a href="upload:a.jpg">файл</a></p>'},
    ],
)
def test_upload_in_a_link_is_an_error_and_does_not_go_out(client, tg, rich_message):
    r = client.post("/api/validate", json={"rich_message": rich_message}).json()
    assert not r["ok"] and "поле файла" in str(r["errors"]), r
    r = client.post("/api/send", json={"post": {"rich_message": rich_message}, "chat_id": "@chan"})
    assert r.status_code == 422, r.json()
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


# #182: a field outside models.Post was dropped by pydantic before the check, so it got no warning at all
def _unused(warnings: list[str], name: str) -> list[str]:
    return [w for w in warnings if f"«{name}»" in w and "верхнем уровне поста" in w]


def test_the_fields_the_check_knows_are_the_fields_of_the_post():
    import models
    from services import publisher

    # a new field of models.Post that the check does not know would be called unused in every post that has it
    assert set(models.Post.model_fields) == publisher.POST_FIELDS


def test_a_top_level_field_the_post_does_not_have_is_a_warning(client, tg):
    r = client.post("/api/validate", json={**BLOCKS_POST, "foo_bar": 1})
    assert r.status_code == 200 and r.json()["ok"] is True
    (w,) = _unused(r.json()["warnings"], "foo_bar")
    assert "options" in w  # where Bot API parameters go
    assert not tg.calls


def test_a_top_level_field_stays_in_the_post_and_never_reaches_bot_api(client, tg):
    post = {**BLOCKS_POST, "foo_bar": 1, "disable_notification": True}
    r = client.post("/api/send", json={"post": post})
    assert r.status_code == 200, r.text
    warnings = r.json()["warnings"]
    assert _unused(warnings, "foo_bar") and _unused(warnings, "disable_notification")
    body = tg.last("sendRichMessage")["body"]
    assert not {"foo_bar", "disable_notification"} & set(body)
    # the record keeps the post as it came (AGENTS.md rule 1)
    assert client.get("/api/posts/" + r.json()["post"]["id"]).json()["payload"]["foo_bar"] == 1
    # a dry run of a schedule shows it before anything is queued
    at = int(time.time()) + 3600
    r = client.post("/api/schedule", json={"post": post, "at": at, "dry_run": True})
    assert r.status_code == 200 and _unused(r.json()["warnings"], "foo_bar")
    assert "foo_bar" not in r.json()["requests"][0]["params"]


def test_mcp_tools_warn_about_a_top_level_field_and_the_draft_keeps_it(client, tg):
    import store
    from test_mcp import call

    _, v, _ = call(client, "validate_post", post={**BLOCKS_POST, "foo_bar": 1})
    assert v["ok"] is True and _unused(v["warnings"], "foo_bar")
    _, d, _ = call(client, "save_draft", post={**BLOCKS_POST, "foo_bar": 1}, title="Черновик")
    assert _unused(d["validation"]["warnings"], "foo_bar")
    assert store.get_draft(d["id"])["payload"]["foo_bar"] == 1
    _, shown, _ = call(client, "preview_post", draft_id=d["id"])
    assert _unused(shown["validation"]["warnings"], "foo_bar")
    _, sent, _ = call(client, "publish_post", draft_id=d["id"], version=d["version"])
    assert _unused(sent["warnings"], "foo_bar")
    assert "foo_bar" not in tg.last("sendRichMessage")["body"]


# Where a post goes is the studio's to decide, as with chat_id in options (RESERVED_PARAMS): a chat inside the post
# is not an unknown field but a wrong address, the post would go to the default channel. An error, not a warning.
def _chat_errors(errors: list[str], name: str) -> list[str]:
    return [e for e in errors if f"«{name}»" in e and "верхнем уровне поста" in e]


def test_chat_ids_in_the_body_of_validate_is_an_error_that_points_to_the_query_parameter(client, tg):
    r = client.post("/api/validate", json={**BLOCKS_POST, "chat_ids": ["@simple_test_general"]})
    assert r.status_code == 200 and r.json()["ok"] is False
    (e,) = _chat_errors(r.json()["errors"], "chat_ids")
    assert "?chat_ids=" in e and "query" in e
    assert not [w for w in r.json()["warnings"] if "chat_ids" in w]


@pytest.mark.parametrize("field", ["chat_id", "chat_ids"])
def test_a_chat_inside_the_post_is_refused_before_anything_goes_out(client, tg, field):
    import store

    post = {**BLOCKS_POST, field: "@other" if field == "chat_id" else ["@other"]}
    # the hint names where this request takes the chats, never a parameter it does not have
    for path, body, hint in (
        ("/api/send", {"post": post}, "chat_id или chat_ids рядом с post"),
        ("/api/send", {"post": post, "dry_run": True}, "chat_id или chat_ids рядом с post"),
        ("/api/schedule", {"post": post, "at": int(time.time()) + 3600}, "chat_id или chat_ids рядом с post"),
        ("/api/preview-send", {"post": post}, "предпросмотр уходит в TG_PREVIEW_CHAT"),
        ("/api/edit", {"post": post, "message_id": 1}, "у /api/edit — chat_id рядом с post"),
    ):
        r = client.post(path, json=body)
        assert r.status_code == 400, (path, r.text)
        assert f"«{field}»" in r.json()["detail"] and hint in r.json()["detail"], (path, r.text)
    assert not [c for c in tg.calls if c["method"] in ("sendRichMessage", "editMessageText")]
    assert store.list_posts() == []


def test_an_edit_of_a_sent_post_with_a_chat_inside_says_the_channel_is_the_posts(client, tg):
    sent = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]
    r = client.post(f"/api/posts/{sent['id']}/edit", json={**BLOCKS_POST, "chat_ids": ["@other"]})
    assert r.status_code == 400
    assert "«chat_ids»" in r.json()["detail"] and "канал опубликованного поста" in r.json()["detail"]
    assert "рядом с post" not in r.json()["detail"]  # this route has no chat_id or chat_ids to pass
    assert not [c for c in tg.calls if c["method"] == "editMessageText"]


def test_mcp_a_chat_inside_the_post_is_an_error_and_the_draft_is_still_saved(client, tg):
    import store
    from test_mcp import call

    post = {**BLOCKS_POST, "chat_ids": ["@x"]}
    _, v, _ = call(client, "validate_post", post=post)
    # these two tools take chat_ids only: the hint names that argument, not a chat_id they do not have
    (e,) = _chat_errors(v["errors"], "chat_ids")
    assert v["ok"] is False and "(chat_ids рядом с post)" in e and "chat_id или" not in e
    # saving is not refused, as with chat_id in options: the agent sees the error and saves again
    _, d, _ = call(client, "save_draft", post=post, title="Не туда")
    (e,) = _chat_errors(d["validation"]["errors"], "chat_ids")
    assert d["validation"]["ok"] is False and "(chat_ids рядом с post)" in e and "chat_id или" not in e
    assert store.get_draft(d["id"])["payload"]["chat_ids"] == ["@x"]
    for tool, extra in (("publish_post", {}), ("schedule_post", {"at": "2099-01-01T09:00:00+03:00"})):
        res, _, text = call(client, tool, draft_id=d["id"], version=d["version"], **extra)
        assert res["isError"] and "«chat_ids»" in text, (tool, text)
    _, shown, _ = call(client, "preview_post", draft_id=d["id"])  # takes no chats: it says where they are set
    assert "chat_ids в save_draft" in _chat_errors(shown["validation"]["errors"], "chat_ids")[0]
    res, _, text = call(client, "send_preview_to_me", post={**BLOCKS_POST, "chat_id": "@x"})
    assert res["isError"] and "«chat_id»" in text and "TG_PREVIEW_CHAT" in text and "рядом с post" not in text
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]
    assert store.list_posts() == []


def test_a_draft_from_a_template_with_a_chat_inside_says_where_the_chats_go(client, tg):
    from test_mcp import call

    _, t, _ = call(client, "save_post_template", title="Рубрика", post={**BLOCKS_POST, "chat_ids": ["@x"]}, fields=[])
    _, d, _ = call(client, "draft_from_template", template_id=t["id"], version=t["version"], values={})
    # the tool takes neither post nor chats: the hint names the tools that do
    (e,) = _chat_errors(d["validation"]["errors"], "chat_ids")
    assert "chat_ids в save_draft" in e and "publish_post" in e and "рядом с post" not in e


def test_a_scheduled_post_with_a_chat_inside_fails_with_the_reason(client, tg):
    import store

    # queued before a chat inside the post was refused: the scheduler refuses it like chat_id in options
    (rec,) = store.add_scheduled(["@test_channel"], {**BLOCKS_POST, "chat_ids": ["@other"]}, int(time.time()))
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "failed" and "«chat_ids»" in p["error"]
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_an_edit_of_a_sent_post_keeps_the_unknown_fields_of_the_new_version(client, tg):
    kb = {"inline_keyboard": [[{"text": "Да", "callback_data": "yes"}]]}
    first = {**BLOCKS_POST, "reply_markup": kb, "callbacks": {"yes": "Спасибо"}, "pin": True, "foo_bar": 1}
    sent = client.post("/api/send", json={"post": first}).json()["post"]
    new_text = {"blocks": [{"type": "paragraph", "text": "Новый текст"}]}
    r = client.post(f"/api/posts/{sent['id']}/edit", json={"rich_message": new_text, "reply_markup": kb, "baz": 2})
    assert r.status_code == 200, r.text
    payload = client.get("/api/posts/" + sent["id"]).json()["payload"]
    # the new version's own fields as it came, like a replaced scheduled post; the old version's are gone
    assert payload["baz"] == 2 and "foo_bar" not in payload
    # what the studio reads stays as after_edit always kept it: done at publish time, or not brought by the edit
    assert payload["rich_message"] == new_text and payload["callbacks"] == {"yes": "Спасибо"} and payload["pin"]
    assert "baz" not in tg.last("editMessageText")["body"]


def test_the_notes_of_a_draft_are_not_an_unknown_field_and_stay_in_the_draft(client, tg):
    from test_mcp import call

    notes = {"source": {"chat": "@elsewhere"}, "buffers": {"html": "<p>старое</p>"}, "format": "blocks"}
    d = client.post("/api/drafts", json={"title": "Из редактора", "post": {**BLOCKS_POST, "_studio": notes}}).json()
    _, shown, _ = call(client, "preview_post", draft_id=d["id"])
    assert not [w for w in shown["validation"]["warnings"] if "_studio" in w]
    assert "_studio" not in shown["post"]
    _, sent, _ = call(client, "publish_post", draft_id=d["id"], version=d["version"])
    assert not [w for w in sent["warnings"] if "_studio" in w]
    assert "_studio" not in tg.last("sendRichMessage")["body"]
    # the editor's notes stay with the draft, not with the record of the sent post
    assert "_studio" not in client.get("/api/posts/" + sent["post"]["id"]).json()["payload"]
    assert client.get("/api/drafts/" + d["id"]).json()["payload"]["_studio"] == notes


def test_a_get_of_check_neither_asks_telegram_nor_remembers_the_channel(client, tg):
    """<img src="/api/check?chat_id=@x"> in a preview, or on any page while the studio is open, was a GET with the
    editor's cookies that saved someone else's channel in «Каналы» (issue #201): a check remembers the channel, so it
    is a POST, and a GET changes nothing."""
    from test_access import token

    r = client.get("/api/check?chat_id=@stranger")
    assert r.status_code == 405
    assert not tg.calls
    assert "@stranger" not in {c["chat_id"] for c in client.get("/api/channels").json()}
    r = client.post("/api/check?chat_id=@other")
    assert r.status_code == 200 and r.json()["can_post"], r.text
    assert "@other" in {c["chat_id"] for c in client.get("/api/channels").json()}
    # it writes to the studio, so a read token may not, as with POST /api/channels
    read, draft = token("r", "read"), token("d", "draft")
    assert client.post("/api/check?chat_id=@third", headers=read).status_code == 403
    assert client.post("/api/check?chat_id=@third", headers=draft).status_code == 200


# ---------- #204: upload:<name> is a file of the uploads, never a path or a URL of its own ----------
# a path, not a name: the studio would read outside the uploads, and the list of posts would point the browser at
# another route of the studio (`/media/../api/export` is GET /api/export with the editor's cookie)
NOT_A_NAME = ["../api/export", "a/../../x.png", "%2e%2e/api/export", "/etc/passwd", "a\\..\\x.png", "a\x00b.png", ".."]
LATER = int(time.time()) + 3600


def _photo(ref: str) -> dict:
    return {"rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": ref}}]}}


def _nothing_stored_or_sent(tg) -> None:
    import store

    assert store.list_posts() == []
    assert not [c for c in tg.calls if c["method"].startswith("send")]


@pytest.mark.parametrize("name", NOT_A_NAME)
def test_an_upload_that_is_no_file_name_is_refused_by_send_and_schedule_alike(client, tg, name):
    r = client.post("/api/send", json={"post": _photo("upload:" + name)})
    assert r.status_code == 400, r.text
    r = client.post("/api/schedule", json={"post": _photo("upload:" + name), "at": LATER})
    assert r.status_code == 400 and "upload:" in r.json()["detail"], r.text
    _nothing_stored_or_sent(tg)


def test_a_schedule_with_a_missing_upload_is_refused_like_a_send(client, tg):
    r = client.post("/api/schedule", json={"post": _photo("upload:nope.png"), "at": LATER})
    assert r.status_code == 400 and "nope.png" in r.json()["detail"] and "не найден" in r.json()["detail"], r.text
    _nothing_stored_or_sent(tg)
    client.post("/api/media", files={"file": ("nope.png", b"\x89PNG", "image/png")})
    assert client.post("/api/schedule", json={"post": _photo("upload:nope.png"), "at": LATER}).status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"comment": _photo("upload:../api/export")["rich_message"]},
        {"poll_after": {"question": "?", "options": ["a", "b"], "media": {"type": "photo", "media": "upload:no.png"}}},
        {
            "reply_markup": {"inline_keyboard": [[{"text": "Да", "callback_data": "yes"}]]},
            "callbacks": {"yes": {"reply": _photo("upload:../api/export")}},
        },
    ],
    ids=["comment", "poll_after", "callbacks.reply"],
)
def test_what_goes_out_after_a_scheduled_post_has_its_uploads_checked_too(client, tg, extra):
    r = client.post("/api/schedule", json={"post": {**BLOCKS_POST, **extra}, "at": LATER})
    assert r.status_code == 400, r.text
    _nothing_stored_or_sent(tg)


def test_a_poll_live_photo_or_paid_media_waits_only_with_files_of_the_uploads(client, tg):
    bad = {"type": "photo", "media": "upload:../api/export"}
    r = client.post("/api/poll", json={"poll": {"question": "?", "options": ["a", "b"], "media": bad}, "at": LATER})
    assert r.status_code == 400, r.text
    live = {"live_photo": "upload:clip.mp4", "photo": "upload:../api/export"}
    r = client.post("/api/media-post", json={"kind": "live_photo", "params": live, "at": LATER})
    assert r.status_code == 400, r.text
    r = client.post(
        "/api/media-post", json={"kind": "paid_media", "params": {"star_count": 5, "media": [bad]}, "at": LATER}
    )
    assert r.status_code == 400, r.text
    _nothing_stored_or_sent(tg)


def test_a_waiting_post_is_not_replaced_with_an_upload_that_is_no_file(client, tg):
    client.post("/api/media", files={"file": ("cat.png", b"\x89PNG", "image/png")})
    rec = client.post("/api/schedule", json={"post": _photo("upload:cat.png"), "at": LATER}).json()
    r = client.post(f"/api/posts/{rec['id']}/edit", json=_photo("upload:../api/export"))
    assert r.status_code == 400, r.text
    r = client.post(f"/api/posts/{rec['id']}/edit", json=_photo("upload:gone.png"))
    assert r.status_code == 400 and "не найден" in r.json()["detail"], r.text
    p = client.get("/api/posts/" + rec["id"]).json()
    assert p["payload"]["rich_message"] == _photo("upload:cat.png")["rich_message"] and p["version"] == rec["version"]


@pytest.mark.parametrize("sent", [False, True], ids=["scheduled", "sent"])
def test_buttons_replaced_with_a_reply_whose_file_is_no_upload_are_refused(client, tg, sent):
    """«Кнопки» (POST /api/posts/{id}/buttons) checks the files of the rich replies as a send does (#213): stored,
    such a reply failed only at a press, when find refused its name (callback.reply_failed)."""
    like = {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]}
    client.post("/api/media", files={"file": ("cat.png", b"\x89PNG", "image/png")})
    if sent:
        pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    else:
        pid = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": LATER}).json()["id"]
    before = client.get("/api/posts/" + pid).json()
    start = len(tg.calls)

    def buttons(ref: str):
        body = {"reply_markup": like, "callbacks": {"like": {"reply": _photo(ref)}}}
        return client.post(f"/api/posts/{pid}/buttons", json=body)

    for ref, why in (("upload:../api/export", "это путь"), ("upload:gone.png", "не найден")):
        r = buttons(ref)
        assert r.status_code == 400 and why in r.json()["detail"], r.text
    assert len(tg.calls) == start
    p = client.get("/api/posts/" + pid).json()
    assert p["payload"] == before["payload"] and p["version"] == before["version"]
    r = buttons("upload:cat.png")
    assert r.status_code == 200 and r.json()["payload"]["callbacks"]["like"]["reply"] == _photo("upload:cat.png"), (
        r.text
    )


@pytest.mark.parametrize("name", ["../api/export", "a\x00b.png", "x" * 300 + ".png"], ids=["path", "nul", "long"])
def test_a_scheduled_post_stored_with_such_a_name_fails_with_the_reason_and_is_not_resent(client, tg, name):
    """Queued before the check (or by an older studio): refused before Telegram is asked, never «may have gone»."""
    import store

    (rec,) = store.add_scheduled(["@test_channel"], _photo("upload:" + name), int(time.time()))
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "failed" and not p["outcome_unknown"] and not p["retry_at"], p
    assert "upload:" in p["error"] or "не найден" in p["error"], p["error"]
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_a_send_with_a_nul_or_an_overlong_upload_name_is_a_400_not_may_have_gone(client, tg):
    for name in ("a\x00b.png", "x" * 300 + ".png"):
        r = client.post("/api/send", json={"post": _photo("upload:" + name)})
        assert r.status_code == 400, r.text
    _nothing_stored_or_sent(tg)
    assert client.get("/media/" + "x" * 300 + ".png").status_code == 404


def test_the_list_of_posts_never_points_the_browser_outside_the_uploads(client, tg):
    """Old records may hold any name: a thumbnail is only a file of the uploads, and its name is encoded."""
    import store
    from services import media

    for name in ("../api/export", "a/../../api/export", "%2e%2e", "..", "missing.png"):
        store.add_post("@test_channel", _photo("upload:" + name), status="sent", message_id=1)
    (media.media_dir() / "%2e%2e").write_bytes(b"\x89PNG")  # a real file of that name: encoded, it stays itself
    (media.media_dir() / "кот #1.png").write_bytes(b"\x89PNG")
    store.add_post("@test_channel", _photo("upload:кот #1.png"), status="sent", message_id=2)
    thumbs = [p["thumb"] for p in client.get("/api/posts").json()]
    assert thumbs == ["/media/%D0%BA%D0%BE%D1%82%20%231.png", None, None, "/media/%252e%252e", None, None], thumbs
    assert client.get(thumbs[0]).content == b"\x89PNG"
    # the server decodes a path once (TestClient decodes twice, so this one is checked by hand)
    assert unquote(thumbs[3].removeprefix("/media/")) == "%2e%2e"
    names = {f["name"]: f["url"] for f in client.get("/api/media").json()}
    assert names == {"%2e%2e": "/media/%252e%252e", "кот #1.png": thumbs[0]}


def test_a_file_put_in_the_uploads_by_hand_under_a_name_no_post_can_use_is_still_cleaned_up(client):
    from services import media

    (media.media_dir() / "a\\b\n.png").write_bytes(b"\x89PNG")
    assert client.post("/api/media/cleanup").json()["deleted"] == ["a\\b\n.png"]
    assert not list(media.media_dir().iterdir())


def test_a_file_with_a_name_that_is_not_utf8_does_not_break_health_or_the_cleanup(client):
    """After an unzip or by hand: Python keeps such a name with surrogates, and its URL is the bytes as they are."""
    import os

    from services import health, media

    name = os.fsdecode(b"\xff-bad.png")
    (media.media_dir() / name).write_bytes(b"\x89PNG")
    assert media.describe(name)["url"] == "/media/%FF-bad.png"
    assert next(c for c in health.report()["checks"] if c["name"] == "storage")["status"] == "ok"
    assert media.delete_unused(older_than=time.time() - 3600) == []  # as the hourly one: too fresh yet
    r = client.post("/api/media/cleanup")
    assert r.status_code == 200 and r.json()["deleted"] == ["�-bad.png"], r.text
    assert not list(media.media_dir().iterdir())


def test_a_symlink_loop_in_the_uploads_does_not_break_the_list_health_or_the_cleanup(client):
    """Put there by hand (ln -s loop.png loop.png): its stat fails with ELOOP, not FileNotFoundError. Path.is_file()
    used to swallow that, so the file was left out; after #204 the list read stat itself and answered 500."""
    import os

    from services import health, media

    os.symlink("loop.png", media.media_dir() / "loop.png")
    (media.media_dir() / "ok.png").write_bytes(b"\x89PNG")
    r = client.get("/api/media")
    assert r.status_code == 200 and [f["name"] for f in r.json()] == ["ok.png"], r.text
    assert next(c for c in health.report()["checks"] if c["name"] == "storage")["status"] == "ok"
    assert client.post("/api/media/cleanup").status_code == 200


def test_deleting_a_symlink_in_the_uploads_removes_the_link_never_the_file_it_points_to(client):
    """Put there by hand: DELETE /api/media/<name> takes the entry of the folder, as the cleanup does (#213). It used
    to resolve the name (find), so the file the link points to went and the link stayed, dangling."""
    import os

    from services import media

    folder = media.media_dir()
    (folder / "real.png").write_bytes(b"\x89PNG")
    os.symlink("real.png", folder / "link.png")
    outside = folder.parent / "outside.png"  # beside the uploads: never one of their files
    outside.write_bytes(b"\x89PNG")
    os.symlink(outside, folder / "out.png")
    os.symlink("loop.png", folder / "loop.png")
    for name in ("link.png", "out.png", "loop.png"):
        r = client.delete("/api/media/" + name)
        assert r.status_code == 200 and not os.path.lexists(folder / name), (name, r.text)
    assert sorted(os.listdir(folder)) == ["real.png"] and outside.read_bytes() == b"\x89PNG"
    # still only a name of the folder; ".." itself httpx turns into DELETE /api, so it goes as %2e%2e
    for name in ("%2e%2e", ".hidden", "a\\b"):
        assert client.delete("/api/media/" + name).status_code == 400, name


def test_a_file_with_a_name_that_is_not_utf8_is_listed_opens_and_is_deleted_by_its_url(client):
    """The list shows the name with «�» (JSON has no such bytes) and gives its URL with the bytes as they are: the
    picture opens by it, and DELETE /api/media/ takes the same segment (the editor's ✕ sends it)."""
    import os

    from services import media

    (media.media_dir() / os.fsdecode(b"\xff-bad.png")).write_bytes(b"\x89PNG")
    (media.media_dir() / os.fsdecode(b"\xfe-notes.txt")).write_bytes(b"text")  # a download, with its name
    r = client.get("/api/media")
    assert r.status_code == 200, r.text
    f, txt = (next(x for x in r.json() if x["kind"] == k) for k in ("photo", "document"))
    assert f["name"] == "�-bad.png" and f["url"] == "/media/%FF-bad.png" and f["used"] is False, f
    assert client.get(f["url"]).content == b"\x89PNG"
    assert txt["url"] == "/media/%FE-notes.txt" and client.get(txt["url"]).content == b"text"
    for x in (f, txt):
        assert client.delete("/api/media/" + x["url"].removeprefix("/media/")).status_code == 200
    assert not list(media.media_dir().iterdir())


def test_a_file_deleted_while_the_list_of_uploads_is_read_does_not_fail_it(client, monkeypatch):
    """The editor reads the list again right after its own DELETE: a file gone between the folder's listing and its
    size is no 500 (found by e2e/54-delete-upload-by-its-url)."""
    from pathlib import Path

    from services import media

    for name in ("stays.png", "gone.png"):
        (media.media_dir() / name).write_bytes(b"\x89PNG")
    real, seen = Path.stat, []

    def deleted_after_its_first_look(self, *a, **kw):
        if self.name == "gone.png":
            seen.append(1)
            if len(seen) > 1:
                raise FileNotFoundError(2, "No such file or directory", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", deleted_after_its_first_look)
    r = client.get("/api/media")
    assert r.status_code == 200 and "stays.png" in [f["name"] for f in r.json()], r.text
    assert client.post("/api/media/cleanup").status_code == 200


def test_a_text_edit_of_a_sent_post_is_not_refused_for_a_file_of_what_already_went(client, tg):
    """The editor sends poll_after and comment with every edit; the poll went at publish time and editMessageText
    takes only the text and the buttons, so a file of the poll deleted since then is no reason to refuse."""
    client.post("/api/media", files={"file": ("pollpic.png", b"\x89PNG", "image/png")})
    poll = {"question": "?", "options": ["a", "b"], "media": {"type": "photo", "media": "upload:pollpic.png"}}
    comment = _photo("upload:pollpic.png")["rich_message"]
    sent = client.post("/api/send", json={"post": {**BLOCKS_POST, "poll_after": poll, "comment": comment}}).json()
    pid = sent["post"]["id"]
    assert client.delete("/api/media/pollpic.png").status_code == 200  # used_by counts only what still waits
    new_text = {"blocks": [{"type": "paragraph", "text": "Новый текст"}]}
    r = client.post(f"/api/posts/{pid}/edit", json={"rich_message": new_text, "poll_after": poll, "comment": comment})
    assert r.status_code == 200, r.text  # TG_UPDATES is off in tests: the comment was skipped, it never goes
    r = client.post(
        "/api/edit",
        json={"post": {"rich_message": new_text, "poll_after": poll}, "message_id": sent["post"]["message_id"]},
    )
    assert r.status_code == 200, r.text
    # what goes out now is checked as before
    r = client.post(f"/api/posts/{pid}/edit", json=_photo("upload:gone.png"))
    assert r.status_code == 400 and "gone.png" in r.json()["detail"], r.text
    r = client.post("/api/edit", json={"post": _photo("upload:gone.png"), "message_id": sent["post"]["message_id"]})
    assert r.status_code == 400 and "gone.png" in r.json()["detail"], r.text


def test_an_edit_checks_the_file_of_a_first_comment_that_still_waits_for_its_forward(client, tg):
    import store

    sent = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]
    store.update_post(sent["id"], comment_status="waiting")
    edit = {**BLOCKS_POST, "comment": _photo("upload:gone.png")["rich_message"]}
    r = client.post(f"/api/posts/{sent['id']}/edit", json=edit)
    assert r.status_code == 400 and "gone.png" in r.json()["detail"], r.text
    assert not [c for c in tg.calls if c["method"] == "editMessageText"]


def test_a_draft_or_a_template_may_name_a_file_not_uploaded_yet_but_never_a_path(client, tg):
    from test_mcp import call

    d = client.post("/api/drafts", json={"title": "Позже", "post": _photo("upload:later.png")})
    assert d.status_code == 200, d.text
    r = client.post("/api/drafts", json={"title": "Путь", "post": _photo("upload:../api/export")})
    assert r.status_code == 400 and "upload:" in r.json()["detail"], r.text
    r = client.put(f"/api/drafts/{d.json()['id']}", json={"title": "x", "post": _photo("upload:a/b.png")})
    assert r.status_code == 400, r.text
    res, _, text = call(client, "save_draft", post=_photo("upload:../api/export"))
    assert res.get("isError") and "upload:" in text, text
    # a template: in its post, as the default of a field and as the value it is filled with
    r = client.post("/api/templates", json={"title": "T", "post": _photo("upload:../x.png")})
    assert r.status_code == 400, r.text
    field = {"key": "pic", "label": "Картинка", "type": "url", "default": "upload:../x.png"}
    r = client.post("/api/templates", json={"title": "T", "post": _photo("{{pic}}"), "fields": [field]})
    assert r.status_code == 422, r.text
    field["default"] = "upload:a.png"
    t = client.post("/api/templates", json={"title": "T", "post": _photo("{{pic}}"), "fields": [field]}).json()
    fill = f"/api/templates/{t['id']}/draft"
    r = client.post(fill, json={"version": t["version"], "values": {"pic": "upload:../x.png"}})
    assert r.status_code == 422, r.text
    assert client.post(fill, json={"version": t["version"], "values": {"pic": "upload:b.png"}}).status_code == 200


# ---------- #212: a publish to several channels that went to some of them ----------
# how the send to the second channel (@b) fails, what its error says, whether the post may be there all the same
SECOND_FAILS = [
    ("refused", "chat not found", False),  # Telegram's own 400
    ("not a member", "bot is not a member", False),  # a channel the bot cannot post to
    ("file deleted", "partial.png", False),  # the studio refuses it: the file went between the check and this send
    ("network", "мог уйти", True),  # Telegram took it, the answer was lost
]


def _second_channel_fails(monkeypatch, tg, how: str) -> None:
    """Every channel but @b takes the post; the send to @b fails `how` (SECOND_FAILS)."""
    import telegram

    real = telegram.call

    async def second_fails(method, params=None, *a, **kw):
        if method != "sendRichMessage" or (params or {}).get("chat_id") != "@b":
            return await real(method, params, *a, **kw)
        if how == "file deleted":
            (settings.get().media_dir / "partial.png").unlink(missing_ok=True)
        elif how != "network":
            code, text = (
                (400, "Bad Request: chat not found") if how == "refused" else (403, "Forbidden: bot is not a member")
            )
            tg.queue["sendRichMessage"] = [{"ok": False, "error_code": code, "description": text}]
        res = await real(method, params, *a, **kw)
        if how == "network":
            raise httpx.ReadTimeout("timed out")
        return res

    monkeypatch.setattr(telegram, "call", second_fails)


def _sends_to(tg, chat: str) -> int:
    return len([c for c in tg.calls if c["method"] == "sendRichMessage" and c["body"].get("chat_id") == chat])


def _partial_post(client) -> dict:
    assert client.post("/api/media", files={"file": ("partial.png", b"\x89PNG", "image/png")}).status_code == 200
    return _photo("upload:partial.png")


@pytest.mark.parametrize("how, why, unknown", SECOND_FAILS)
def test_a_failed_second_channel_keeps_the_first_sent_and_a_retry_with_the_key_sends_nothing(
    client, tg, monkeypatch, how, why, unknown
):
    body, h = {"post": _partial_post(client), "chat_ids": ["@a", "@b"]}, {"Idempotency-Key": f"partial-{how}"}
    _second_channel_fails(monkeypatch, tg, how)
    first = client.post("/api/send", json=body, headers=h)
    assert first.status_code == 200, first.text
    went, missed = first.json()["results"]
    # where it went, with its message_id, and where it did not and why
    assert went["ok"] and went["chat_id"] == "@a" and went["message"]["message_id"] == went["post"]["message_id"]
    assert not missed["ok"] and missed["chat_id"] == "@b" and why in missed["error"], missed
    rows = {p["chat_id"]: p for p in client.get("/api/posts").json()}
    assert rows["@a"]["status"] == "sent" and rows["@a"]["message_id"] == went["message"]["message_id"]
    assert rows["@b"]["status"] == "failed" and why in rows["@b"]["error"], rows["@b"]
    assert rows["@b"]["outcome_unknown"] is unknown
    sent = len([c for c in tg.calls if c["method"] == "sendRichMessage"])
    _partial_post(client)  # the file is back: a retry that ran again would reach Telegram
    again = client.post("/api/send", json=body, headers=h)
    assert again.status_code == 200 and again.json() == first.json(), again.text
    assert len([c for c in tg.calls if c["method"] == "sendRichMessage"]) == sent and _sends_to(tg, "@a") == 1


def test_publish_post_to_several_channels_answers_a_retry_with_its_key_from_the_first_result(client, tg, monkeypatch):
    from test_mcp import call

    post = _partial_post(client)
    _second_channel_fails(monkeypatch, tg, "file deleted")
    _, first, text = call(client, "publish_post", post=post, chat_ids=["@a", "@b"], idempotency_key="partial-mcp")
    assert first is not None, text
    assert [(r["chat_id"], r["ok"]) for r in first["results"]] == [("@a", True), ("@b", False)]
    _, again, text = call(client, "publish_post", post=post, chat_ids=["@a", "@b"], idempotency_key="partial-mcp")
    assert again == first, text
    assert _sends_to(tg, "@a") == 1


def test_a_studio_refusal_after_a_channel_that_may_have_got_the_post_keeps_the_key(client, tg, monkeypatch):
    """Nothing confirmed, but @a may have the post: not a 400 that frees the key for a resend to @a."""
    import telegram

    real = telegram.call

    async def lost_then_refused(method, params=None, *a, **kw):
        res = await real(method, params, *a, **kw)
        if method == "sendRichMessage" and params["chat_id"] == "@a":
            (settings.get().media_dir / "partial.png").unlink()
            raise httpx.ReadTimeout("timed out")
        return res

    body, h = {"post": _partial_post(client), "chat_ids": ["@a", "@b"]}, {"Idempotency-Key": "partial-unknown"}
    monkeypatch.setattr(telegram, "call", lost_then_refused)
    first = client.post("/api/send", json=body, headers=h)
    assert first.status_code == 502 and "могло уйти" in first.json()["detail"]["message"], first.text
    assert any("partial.png" in e for e in first.json()["detail"]["errors"]), first.text
    rows = {p["chat_id"]: p for p in client.get("/api/posts").json()}
    assert rows["@a"]["outcome_unknown"] is True and rows["@b"]["status"] == "failed"
    again = client.post("/api/send", json=body, headers=h)
    assert again.status_code == 502 and "новым ключом" in again.json()["detail"]["message"], again.text
    assert _sends_to(tg, "@a") == 1


@pytest.mark.parametrize("how, why, unknown", SECOND_FAILS)
def test_a_scheduled_post_to_several_channels_keeps_the_one_that_went_and_never_resends(
    client, tg, monkeypatch, how, why, unknown
):
    body = {"post": _partial_post(client), "chat_ids": ["@a", "@b"], "at": int(time.time())}
    assert client.post("/api/schedule", json=body).status_code == 200
    _second_channel_fails(monkeypatch, tg, how)
    asyncio.run(scheduler.run_due_posts())
    rows = {p["chat_id"]: p for p in client.get("/api/posts").json()}
    assert rows["@a"]["status"] == "sent" and rows["@a"]["message_id"], rows["@a"]
    assert rows["@b"]["status"] == "failed" and why in rows["@b"]["error"], rows["@b"]
    assert rows["@b"]["outcome_unknown"] is unknown
    asyncio.run(scheduler.run_due_posts())
    reached = 0 if how == "file deleted" else 1  # a refused file never reaches Telegram
    assert _sends_to(tg, "@a") == 1 and _sends_to(tg, "@b") == reached


def test_the_check_of_a_draft_reports_a_poll_after_the_post_that_would_be_refused(client, tg):
    # the check (/api/validate, validate_post, save_draft, the editor) said ok, and only the publish refused it (422)
    post = {**BLOCKS_POST, "poll_after": {"question": "?", "options": ["один"]}}
    from services import publisher

    checked = client.post("/api/validate", json=post).json()
    saved = publisher.check(post)  # what save_draft and fill_post_template return as validation
    for r in (checked, saved):
        assert r["ok"] is False and any("опрос после поста: нужно минимум два варианта" in e for e in r["errors"]), r
    good = client.post("/api/validate", json={**BLOCKS_POST, "poll_after": {"question": "?", "options": ["да", "нет"]}})
    assert good.json()["ok"] is True


def test_an_integer_outside_the_range_the_spec_states_is_reported_and_the_editor_knows_it(client):
    """A heading of size 16556 passed the check silently; only «В HTML» refused it (h1–h6). The range is the spec's
    description ("…; 1-6, …"); Telegram's verdict on such a value is unmeasured, so it is a warning, as for an enum."""
    import spec

    spec.current.load_initial()

    def warnings(block):
        return spec.validate_rich_message(spec.current, {"blocks": [block]})["warnings"]

    (w,) = warnings({"type": "heading", "size": 16556, "text": "Заголовок"})
    assert "blocks[0].size" in w and "16556" in w and "1–6" in w
    assert warnings({"type": "heading", "size": 6, "text": "Заголовок"}) == []
    assert any(
        "0–24" in w for w in warnings({"type": "map", "location": {"latitude": 55.75, "longitude": 37.62}, "zoom": 25})
    )
    # the editor's form reads the same range; a range the app clamps itself is not a limit of the post
    fields = {f["name"]: f for f in client.get("/api/spec").json()["types"]["InputRichBlockSectionHeading"]["fields"]}
    assert fields["size"]["range"] == [1, 6] and fields["text"]["range"] is None
    height = next(f for f in spec.current.types["InputWidgetMessageContent"]["fields"] if f["name"] == "height")
    assert "50-600" in height["description"] and spec.number_range(height["description"], height["types"]) is None
