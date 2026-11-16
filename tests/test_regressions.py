"""Regressions found in the architecture review (rich-studio-arch/architecture-review.md, P0/P1)."""

import asyncio
import time

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
