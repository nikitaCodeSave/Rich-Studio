"""Regressions found in the architecture review (rich-studio-arch/architecture-review.md, P0/P1)."""

import asyncio
import time

import httpx

import settings
from services import scheduler
from test_api import BLOCKS_POST


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
