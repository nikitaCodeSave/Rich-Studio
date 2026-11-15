"""A send whose outcome is unknown (the post may be in the channel): a flag in the data, not words in the error.

Every path that answers "may have gone" sets `posts.outcome_unknown`; a successful resend of the draft never drops
such a row, and the health report shows it until a person removes it (issue #90)."""

import asyncio
import time

import httpx
import pytest

import store
import telegram
from services import health, publisher, scheduler
from test_api import BLOCKS_POST, POLL


def _draft(client) -> str:
    return client.post("/api/drafts", json={"title": "A", "post": BLOCKS_POST}).json()["id"]


def _no_answer(*methods: str):
    """Telegram took the request and the answer was lost: the post may be in the channel."""
    real = telegram.call

    async def call(method, params=None, *a, **kw):
        if method in methods:
            raise httpx.ReadTimeout("timeout")
        return await real(method, params, *a, **kw)

    return call


def _flags() -> list[tuple[str, str, int]]:
    """(chat, status, flag) of every row, oldest first."""
    with store.conn() as c:
        return [tuple(r) for r in c.execute("select chat_id, status, outcome_unknown from posts order by rowid")]


def test_a_crash_mid_send_stays_listed_after_the_draft_goes_out_again(client, tg):
    """The row recovery left after a crash had no MAY_HAVE_GONE in its text, and the next successful send of the
    draft dropped it with the refusals: the trace of a possible duplicate was gone."""
    did = _draft(client)
    crashed = store.add_post("@c", BLOCKS_POST, did, "sending")  # the process died right here
    assert store.recover_stale_sending() == 1
    assert client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": did, "chat_id": "@c"}).status_code == 200
    left = store.get_post(crashed["id"])
    assert left["status"] == "failed" and left["outcome_unknown"] is True
    rows = client.get("/api/posts").json()
    assert [(p["status"], p["outcome_unknown"]) for p in rows] == [("sent", False), ("failed", True)]


def test_only_a_refusal_leaves_with_the_next_send(client, tg, monkeypatch):
    """The text of the error no longer decides: a refusal that merely quotes "мог уйти" still goes."""
    did, real = _draft(client), telegram.call
    store.add_post("@c", BLOCKS_POST, did, "failed", error="Bad Request: текст «мог уйти» не про исход")
    monkeypatch.setattr(telegram, "call", _no_answer("sendRichMessage"))
    assert client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": did, "chat_id": "@c"}).status_code == 502
    monkeypatch.setattr(telegram, "call", real)
    assert client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": did, "chat_id": "@c"}).status_code == 200
    assert _flags() == [("@c", "failed", 1), ("@c", "sent", 0)]


def test_no_answer_in_one_of_several_channels_flags_only_that_one(client, tg, monkeypatch):
    real = telegram.call

    async def flaky(method, params=None, *a, **kw):
        if method == "sendRichMessage" and params["chat_id"] == "@b":
            raise httpx.ReadTimeout("timeout")
        return await real(method, params, *a, **kw)

    monkeypatch.setattr(telegram, "call", flaky)
    assert client.post("/api/send", json={"post": BLOCKS_POST, "chat_ids": ["@a", "@b"]}).status_code == 200
    assert _flags() == [("@a", "sent", 0), ("@b", "failed", 1)]


def test_a_refusal_has_no_flag(client, tg):
    tg.fail["sendRichMessage"] = "Bad Request: chat not found"
    assert client.post("/api/send", json={"post": BLOCKS_POST}).status_code == 422
    assert _flags() == [("@test_channel", "failed", 0)]


def test_a_send_stopped_midway_is_flagged(client, monkeypatch):
    async def hang(*a, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(publisher, "send", hang)

    async def scenario():
        t = asyncio.create_task(publisher.publish(["@c"], BLOCKS_POST))
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(scenario())
    assert _flags() == [("@c", "failed", 1)]


def test_the_scheduler_flags_a_send_without_an_answer(client, tg, monkeypatch):
    store.add_post("@c", BLOCKS_POST, status="scheduled", scheduled_at=int(time.time()) - 5)
    monkeypatch.setattr(telegram, "call", _no_answer("sendRichMessage"))
    asyncio.run(scheduler.run_due_posts())
    assert _flags() == [("@c", "failed", 1)]


def test_the_scheduler_flags_a_send_stopped_midway(client, monkeypatch):
    store.add_post("@c", BLOCKS_POST, status="scheduled", scheduled_at=int(time.time()) - 5)

    async def hang(*a, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(publisher, "send", hang)

    async def scenario():
        t = asyncio.create_task(scheduler.run_due_posts())
        await asyncio.sleep(0.05)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(scenario())
    assert _flags() == [("@c", "failed", 1)]


def test_the_scheduler_flags_nothing_it_knows_was_not_sent(client, tg):
    store.add_post("@c", BLOCKS_POST, status="scheduled", scheduled_at=int(time.time()) - 5)
    tg.fail["sendRichMessage"] = "Bad Request: chat not found"
    asyncio.run(scheduler.run_due_posts())
    assert _flags() == [("@c", "failed", 0)]


def test_a_poll_after_the_post_without_an_answer_is_flagged_and_kept(client, tg, monkeypatch):
    did = _draft(client)
    monkeypatch.setattr(telegram, "call", _no_answer("sendPoll"))
    post = {**BLOCKS_POST, "poll_after": POLL}
    for _ in range(2):
        assert client.post("/api/send", json={"post": post, "draft_id": did, "chat_id": "@c"}).status_code == 200
    assert [f for f in _flags() if f[1] == "failed"] == [("@c", "failed", 1), ("@c", "failed", 1)]


def test_a_refused_poll_after_the_post_is_not_flagged(client, tg):
    tg.fail["sendPoll"] = "Bad Request: poll options are too long"
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "poll_after": POLL}, "chat_id": "@c"})
    assert r.status_code == 200
    assert _flags() == [("@c", "sent", 0), ("@c", "failed", 0)]


def test_health_shows_an_unknown_outcome_however_old_until_the_row_is_removed(client, tg):
    """Like a missed post: a crash and a day off later the report still asks to look at the channel; a resend does
    not answer that question (there may be two posts now), removing the row after looking does."""
    did = _draft(client)
    crashed = store.add_post("@c", BLOCKS_POST, did, "sending")
    store.recover_stale_sending()
    refused = store.add_post("@c", BLOCKS_POST, status="failed", error="Bad Request: chat not found")
    long_ago = int(time.time()) - health.DAY - 3600
    with store.conn() as c:
        c.execute("update posts set created_at=? where id in (?, ?)", (long_ago, crashed["id"], refused["id"]))
    assert client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": did, "chat_id": "@c"}).status_code == 200
    check = health.posts()
    assert check["status"] == "warn" and check["summary"].startswith("1 постов")
    [shown] = check["details"]["posts"]
    assert shown["id"] == crashed["id"] and shown["outcome_unknown"] is True
    assert "outcome_unknown" in check["action"] and "DELETE /api/posts/{id}" in check["action"]
    assert client.delete("/api/posts/" + crashed["id"]).json()["status"] == "removed"
    assert health.posts()["status"] == "ok"


def test_the_flag_is_a_bool_in_every_answer(client, tg, monkeypatch):
    """One type for one field: the list, a single post, a send's answer and the health report agree."""
    did = _draft(client)
    crashed = store.add_post("@c", BLOCKS_POST, did, "sending")
    store.recover_stale_sending()
    sent = client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": did, "chat_id": "@c"}).json()
    assert sent["post"]["outcome_unknown"] is False and sent["results"][0]["post"]["outcome_unknown"] is False
    assert client.get("/api/posts/" + crashed["id"]).json()["outcome_unknown"] is True
    assert client.get("/api/posts/" + sent["post"]["id"]).json()["outcome_unknown"] is False
    assert {p["id"]: p["outcome_unknown"] for p in client.get("/api/posts").json()} == {
        crashed["id"]: True,
        sent["post"]["id"]: False,
    }
    assert [p["outcome_unknown"] for p in health.posts()["details"]["posts"]] == [True]
