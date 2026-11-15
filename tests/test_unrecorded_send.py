"""Telegram accepted the post, the studio could not write it down (issue #89).

The post is in the channel: the answer says so with chat_id and message_id, never a bare 500 or "may have gone"; a
retry with the same key sends nothing; the confirmation is written at the next chance and recovery never turns it
into "failed". A failure of a step after the record is a warning in a 200. The same for the scheduler."""

import asyncio
import json
import sqlite3
import time

import pytest

import logs
import store
import telegram
from services import channels, idempotency, loops, publisher, scheduler
from test_api import BLOCKS_POST, POLL
from test_mcp import call

FULL = "database or disk is full"
UNRECORDED = "опубликован, студия не смогла записать — не публикуйте повторно"


@pytest.fixture(autouse=True)
def nothing_remembered(monkeypatch):
    """What one test could not write down must not leak into the next one."""
    monkeypatch.setattr(publisher, "unrecorded", {})
    monkeypatch.setattr(idempotency, "unwritten", {})


@pytest.fixture
def log_file(fresh_data):
    path = logs.setup()
    yield path
    logs.setup()  # close the file before the folder goes away


def sends(tg) -> int:
    return len([c for c in tg.calls if c["method"] == "sendRichMessage"])


def _full_on_sent(monkeypatch, chat=None):
    """The disk is full when the studio writes "sent" (for `chat` only, if given). Returns the undo."""
    real = store.update_post

    def update(pid, **fields):
        if fields.get("status") == "sent" and (chat is None or (store.get_post(pid) or {}).get("chat_id") == chat):
            raise sqlite3.OperationalError(FULL)
        return real(pid, **fields)

    monkeypatch.setattr(store, "update_post", update)
    return lambda: monkeypatch.setattr(store, "update_post", real)


def _broken(*a, **kw):
    raise sqlite3.OperationalError("disk I/O error")


def _unrecorded_events() -> list[dict]:
    return [x for x in logs.read("ERROR") if x.get("event") == "send.unrecorded"]


# ---------- the record of "sent" fails ----------
def test_the_answer_says_where_the_post_is_and_the_log_keeps_it(client, tg, monkeypatch, log_file):
    _full_on_sent(monkeypatch)
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 502
    d = r.json()["detail"]
    assert UNRECORDED in d["message"] and FULL in d["message"]
    assert (d["chat_id"], d["message_id"]) == ("@test_channel", tg.next_id)
    [event] = _unrecorded_events()
    assert (event["chat_id"], event["message_id"]) == ("@test_channel", tg.next_id)
    # the row stays "sending" in the table: nothing marks it failed while the studio remembers the answer
    [p] = store.list_posts()
    assert p["status"] == "sending" and publisher.unrecorded[p["id"]]["message_id"] == tg.next_id


def test_the_whole_disk_full_is_the_same_502_and_a_retry_sends_nothing(client, tg, monkeypatch):
    """finish('uncertain') fails on the same disk: the client still learns the message_id, not a bare 500."""
    _full_on_sent(monkeypatch)
    monkeypatch.setattr(store.idempotency, "finish", _broken)
    h = {"Idempotency-Key": "full-1"}
    first = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert first.status_code == 502 and UNRECORDED in first.json()["detail"]["message"]
    again = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert again.status_code == 502 and again.json()["detail"] == first.json()["detail"]
    assert sends(tg) == 1


def test_the_confirmation_is_written_at_the_next_send(client, tg, monkeypatch, log_file):
    restore = _full_on_sent(monkeypatch)
    assert client.post("/api/send", json={"post": BLOCKS_POST}).status_code == 502
    first_id = tg.next_id
    restore()  # space on the disk again
    assert client.post("/api/send", json={"post": BLOCKS_POST}).status_code == 200
    posts = sorted(store.list_posts(), key=lambda p: p["message_id"])
    assert [(p["status"], p["message_id"]) for p in posts] == [("sent", first_id), ("sent", first_id + 1)]
    assert store.get_post(posts[0]["id"])["chat_num"] == -1001
    assert publisher.unrecorded == {}
    assert any(x.get("event") == "send.recorded" for x in logs.read("INFO"))


def test_recovery_of_sending_rows_leaves_a_remembered_send_alone(client, tg, monkeypatch):
    monkeypatch.setattr(loops, "STATE", loops.State(owner="me"))
    restore = _full_on_sent(monkeypatch)
    real_confirm = store.confirm_sent
    monkeypatch.setattr(store, "confirm_sent", _broken)  # the database still refuses the late record
    assert client.post("/api/send", json={"post": BLOCKS_POST}).status_code == 502
    restore()
    loops.Loops([])._lead()  # this process takes the lease: recovery of rows left "sending"
    [p] = store.list_posts()
    assert p["status"] == "sending" and not p["outcome_unknown"]
    monkeypatch.setattr(store, "confirm_sent", real_confirm)
    loops.Loops([])._lead()  # written down before recovery runs
    [p] = store.list_posts()
    assert p["status"] == "sent" and p["message_id"] == tg.next_id


def test_a_late_record_settles_a_row_another_process_marked_may_have_gone(client, tg, monkeypatch):
    restore = _full_on_sent(monkeypatch)
    assert client.post("/api/send", json={"post": BLOCKS_POST}).status_code == 502
    restore()
    assert store.recover_stale_sending() == 1  # another studio process took over and knew nothing of the answer
    assert publisher.write_unrecorded() == 0
    [p] = store.list_posts()
    assert (p["status"], p["message_id"], p["outcome_unknown"], p["error"]) == ("sent", tg.next_id, False, None)


def test_one_unrecorded_channel_of_several_is_reported_and_the_others_go(client, tg, monkeypatch):
    _full_on_sent(monkeypatch, chat="@b")
    r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_ids": ["@a", "@b", "@c"]})
    assert r.status_code == 200
    out = r.json()
    by_chat = {x["chat_id"]: x for x in out["results"]}
    assert all(x["ok"] for x in by_chat.values()) and sends(tg) == 3
    assert by_chat["@b"]["unrecorded"] and FULL in by_chat["@b"]["error"]
    assert by_chat["@b"]["message_id"] == by_chat["@b"]["message"]["message_id"]
    assert out["post"]["chat_id"] == "@a"  # the first recorded one
    assert any(w.startswith("@b: ") and UNRECORDED in w for w in out["warnings"])


def test_nothing_recorded_but_one_channel_has_the_post_is_unrecorded_not_not_sent(client, tg, monkeypatch):
    real = telegram.call

    async def refuse_a(method, params=None, *a, **kw):
        if method == "sendRichMessage" and params["chat_id"] == "@a":
            raise telegram.TelegramError("Bad Request: chat not found", 400)
        return await real(method, params, *a, **kw)

    monkeypatch.setattr(telegram, "call", refuse_a)
    _full_on_sent(monkeypatch, chat="@b")
    r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_ids": ["@a", "@b"]})
    assert r.status_code == 502
    d = r.json()["detail"]
    assert UNRECORDED in d["message"] and (d["chat_id"], d["message_id"]) == ("@b", tg.next_id)
    assert d["errors"] == ["@a: Bad Request: chat not found"]


def test_poll_and_media_kinds_answer_the_same(client, tg, monkeypatch):
    _full_on_sent(monkeypatch)
    r = client.post("/api/poll", json={"poll": POLL})
    assert r.status_code == 502 and UNRECORDED in r.json()["detail"]["message"]
    assert r.json()["detail"]["message_id"] == tg.next_id


def test_mcp_publish_says_the_same_and_a_retry_sends_nothing(client, tg, monkeypatch):
    _full_on_sent(monkeypatch)
    for _ in range(2):  # an agent retries without a key: the request itself is the key
        res, _, text = call(client, "publish_post", post=BLOCKS_POST)
        assert res["isError"] and UNRECORDED in text
        detail = json.loads(text[text.index("{") :])
        assert (detail["chat_id"], detail["message_id"]) == ("@test_channel", tg.next_id)
    assert sends(tg) == 1


def test_a_poll_after_the_post_that_could_not_be_recorded_is_a_warning_with_its_id(client, tg, monkeypatch, log_file):
    real = store.add_post

    def add(chat, payload, *a, **kw):
        if kw.get("kind") == "poll" and (a[1:2] == ("sent",) or kw.get("status") == "sent"):
            raise sqlite3.OperationalError(FULL)
        return real(chat, payload, *a, **kw)

    monkeypatch.setattr(store, "add_post", add)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "poll_after": POLL}})
    assert r.status_code == 200 and r.json()["post"]["status"] == "sent"
    [w] = [w for w in r.json()["warnings"] if "опрос" in w]
    assert UNRECORDED in w and str(tg.next_id) in w
    assert [x["message_id"] for x in _unrecorded_events()] == [tg.next_id]


# ---------- the steps after the record ----------
def _sent_with_warning(r, needle: str) -> dict:
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["post"]["status"] == "sent" and out["post"]["message_id"] == out["message"]["message_id"]
    assert any(needle in w for w in out["warnings"]), out["warnings"]
    return out


def test_drop_refused_failing_is_a_warning(client, tg, monkeypatch):
    did = client.post("/api/drafts", json={"title": "A", "post": BLOCKS_POST}).json()["id"]
    monkeypatch.setattr(store, "drop_refused", _broken)
    _sent_with_warning(client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": did}), "disk I/O error")


def test_channel_title_failing_is_a_warning(client, tg, monkeypatch):
    monkeypatch.setattr(channels, "title_of", _broken)
    out = _sent_with_warning(client.post("/api/send", json={"post": BLOCKS_POST}), "disk I/O error")
    assert out["results"][0]["chat_title"] is None


def test_after_send_steps_failing_is_a_warning(client, tg, monkeypatch):
    real = store.update_post

    def update(pid, **fields):
        if "chat_num" in fields:
            raise sqlite3.OperationalError(FULL)
        return real(pid, **fields)

    monkeypatch.setattr(store, "update_post", update)
    _sent_with_warning(client.post("/api/send", json={"post": BLOCKS_POST}), FULL)


def test_reading_the_record_back_failing_is_a_warning(client, tg, monkeypatch):
    monkeypatch.setattr(store, "get_post", _broken)
    out = _sent_with_warning(client.post("/api/send", json={"post": BLOCKS_POST}), "disk I/O error")
    assert out["post"]["id"] == store.list_posts()[0]["id"]


def test_saving_the_answer_for_the_key_failing_is_a_warning_and_a_retry_sends_nothing(client, tg, monkeypatch):
    real = store.idempotency.finish

    def finish(key, status, response):
        if status == "done":
            raise sqlite3.OperationalError(FULL)
        return real(key, status, response)

    monkeypatch.setattr(store.idempotency, "finish", finish)
    h = {"Idempotency-Key": "done-1"}
    first = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    _sent_with_warning(first, "Idempotency-Key")
    again = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert again.status_code == 200 and again.json()["post"]["id"] == first.json()["post"]["id"]
    assert sends(tg) == 1
    monkeypatch.setattr(store.idempotency, "finish", real)
    client.post("/api/send", json={"post": BLOCKS_POST})  # the next request writes the answer down
    with store.conn() as c:
        assert c.execute("select status from idempotency where key='done-1'").fetchone()[0] == "done"


# ---------- the scheduler ----------
def _due(*chats: str) -> list[str]:
    recs = store.add_scheduled(list(chats), BLOCKS_POST, int(time.time()) - 5)
    return [r["id"] for r in recs]


def test_scheduler_goes_on_after_a_send_it_could_not_record(client, tg, monkeypatch, log_file):
    a, b = _due("@a", "@b")
    restore = _full_on_sent(monkeypatch, chat="@a")
    asyncio.run(scheduler.run_due_posts())
    assert store.get_post(a)["status"] == "sending" and store.get_post(b)["status"] == "sent"
    [event] = _unrecorded_events()
    assert event["chat_id"] == "@a" and event["post_id"] == a
    assert event["message_id"] == store.get_post(b)["message_id"] - 1
    restore()
    asyncio.run(scheduler.run_due_posts())  # the next pass writes it down
    p = store.get_post(a)
    assert p["status"] == "sent" and p["message_id"] == event["message_id"]
    assert sends(tg) == 2


def test_scheduler_goes_on_when_the_outcome_of_one_post_cannot_be_written(client, tg, monkeypatch):
    a, b = _due("@a", "@b")
    real = telegram.call

    async def refuse_a(method, params=None, *x, **kw):
        if method == "sendRichMessage" and params["chat_id"] == "@a":
            raise telegram.TelegramError("Bad Request: chat not found", 400)
        return await real(method, params, *x, **kw)

    real_update = store.update_post

    def update(pid, **fields):
        if fields.get("status") == "failed":
            raise sqlite3.OperationalError(FULL)
        return real_update(pid, **fields)

    monkeypatch.setattr(telegram, "call", refuse_a)
    monkeypatch.setattr(store, "update_post", update)
    with pytest.raises(sqlite3.OperationalError):  # for the loop's heartbeat, after the whole pass
        asyncio.run(scheduler.run_due_posts())
    assert store.get_post(a)["status"] == "sending"  # for recovery to settle; the refusal is in the log
    assert store.get_post(b)["status"] == "sent"


def test_scheduler_warnings_that_cannot_be_written_do_not_break_the_pass(client, tg, monkeypatch):
    [a] = _due("@a")
    real = store.update_post

    def update(pid, **fields):
        if set(fields) == {"error"}:
            raise sqlite3.OperationalError(FULL)
        return real(pid, **fields)

    store.update_post(a, payload={**BLOCKS_POST, "pin": True})
    tg.fail["pinChatMessage"] = "Bad Request: not enough rights"
    monkeypatch.setattr(store, "update_post", update)
    asyncio.run(scheduler.run_due_posts())
    assert store.get_post(a)["status"] == "sent"
