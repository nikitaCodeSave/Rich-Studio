"""Rich reply to a callback button (Bot API 10.4): the ephemeral sendRichMessage a press sends before it is answered,
its check like a post, and what happens when it does not go (ADR 0021)."""

import asyncio
import time

import httpx
import pytest

import logs
import settings
import store
import telegram
from services import callbacks, updates
from test_api import BLOCKS_POST
from test_mcp import call

LIKE = {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]}
REPLY = {
    "rich_message": {"html": "<p><b>Секрет</b> только для вас</p>"},
    "ephemeral_message_parameters": {"replace_callback_query_message": True},
}


@pytest.fixture(autouse=True)
def log_file(fresh_data):
    """The JSON log in this test's data folder."""
    yield logs.setup()
    logs.setup()  # close the file before the folder goes away


def _publish(client, answer) -> dict:
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "reply_markup": LIKE, "callbacks": {"like": answer}}})
    assert r.status_code == 200, r.text
    return r.json()


def _press(sent: dict, cq: str = "cq1", received: float | None = None) -> str | None:
    upd = {
        "update_id": 1,
        "callback_query": {
            "id": cq,
            "data": "like",
            "from": {"id": 5},
            "message": {"message_id": sent["message"]["message_id"], "chat": {"id": -1001}},
        },
    }
    return asyncio.run(updates.handle_update(upd, received))


def _methods(tg, start: int = 0) -> list[str]:
    return [c["method"] for c in tg.calls[start:]]


def test_a_text_and_the_old_object_are_answered_as_before(client, tg):
    for answer, body in (
        ("Спасибо", {"text": "Спасибо"}),
        ({"text": "Голос учтён", "show_alert": True, "cache_time": 5}, {"text": "Голос учтён", "show_alert": True}),
    ):
        sent = _publish(client, answer)
        assert sent["warnings"] == []
        start = len(tg.calls)
        _press(sent)
        assert _methods(tg, start) == ["answerCallbackQuery"]  # no reply was asked for, none goes
        assert {k: v for k, v in tg.last("answerCallbackQuery")["body"].items() if k in body} == body


def test_a_press_sends_the_rich_reply_then_answers(client, tg):
    sent = _publish(client, {"text": "Ответ на месте поста", "reply": REPLY})
    start = len(tg.calls)
    assert _press(sent) == "callback"
    assert _methods(tg, start) == ["sendRichMessage", "answerCallbackQuery"]
    reply = tg.calls[start]["body"]
    # sendRichMessage as the post wrote it: no chat, the press's id added next to the flag, nothing renamed
    assert "chat_id" not in reply and reply["rich_message"] == REPLY["rich_message"]
    assert reply["ephemeral_message_parameters"] == {"replace_callback_query_message": True, "callback_query_id": "cq1"}
    # the reply is the studio's key, never a parameter of answerCallbackQuery
    assert tg.last("answerCallbackQuery")["body"] == {"callback_query_id": "cq1", "text": "Ответ на месте поста"}
    line = next(x for x in logs.read("INFO", text="callback.reply") if x.get("event") == "callback.reply")
    assert line["post_id"] == sent["post"]["id"] and line["callback_data"] == "like" and line["replace"] is True
    post = client.get(f"/api/posts/{sent['post']['id']}").json()
    assert post["clicks"] == {"like": 1} and post["reply_errors"] is None


def test_a_reply_without_text_still_stops_the_button(client, tg):
    sent = _publish(client, {"reply": {"rich_message": {"markdown": "**Только вам**"}}})
    _press(sent)
    assert tg.last("sendRichMessage")["body"]["ephemeral_message_parameters"] == {"callback_query_id": "cq1"}
    assert tg.last("answerCallbackQuery")["body"] == {"callback_query_id": "cq1"}


def test_a_refused_reply_is_not_repeated_and_is_counted_next_to_the_clicks(client, tg):
    sent = _publish(client, {"text": "Смотрите ниже", "reply": REPLY})
    refused = {"ok": False, "error_code": 400, "description": "Bad Request: query is too old"}
    tg.queue["sendRichMessage"] = [refused, refused]
    start = len(tg.calls)
    _press(sent, "cq1")
    _press(sent, "cq2")
    # one reply per press, never a second try; the answer goes all the same
    assert _methods(tg, start) == ["sendRichMessage", "answerCallbackQuery"] * 2
    assert tg.last("answerCallbackQuery")["body"]["text"] == "Смотрите ниже"
    post = client.get(f"/api/posts/{sent['post']['id']}").json()
    assert post["clicks"] == {"like": 2}
    assert post["reply_errors"]["like"]["n"] == 2 and "query is too old" in post["reply_errors"]["like"]["error"]
    [row] = [p for p in client.get("/api/posts").json() if p["id"] == sent["post"]["id"]]
    assert row["reply_errors"]["like"]["n"] == 2  # the list has it next to the clicks
    line = next(x for x in logs.read("WARNING", text="callback.reply_failed") if x.get("outcome") == "refused")
    assert line["post_id"] == sent["post"]["id"] and line["callback_data"] == "like" and line["code"] == 400


def test_a_reply_that_may_have_gone_is_not_sent_again(client, tg, monkeypatch):
    sent = _publish(client, {"text": "ок", "reply": REPLY})
    real = telegram.call
    asked: list[str] = []

    async def broken_mid_request(method, params=None, timeout=60):
        asked.append(method)
        if method == "sendRichMessage":
            raise httpx.ReadError(f"broken {settings.get().api_base}/bot{settings.get().bot_token}/sendRichMessage")
        return await real(method, params, timeout)

    monkeypatch.setattr(telegram, "call", broken_mid_request)
    _press(sent)
    assert asked == ["sendRichMessage", "answerCallbackQuery"]
    err = client.get(f"/api/posts/{sent['post']['id']}").json()["reply_errors"]["like"]["error"]
    assert "мог дойти" in err and settings.get().bot_token not in err  # stored without the token (redact.py)
    line = next(x for x in logs.read("WARNING", text="callback.reply_failed"))
    assert line["outcome"] == "may_have_gone" and settings.get().bot_token not in line["msg"]


def test_a_reply_waits_no_longer_than_the_window(client, tg, monkeypatch):
    sent = _publish(client, {"text": "ок", "reply": REPLY})
    real = telegram.call

    async def silent(method, params=None, timeout=60):
        if method == "sendRichMessage":
            await asyncio.sleep(5)  # this coroutine only: the fake Telegram's thread keeps running
        return await real(method, params, timeout)

    monkeypatch.setattr(telegram, "call", silent)
    started = time.monotonic()
    _press(sent, received=time.monotonic() - (callbacks.WINDOW - 1.5))  # 1.5 s left of the window
    assert time.monotonic() - started < 4
    assert tg.last("answerCallbackQuery")["body"]["callback_query_id"] == "cq1"
    err = client.get(f"/api/posts/{sent['post']['id']}").json()["reply_errors"]["like"]["error"]
    assert "не ответил" in err and "мог дойти" in err


def test_a_press_that_waited_past_the_window_gets_only_the_answer(client, tg):
    """The studio was busy or getUpdates brought the press late: Telegram would not take the reply any more."""
    sent = _publish(client, {"text": "Поздно", "reply": REPLY})
    start = len(tg.calls)
    _press(sent, received=time.monotonic() - 20)
    assert _methods(tg, start) == ["answerCallbackQuery"]
    err = client.get(f"/api/posts/{sent['post']['id']}").json()["reply_errors"]["like"]
    assert err["n"] == 1 and "15 с" in err["error"]
    assert next(x for x in logs.read("WARNING", text="callback.reply_failed"))["outcome"] == "late"


def test_a_broken_stored_reply_never_stops_the_answer(client, tg):
    """A payload stored before the check (or by hand in the database) still gets its answer; the reply is a failure."""
    sent = _publish(client, "Спасибо")
    pid = sent["post"]["id"]
    broken = {"text": "ок", "reply": {"rich_message": {"html": "<p>x</p>"}, "ephemeral_message_parameters": "yes"}}
    store.update_post(pid, payload={**store.get_post(pid)["payload"], "callbacks": {"like": broken}})
    start = len(tg.calls)
    _press(sent)
    assert _methods(tg, start) == ["answerCallbackQuery"]
    assert client.get(f"/api/posts/{pid}").json()["reply_errors"]["like"]["n"] == 1


def test_the_buttons_of_a_preview_send_its_reply_too(client, tg):
    post = {**BLOCKS_POST, "reply_markup": LIKE, "callbacks": {"like": {"reply": REPLY}}}
    mid = client.post("/api/preview-send", json={"post": post}).json()["message_id"]
    start = len(tg.calls)
    click = {"id": "cqp", "data": "like", "message": {"message_id": mid, "chat": {"id": 777}}}
    asyncio.run(updates.handle_update({"update_id": 1, "callback_query": click}))
    assert _methods(tg, start) == ["sendRichMessage", "answerCallbackQuery"]
    assert tg.calls[start]["body"]["ephemeral_message_parameters"]["callback_query_id"] == "cqp"


# ---------- the check: like a post, at save and before anything goes ----------
def test_a_bad_reply_stops_the_post_like_a_bad_first_comment(client, tg):
    bad = {**BLOCKS_POST, "reply_markup": LIKE, "callbacks": {"like": {"reply": {"rich_message": {}}}}}
    report = client.post("/api/validate", json=bad).json()
    assert report["ok"] is False
    assert report["errors"] == ["ответ на кнопку «👍»: нужно ровно одно из html / markdown / blocks, сейчас: ничего"]
    start = len(tg.calls)
    for path, body in (("/api/send", {"post": bad}), ("/api/preview-send", {"post": bad})):
        r = client.post(path, json=body)
        assert r.status_code == 422 and r.json()["detail"]["message"] == "ответ на кнопку не прошёл проверку"
    assert len(tg.calls) == start  # nothing went to Telegram
    # the first comment is checked at save the same way now
    comment = client.post("/api/validate", json={**BLOCKS_POST, "comment": {"html": ""}}).json()
    assert comment["ok"] is False and comment["errors"][0].startswith("комментарий: ")


def test_what_a_reply_must_not_hold(client):
    def errors(reply):
        post = {**BLOCKS_POST, "reply_markup": LIKE, "callbacks": {"like": {"reply": reply}}}
        return client.post("/api/validate", json=post).json()["errors"]

    rm = {"html": "<p>x</p>"}
    assert errors({"rich_message": rm, "chat_id": "@channel"}) == [
        "ответ на кнопку «👍»: reply.chat_id: ответ уходит только нажавшему, чат не задаётся"
    ]
    [own] = errors({"rich_message": rm, "ephemeral_message_parameters": {"callback_query_id": "x"}})
    assert "callback_query_id: его задаёт студия" in own
    [who] = errors({"rich_message": rm, "ephemeral_message_parameters": {"receiver_user_id": 5}})
    assert "receiver_user_id" in who
    [flat] = errors({"html": "<p>x</p>"})
    assert "нет обязательного поля rich_message" in flat and "внутрь rich_message" in flat
    [typed] = errors({"rich_message": rm, "ephemeral_message_parameters": {"replace_callback_query_message": "yes"}})
    assert "replace_callback_query_message: ожидалось true/false" in typed
    [blocks] = errors({"rich_message": {"blocks": [{"type": "paragraph"}]}})
    assert blocks.startswith("ответ на кнопку «👍»: reply.rich_message.blocks[0]")


def test_unknown_fields_are_warnings_and_go_as_they_are(client, tg):
    answer = {"text": "ок", "future_answer_field": 1, "reply": {**REPLY, "future_param": True}}
    sent = _publish(client, answer)
    assert any("future_answer_field: поля нет в answerCallbackQuery" in w for w in sent["warnings"])
    assert any("reply.future_param: поля нет в sendRichMessage" in w for w in sent["warnings"])
    _press(sent)
    assert tg.last("sendRichMessage")["body"]["future_param"] is True
    assert tg.last("answerCallbackQuery")["body"]["future_answer_field"] == 1


def test_warnings_for_what_would_not_work(client):
    answer = {
        "text": "ок",
        "cache_time": 60,
        "reply": {**REPLY, "reply_markup": {"inline_keyboard": [[{"text": "Ещё", "callback_data": "more"}]]}},
    }
    post = {**BLOCKS_POST, "reply_markup": LIKE, "callbacks": {"like": answer}}
    report = client.post("/api/validate", json=post).json()
    assert report["ok"] is True
    assert any("cache_time" in w for w in report["warnings"])
    assert any("студия не отвечает" in w for w in report["warnings"])


def test_buttons_replaced_with_a_bad_reply_are_refused(client, tg):
    sent = _publish(client, "Спасибо")
    start = len(tg.calls)
    r = client.post(
        f"/api/posts/{sent['post']['id']}/buttons",
        json={"reply_markup": LIKE, "callbacks": {"like": {"reply": {"rich_message": {"html": ""}}}}},
    )
    assert r.status_code == 422 and r.json()["detail"]["message"] == "ответ на кнопку не прошёл проверку"
    assert len(tg.calls) == start
    ok = client.post(
        f"/api/posts/{sent['post']['id']}/buttons", json={"reply_markup": LIKE, "callbacks": {"like": {"reply": REPLY}}}
    )
    assert ok.status_code == 200 and ok.json()["payload"]["callbacks"]["like"]["reply"] == REPLY


def test_save_draft_reports_the_reply_errors_to_the_agent(client):
    post = {**BLOCKS_POST, "reply_markup": LIKE, "callbacks": {"like": {"reply": {"rich_message": {"html": ""}}}}}
    _, d, _ = call(client, "save_draft", post=post, title="С ответом")
    assert d["validation"]["ok"] is False
    assert d["validation"]["errors"][0].startswith("ответ на кнопку «👍»:")
    assert store.get_draft(d["id"])["payload"]["callbacks"]["like"]["reply"] == {"rich_message": {"html": ""}}
