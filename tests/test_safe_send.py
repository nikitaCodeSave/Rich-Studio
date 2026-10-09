"""Safe sending for agents: Idempotency-Key, who sent the post, dry run."""

import time

import httpx
import pytest

import store
from test_api import BLOCKS_POST


def sends(tg) -> int:
    return len([c for c in tg.calls if c["method"] == "sendRichMessage"])


# ---------- Idempotency-Key ----------
def test_retry_with_same_key_returns_first_result_without_second_post(client, tg):
    h = {"Idempotency-Key": "post-1"}
    first = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    again = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert sends(tg) == 1
    assert len(store.list_posts()) == 1


def test_without_key_every_request_posts(client, tg):
    client.post("/api/send", json={"post": BLOCKS_POST})
    client.post("/api/send", json={"post": BLOCKS_POST})
    assert sends(tg) == 2


def test_same_key_with_different_body_is_refused(client, tg):
    h = {"Idempotency-Key": "post-2"}
    client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    other = {"rich_message": {"html": "<p>другой</p>"}}
    r = client.post("/api/send", json={"post": other}, headers=h)
    assert r.status_code == 422 and "Idempotency-Key" in r.json()["detail"]
    assert sends(tg) == 1


def test_key_is_freed_when_nothing_was_sent(client, tg):
    h = {"Idempotency-Key": "post-3"}
    tg.fail["sendRichMessage"] = "Bad Request: chat not found"
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 502
    tg.fail.clear()
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert r.status_code == 200 and sends(tg) == 2  # the failed attempt reached Telegram too


def test_network_error_mid_send_is_never_resent_with_same_key(client, tg, monkeypatch):
    import telegram

    real = telegram.call

    async def down(method, params=None, timeout=60):
        raise httpx.ReadTimeout("timed out")

    h = {"Idempotency-Key": "post-4"}
    monkeypatch.setattr(telegram, "call", down)
    first = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert first.status_code == 502 and "мог уйти" in first.json()["detail"]
    monkeypatch.setattr(telegram, "call", real)
    again = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert again.status_code == 502 and "мог уйти" in again.json()["detail"]
    assert sends(tg) == 0


def test_error_after_telegram_accepted_never_resends_with_same_key(client, tg, monkeypatch):
    # Telegram took the post, then the studio broke while recording it: the key must not be freed
    real = store.add_post

    def broken(*a, **kw):
        raise RuntimeError("disk full")

    h = {"Idempotency-Key": "post-5"}
    monkeypatch.setattr(store, "add_post", broken)
    with pytest.raises(RuntimeError):
        client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    monkeypatch.setattr(store, "add_post", real)
    again = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert again.status_code == 502 and "мог уйти" in again.json()["detail"]
    assert sends(tg) == 1


def test_pending_key_left_by_a_crash_is_may_have_gone(client, tg):
    h = {"Idempotency-Key": "post-6"}
    client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    # as if the process died mid-send: the claim stays "pending"
    with store.conn() as c:
        c.execute("update idempotency set status='pending', response=null where key='post-6'")
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 409  # maybe still running
    with store.conn() as c:
        c.execute("update idempotency set created_at=created_at-3600 where key='post-6'")
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert r.status_code == 502 and "мог уйти" in r.json()["detail"]
    assert sends(tg) == 1


def test_schedule_and_poll_accept_keys(client, tg):
    h = {"Idempotency-Key": "sched-1"}
    body = {"post": BLOCKS_POST, "at": "2099-01-01T09:00:00+03:00"}
    a = client.post("/api/schedule", json=body, headers=h).json()
    b = client.post("/api/schedule", json=body, headers=h).json()
    assert a["id"] == b["id"] and len(store.list_posts()) == 1
    poll = {"poll": {"question": "Да?", "options": ["да", "нет"]}}
    h = {"Idempotency-Key": "poll-1"}
    client.post("/api/poll", json=poll, headers=h)
    client.post("/api/poll", json=poll, headers=h)
    assert len([c for c in tg.calls if c["method"] == "sendPoll"]) == 1


def test_too_long_key_is_refused(client, tg):
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers={"Idempotency-Key": "k" * 201})
    assert r.status_code == 400 and sends(tg) == 0


# ---------- who sent the post ----------
def test_actor_is_recorded(client, tg):
    client.post("/api/send", json={"post": BLOCKS_POST}, headers={"X-Studio-Client": "mcp:claude-code"})
    client.post("/api/send", json={"post": BLOCKS_POST})
    client.post("/api/send", json={"post": BLOCKS_POST}, headers={"X-Studio-Client": "<script>"})
    actors = sorted(p["actor"] for p in store.list_posts())
    assert actors == ["api", "api", "mcp:claude-code"]


def test_actor_survives_scheduling(client, tg):
    import asyncio

    from services import scheduler

    body = {"post": BLOCKS_POST, "at": "2099-01-01T09:00:00+03:00"}
    rec = client.post("/api/schedule", json=body, headers={"X-Studio-Client": "ui"}).json()
    store.update_post(rec["id"], scheduled_at=int(time.time()) - 5)
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "sent" and p["actor"] == "ui"


# ---------- dry run ----------
def test_dry_run_shows_requests_and_sends_nothing(client, tg):
    post = {**BLOCKS_POST, "pin": True, "reply_markup": {"inline_keyboard": [[{"text": "a", "url": "https://t.me"}]]}}
    r = client.post("/api/send", json={"post": post, "chat_ids": ["@a", "-100"], "dry_run": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dry_run"] is True and d["after_send"] == ["pinChatMessage"]
    assert [x["params"]["chat_id"] for x in d["requests"]] == ["@a", -100]
    assert d["requests"][0]["method"] == "sendRichMessage"
    assert d["requests"][0]["params"]["rich_message"] == BLOCKS_POST["rich_message"]
    assert not tg.calls and not store.list_posts()


def test_dry_run_still_validates_and_checks_uploads(client, tg):
    bad = {"rich_message": {"blocks": [{"type": "heading", "text": "x", "size": "big"}]}}
    assert client.post("/api/send", json={"post": bad, "dry_run": True}).status_code == 422
    missing = {"rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": "upload:nope.jpg"}}]}}
    r = client.post("/api/send", json={"post": missing, "dry_run": True})
    assert r.status_code == 400 and "nope.jpg" in r.json()["detail"]
    assert not tg.calls


def test_dry_run_schedule_creates_nothing(client, tg):
    body = {"post": BLOCKS_POST, "at": "2099-01-01T09:00:00+03:00", "dry_run": True}
    d = client.post("/api/schedule", json=body).json()
    assert d["dry_run"] is True and d["at"] == 4070930400
    assert not store.list_posts()
