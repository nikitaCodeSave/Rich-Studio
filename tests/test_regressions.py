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
