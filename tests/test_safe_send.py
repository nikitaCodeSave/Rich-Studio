"""Safe sending for agents: Idempotency-Key, who sent the post, dry run."""

import time

import httpx

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
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 422
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
    real = store.update_post

    def broken(pid, **fields):
        if fields.get("status") == "sent":
            raise RuntimeError("disk full")
        return real(pid, **fields)

    h = {"Idempotency-Key": "post-5"}
    monkeypatch.setattr(store, "update_post", broken)
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 500
    monkeypatch.setattr(store, "update_post", real)
    # the row written before the send is still there: the next start marks it "may have gone"
    assert [p["status"] for p in store.list_posts()] == ["sending"]
    assert store.recover_stale_sending() == 1
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


DAY_AND_HOUR = 25 * 3600


def _age_keys(seconds: int) -> None:
    with store.conn() as c:
        c.execute("update idempotency set created_at=created_at-?", (seconds,))


def _lose_answers(monkeypatch):
    """The post reaches Telegram, the answer is lost on the way back."""
    import telegram

    real = telegram.call

    async def lost(method, params=None, timeout=60):
        result = await real(method, params, timeout)
        if method == "sendRichMessage":
            raise httpx.ReadTimeout("timed out")
        return result

    monkeypatch.setattr(telegram, "call", lost)
    return lambda: monkeypatch.setattr(telegram, "call", real)


def _crash_mid_send(client, key):
    """The process died after the post went: the claim stays "pending"."""
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers={"Idempotency-Key": key}).status_code == 200
    with store.conn() as c:
        c.execute("update idempotency set status='pending', response=null where key=?", (key,))


def test_key_after_which_the_post_may_have_gone_never_expires(client, tg, monkeypatch):
    from services import housekeeping

    h = {"Idempotency-Key": "gone-1"}
    restore = _lose_answers(monkeypatch)
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 502
    restore()
    _crash_mid_send(client, "gone-2")
    assert sends(tg) == 2
    _age_keys(DAY_AND_HOUR)
    housekeeping.cleanup()
    for key in ("gone-1", "gone-2"):
        r = client.post("/api/send", json={"post": BLOCKS_POST}, headers={"Idempotency-Key": key})
        assert r.status_code == 502, r.text
        detail = r.json()["detail"]
        # what to do instead; the agent cannot see the channel, the person looks
        assert "мог уйти" in detail and "вместе с пользователем" in detail and "новым ключом" in detail
    assert sends(tg) == 2


def test_several_channels_that_may_have_got_the_post_keep_their_errors_on_retry(client, tg, monkeypatch):
    body, h = {"post": BLOCKS_POST, "chat_ids": ["@a", "@b"]}, {"Idempotency-Key": "gone-3"}
    restore = _lose_answers(monkeypatch)
    first = client.post("/api/send", json=body, headers=h)
    restore()
    again = client.post("/api/send", json=body, headers=h)
    assert first.status_code == again.status_code == 502
    assert again.json()["detail"]["errors"] == first.json()["detail"]["errors"] and sends(tg) == 2
    assert "новым ключом" in again.json()["detail"]["message"]


def test_scheduling_cut_off_by_a_crash_points_to_the_list_of_posts(client, tg):
    body, h = {"post": BLOCKS_POST, "at": "2099-01-01T09:00:00+03:00"}, {"Idempotency-Key": "sched-gone"}
    assert client.post("/api/schedule", json=body, headers=h).status_code == 200
    with store.conn() as c:
        c.execute("update idempotency set status='pending', response=null")
    _age_keys(3600)
    r = client.post("/api/schedule", json=body, headers=h)
    assert r.status_code == 502 and "отложите заново" in r.json()["detail"]
    assert len(store.list_posts()) == 1


def test_approval_without_an_answer_is_not_sent_again_with_a_new_key_either(client, tg, monkeypatch):
    # the hint must not send the agent to a dead end: only the person's «Решить снова» opens the decision again
    import asyncio

    import telegram
    from services import updates
    from test_suggested_posts import _suggestion

    real = telegram.call

    async def timeout(method, params=None, *a, **kw):
        if method == "approveSuggestedPost":
            raise TimeoutError("read timeout")
        return await real(method, params, *a, **kw)

    asyncio.run(updates.handle_update(_suggestion(80, {"state": "pending"})))
    url, h = "/api/suggested-posts/-1009:80/approve", {"Idempotency-Key": "approve-80"}
    monkeypatch.setattr(telegram, "call", timeout)
    assert client.post(url, json={}, headers=h).status_code == 502
    monkeypatch.setattr(telegram, "call", real)
    r = client.post(url, json={}, headers=h)
    assert r.status_code == 502 and "Решить снова" in r.json()["detail"] and "канал вместе" not in r.json()["detail"]
    assert client.post(url, json={}, headers={"Idempotency-Key": "approve-80b"}).status_code == 400
    assert client.post("/api/suggested-posts/-1009:80/reopen").status_code == 200
    assert client.post(url, json={}, headers={"Idempotency-Key": "approve-80c"}).status_code == 200
    assert len([c for c in tg.calls if c["method"] == "approveSuggestedPost"]) == 1


def test_done_key_is_forgotten_after_a_day(client, tg):
    from services import housekeeping

    h = {"Idempotency-Key": "done-1"}
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 200
    _age_keys(DAY_AND_HOUR)
    assert housekeeping.cleanup()["idempotency_keys"] == 1
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 200
    assert sends(tg) == 2


def test_agent_without_key_is_told_to_pass_a_new_one(client, tg, monkeypatch):
    # MCP turns the request into the key: the same draft and version stay blocked after a day too
    from test_mcp import call

    _, d, _ = call(client, "save_draft", post=BLOCKS_POST, title="Анонс")
    publish = {"draft_id": d["id"], "version": d["version"]}
    restore = _lose_answers(monkeypatch)
    res, _, text = call(client, "publish_post", **publish)
    assert res["isError"] and "мог уйти" in text
    restore()
    _age_keys(DAY_AND_HOUR)
    store.idempotency.purge()
    res, _, text = call(client, "publish_post", **publish)
    assert res["isError"] and "мог уйти" in text and "idempotency_key" in text
    assert sends(tg) == 1
    res, _, _ = call(client, "publish_post", **publish, idempotency_key="anons-2")
    assert not res.get("isError") and sends(tg) == 2


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
