"""The poll after a post and the first comment are written down before Telegram is asked (issue #98).

Like the post itself: the poll's row is "sending" and the comment's status is "sending" while the request runs, and
the request is in publisher.in_flight (a stop waits for it). A stop or a crash mid-request leaves "may have gone",
never no trace or the false "the forward never came" a day later, and nothing sends it again. A comment that failed
while the studio was off for longer than a day is in the health report for a day from its failure."""

import asyncio
import sqlite3
import time

import pytest

import settings
import store
import telegram
from services import health, kinds, listing, loops, publisher, scheduler, updates
from test_api import BLOCKS_POST, POLL

COMMENT = {"html": "<p>Ссылки из эфира</p>"}
FULL = "database or disk is full"


class Crash(BaseException):
    """The process dies right here: no handler runs, nothing more is written."""


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(loops, "STATE", loops.State(owner="me"))
    monkeypatch.setattr(publisher, "in_flight", publisher.InFlight())
    monkeypatch.setattr(publisher, "unrecorded", {})


def _count(tg, method: str, comment: bool = False) -> int:
    return len([c for c in tg.calls if c["method"] == method and ("reply_parameters" in c["body"]) == comment])


def _polls() -> list[dict]:
    return [p for p in store.list_posts() if p["kind"] == kinds.POLL]


def _recover() -> None:
    """The next process takes the lease: rows left "sending" by the one before."""
    loops.Loops([])._lead()


def _flagged() -> list[str]:
    return [p["id"] for p in health.posts().get("details", {}).get("posts", [])]


# ---------- the poll after the post ----------
def _poll_send(monkeypatch, then):
    """publisher.send for the poll after the post: Telegram gets the request, then `then(row)` runs instead of the
    answer coming back (`row`: the poll's record as it was when Telegram was asked). Returns what was seen."""
    real, seen = publisher.send, {}

    async def send(chat, post, kind=kinds.RICH):
        if kind != kinds.POLL:
            return await real(chat, post, kind)
        [row] = _polls()
        seen.update(row=row, in_flight=row["id"] in publisher.in_flight)
        res = await real(chat, post, kind)
        return await then(row) if then else res

    monkeypatch.setattr(publisher, "send", send)
    return seen


async def _hang(_row):
    await asyncio.Event().wait()


async def _crash(_row):
    raise Crash


async def _cancel_mid_request(coro, tg, method: str, comment: bool = False):
    """Run `coro`, and stop it (as the studio's shutdown does) once Telegram got the request and the answer is late."""
    t = asyncio.create_task(coro)
    for _ in range(300):
        if _count(tg, method, comment):
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t


def test_the_poll_after_the_post_is_written_down_before_telegram_is_asked(client, tg, monkeypatch):
    seen = _poll_send(monkeypatch, None)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "poll_after": POLL}})
    assert r.status_code == 200 and r.json()["warnings"] == []
    assert seen["row"]["status"] == "sending" and seen["in_flight"]
    [listed] = _polls()
    assert listed["id"] == seen["row"]["id"]  # the same row, now with the outcome
    poll = store.get_post(listed["id"])
    assert (poll["status"], poll["message_id"], poll["poll_id"]) == ("sent", tg.next_id, f"poll{tg.next_id}")
    assert poll["chat_num"] == -1001 and not publisher.in_flight


def test_a_stop_mid_poll_leaves_may_have_gone_and_nothing_sends_it_again(client, tg, monkeypatch):
    """Found in the review of #139: a stop mid-request left no row of the poll at all."""
    _poll_send(monkeypatch, _hang)
    asyncio.run(_cancel_mid_request(publisher.publish(["@c"], {**BLOCKS_POST, "poll_after": POLL}), tg, "sendPoll"))
    [poll] = _polls()
    assert poll["status"] == "failed" and poll["outcome_unknown"] is True
    assert publisher.POLL_MAY_HAVE_GONE in poll["error"] and "остановилась" in poll["error"]
    assert [p["status"] for p in store.list_posts() if p["kind"] == kinds.RICH] == ["sent"]
    assert not publisher.in_flight
    before = store.get_post(poll["id"])
    _recover()
    asyncio.run(scheduler.run_due_posts())
    assert _count(tg, "sendPoll") == 1 and store.get_post(poll["id"]) == before
    assert poll["id"] in _flagged()


def test_a_crash_mid_poll_is_may_have_gone_after_the_restart(client, tg, monkeypatch):
    _poll_send(monkeypatch, _crash)
    with pytest.raises(Crash):
        asyncio.run(publisher.publish(["@c"], {**BLOCKS_POST, "poll_after": POLL}))
    [left] = _polls()
    assert left["status"] == "sending"  # the trace a crash leaves: written before the request
    monkeypatch.setattr(publisher, "in_flight", publisher.InFlight())  # a new process knows of no send under way
    assert left["id"] in _flagged()
    _recover()
    poll = store.get_post(left["id"])
    assert poll["status"] == "failed" and poll["outcome_unknown"] is True and "мог уйти" in poll["error"]
    asyncio.run(scheduler.run_due_posts())
    assert _count(tg, "sendPoll") == 1


def test_the_scheduler_stopped_mid_poll_leaves_may_have_gone(client, tg, monkeypatch):
    store.add_post("@c", {**BLOCKS_POST, "poll_after": POLL}, status="scheduled", scheduled_at=int(time.time()) - 5)
    _poll_send(monkeypatch, _hang)
    asyncio.run(_cancel_mid_request(scheduler.run_due_posts(), tg, "sendPoll"))
    [poll] = _polls()
    assert poll["status"] == "failed" and poll["outcome_unknown"] is True
    assert [p["status"] for p in store.list_posts() if p["kind"] == kinds.RICH] == ["sent"]
    _recover()
    asyncio.run(scheduler.run_due_posts())
    assert _count(tg, "sendPoll") == 1 and _count(tg, "sendRichMessage") == 1


def test_a_poll_whose_row_cannot_be_written_is_not_sent(client, tg, monkeypatch):
    """Like the post: no row, no request."""
    real = store.add_post

    def add(chat, payload, *a, **kw):
        if kw.get("kind") == kinds.POLL:
            raise sqlite3.OperationalError(FULL)
        return real(chat, payload, *a, **kw)

    monkeypatch.setattr(store, "add_post", add)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "poll_after": POLL}})
    assert r.status_code == 200 and r.json()["post"]["status"] == "sent"
    [w] = r.json()["warnings"]
    assert "опрос после поста не отправлен" in w and FULL in w
    assert _count(tg, "sendPoll") == 0


# ---------- the first comment ----------
def _with_comment(client, monkeypatch) -> dict:
    monkeypatch.setattr(settings.get(), "updates_on", True)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "comment": COMMENT}}).json()
    assert r["post"]["comment_status"] == "waiting"
    return r


def _forward(r: dict) -> dict:
    return {
        "update_id": 9,
        "message": {
            "message_id": 77,
            "chat": {"id": -2002, "type": "supergroup"},
            "is_automatic_forward": True,
            "forward_origin": {"type": "channel", "chat": {"id": -1001}, "message_id": r["message"]["message_id"]},
        },
    }


def _comment_send(monkeypatch, pid: str, then):
    """telegram.call for the first comment: Telegram gets it, then `then` runs instead of the answer coming back."""
    real, seen = telegram.call, {}

    async def call(method, params=None, *a, **kw):
        if method != "sendRichMessage" or "reply_parameters" not in (params or {}):
            return await real(method, params, *a, **kw)
        seen.update(status=store.get_post(pid)["comment_status"], in_flight=pid in publisher.in_flight)
        res = await real(method, params, *a, **kw)
        return await then(None) if then else res

    monkeypatch.setattr(telegram, "call", call)
    return seen


def test_the_first_comment_is_marked_sending_before_telegram_is_asked(client, tg, monkeypatch):
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    seen = _comment_send(monkeypatch, pid, None)
    assert asyncio.run(updates.handle_update(_forward(r))) == "comment"
    assert seen == {"status": "sending", "in_flight": True}
    p = store.get_post(pid)
    assert (p["comment_status"], p["comment_message_id"]) == ("sent", tg.next_id) and not publisher.in_flight


def test_a_stop_mid_comment_is_may_have_gone_not_a_forward_that_never_came(client, tg, monkeypatch):
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    _comment_send(monkeypatch, pid, _hang)

    asyncio.run(_cancel_mid_request(updates.handle_update(_forward(r)), tg, "sendRichMessage", comment=True))
    p = store.get_post(pid)
    assert p["status"] == "sent" and p["outcome_unknown"] is False  # the post itself is in the channel
    assert p["comment_status"] == "failed" and "мог уйти" in p["error"] and "остановилась" in p["error"]
    assert not publisher.in_flight
    # a day later: the expiry of waiting comments leaves it as it is
    assert store.expire_comments(int(time.time()) + 1, "первый комментарий: пересылка не пришла") == 0
    _recover()
    assert asyncio.run(updates.handle_update(_forward(r))) is None  # the same forward again: nothing to send
    assert store.get_post(pid) == p and _count(tg, "sendRichMessage", comment=True) == 1
    assert pid in _flagged()


def test_a_crash_mid_comment_is_may_have_gone_after_the_restart(client, tg, monkeypatch):
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    _comment_send(monkeypatch, pid, _crash)
    with pytest.raises(Crash):
        asyncio.run(updates.handle_update(_forward(r)))
    assert store.get_post(pid)["comment_status"] == "sending"
    monkeypatch.setattr(publisher, "in_flight", publisher.InFlight([pid]))
    assert pid not in _flagged()  # being sent this second: normal
    monkeypatch.setattr(publisher, "in_flight", publisher.InFlight())  # a new process
    assert pid in _flagged()  # left behind by a crash
    assert store.expire_comments(int(time.time()) + 1, "первый комментарий: пересылка не пришла") == 0
    _recover()
    p = store.get_post(pid)
    assert p["comment_status"] == "failed" and "мог уйти" in p["error"] and "не пришла" not in p["error"]
    assert p["status"] == "sent" and p["outcome_unknown"] is False
    assert asyncio.run(updates.handle_update(_forward(r))) is None
    assert _count(tg, "sendRichMessage", comment=True) == 1
    assert pid in _flagged()


def test_a_comment_whose_sent_cannot_be_written_stays_sending_and_is_never_resent(client, tg, monkeypatch):
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    real = store.update_post

    def update(post_id, **fields):
        if fields.get("comment_status") == "sent":
            raise sqlite3.OperationalError(FULL)
        return real(post_id, **fields)

    monkeypatch.setattr(store, "update_post", update)
    assert asyncio.run(updates.handle_update(_forward(r))) == "comment"
    assert store.get_post(pid)["comment_status"] == "sending" and not publisher.in_flight
    assert pid in _flagged() and "send.unrecorded" in health.posts()["action"]
    assert asyncio.run(updates.handle_update(_forward(r))) is None
    _recover()
    assert store.get_post(pid)["comment_status"] == "failed"
    assert _count(tg, "sendRichMessage", comment=True) == 1


def test_the_list_of_posts_tells_a_comment_under_way_from_an_unconfirmed_one(client, tg, monkeypatch):
    """Issue #158: "sending" is a request under way (the editor says «отправляется») or a comment nobody sends any
    more, here one Telegram took whose record the database refused («не подтверждён»)."""
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]

    def listed() -> tuple:
        [p] = [p for p in client.get("/api/posts").json() if p["id"] == pid]
        return p["comment_status"], p["comment_in_flight"]

    real_call, real_update, during = telegram.call, store.update_post, {}

    async def call(method, params=None, *a, **kw):
        if method == "sendRichMessage" and "reply_parameters" in (params or {}):
            [p] = [p for p in listing.list_posts() if p["id"] == pid]
            during["listed"] = p["comment_status"], p["comment_in_flight"]
        return await real_call(method, params, *a, **kw)

    def update(post_id, **fields):
        if fields.get("comment_status") == "sent":
            raise sqlite3.OperationalError(FULL)
        return real_update(post_id, **fields)

    assert listed() == ("waiting", False)
    monkeypatch.setattr(telegram, "call", call)
    monkeypatch.setattr(store, "update_post", update)
    assert asyncio.run(updates.handle_update(_forward(r))) == "comment"
    assert during == {"listed": ("sending", True)}
    assert listed() == ("sending", False)


def test_a_comment_sent_while_the_scheduler_sends_the_poll_keeps_its_post_in_flight(client, tg, monkeypatch):
    """Review of #156: the scheduler and getUpdates hold the same post id. The forward comes while the scheduler sends
    the poll after the post, the scheduler finishes first: the comment still being sent must stay in flight (not
    stuck in the health report), and a stop waits for it instead of cutting it off as "may have gone"."""
    monkeypatch.setattr(settings.get(), "updates_on", True)
    monkeypatch.setattr(loops, "SHUTDOWN_GRACE", 5)
    post = {**BLOCKS_POST, "comment": COMMENT, "poll_after": POLL}
    pid = store.add_post("@c", post, status="scheduled", scheduled_at=int(time.time()) - 5)["id"]
    poll_asked, poll_gate, comment_asked, comment_gate = (asyncio.Event() for _ in range(4))
    real_send, real_call = publisher.send, telegram.call

    async def send(chat, body, kind=kinds.RICH):
        res = await real_send(chat, body, kind)
        if kind == kinds.POLL:
            poll_asked.set()
            await poll_gate.wait()
        return res

    async def call(method, params=None, *a, **kw):
        res = await real_call(method, params, *a, **kw)
        if method == "sendRichMessage" and "reply_parameters" in (params or {}):
            comment_asked.set()
            await comment_gate.wait()
        return res

    monkeypatch.setattr(publisher, "send", send)
    monkeypatch.setattr(telegram, "call", call)

    async def scenario():
        scheduled = asyncio.create_task(scheduler.run_due_posts())
        await asyncio.wait_for(poll_asked.wait(), 5)
        forward = _forward({"message": {"message_id": store.get_post(pid)["message_id"]}})
        comment = asyncio.create_task(updates.handle_update(forward))
        await asyncio.wait_for(comment_asked.wait(), 5)
        poll_gate.set()
        await scheduled  # the scheduler is done with the post, its comment is not
        assert store.get_post(pid)["comment_status"] == "sending" and pid in publisher.in_flight
        assert pid not in _flagged()
        stopping = asyncio.create_task(loops.Loops([]).stop())
        await asyncio.sleep(0.3)
        assert not stopping.done()  # waits for the comment
        comment_gate.set()
        assert await comment == "comment"
        await asyncio.wait_for(stopping, 5)

    asyncio.run(scenario())
    p = store.get_post(pid)
    assert (p["status"], p["comment_status"]) == ("sent", "sent") and pid not in publisher.in_flight


def test_in_flight_counts_its_owners():
    sending = publisher.InFlight()
    sending.add("p")
    sending.add("p")
    sending.discard("p")
    assert "p" in sending and list(sending) == ["p"] and len(sending) == 1
    sending.discard("p")
    sending.discard("p")  # one discard too many is harmless, as with a set
    assert "p" not in sending and not sending


def test_a_comment_is_claimed_only_while_it_waits(client, tg, monkeypatch):
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    assert store.claim_comment(pid) is True
    assert store.claim_comment(pid) is False  # taken already
    store.fail_comment(pid, "первый комментарий: отказ")
    assert store.claim_comment(pid) is False
    assert store.get_post(pid)["comment_status"] == "failed"
    without = store.add_post("@c", BLOCKS_POST, status="sent", message_id=1)["id"]
    assert store.claim_comment(without) is False and store.get_post(without)["comment_status"] is None


def test_a_comment_failure_is_added_to_the_error_of_the_post(client, tg, monkeypatch):
    """The scheduler keeps the warnings of a sent post in its error: the comment's failure must not replace them."""
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    store.update_post(pid, error="не удалось закрепить: нет прав")
    tg.fail["sendRichMessage"] = "Bad Request: not enough rights to send messages"
    asyncio.run(updates.handle_update(_forward(r)))
    p = store.get_post(pid)
    assert p["comment_status"] == "failed"
    assert (
        p["error"]
        == "не удалось закрепить: нет прав; первый комментарий: Bad Request: not enough rights to send messages"
    )


def test_the_warnings_of_a_scheduled_post_keep_a_comment_failure_written_meanwhile(client, tg, monkeypatch):
    """Issue #158, the other way round: the forward comes while the scheduler pins the post and the comment fails in
    that second. The pin's warning is written after the failure and must not replace it."""
    monkeypatch.setattr(settings.get(), "updates_on", True)
    post = {**BLOCKS_POST, "comment": COMMENT, "pin": True}
    pid = store.add_post("@c", post, status="scheduled", scheduled_at=int(time.time()) - 5)["id"]
    tg.fail["pinChatMessage"] = "Bad Request: not enough rights to pin a message"
    real, comment = telegram.call, {}

    async def call(method, params=None, *a, **kw):
        if method == "pinChatMessage":  # the comment waits now, and its forward comes before the pin's answer
            refused = {"ok": False, "error_code": 400, "description": "Bad Request: not enough rights to send messages"}
            tg.queue["sendRichMessage"] = [refused]
            forward = _forward({"message": {"message_id": store.get_post(pid)["message_id"]}})
            comment["handled"] = await updates.handle_update(forward)
        return await real(method, params, *a, **kw)

    monkeypatch.setattr(telegram, "call", call)
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(pid)
    assert comment == {"handled": "comment"} and (p["status"], p["comment_status"]) == ("sent", "failed")
    assert p["error"] == (
        "первый комментарий: Bad Request: not enough rights to send messages; "
        "не удалось закрепить: Bad Request: not enough rights to pin a message"
    )


def _fail_comment_days_ago(pid: str, days: float) -> None:
    with store.conn() as c:
        c.execute("update posts set comment_failed_at=? where id=?", (int(time.time() - days * health.DAY), pid))


def test_a_comment_that_expired_while_the_studio_was_off_is_in_the_report(client, tg, monkeypatch):
    """Off for three days: the wait ended two days ago, the comment fails only at the first getUpdates after the
    start, and the report's day counts from that moment (#111 counted it from the end of the wait)."""
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    store.update_post(pid, sent_at=int(time.time()) - 3 * health.DAY)
    assert store.expire_comments(int(time.time()) - updates.COMMENT_WAIT, "первый комментарий: не пришла") == 1
    assert health.posts()["status"] == "warn" and _flagged() == [pid]
    _fail_comment_days_ago(pid, 1.01)  # a day after the failure: the studio has nothing to resolve it with
    assert health.posts()["status"] == "ok"


def test_a_comment_cut_off_mid_send_leaves_the_report_a_day_after(client, tg, monkeypatch):
    r = _with_comment(client, monkeypatch)
    pid = r["post"]["id"]
    _comment_send(monkeypatch, pid, _crash)
    with pytest.raises(Crash):
        asyncio.run(updates.handle_update(_forward(r)))
    monkeypatch.setattr(publisher, "in_flight", publisher.InFlight())
    _recover()
    assert _flagged() == [pid]
    _fail_comment_days_ago(pid, 1.01)
    assert health.posts()["status"] == "ok"


def test_comments_failed_before_the_column_keep_the_window_they_had():
    """Rows written before the step: the end of the wait for the forward, the latest moment they could fail."""
    from store import db
    from test_migrations import _database_at

    files = sorted(db.MIGRATIONS_DIR.glob("*.sql"))
    old = _database_at(next(i for i, f in enumerate(files) if f.name.endswith("_comment_failed_at.sql")))
    old.executemany(
        "insert into posts(id, chat_id, payload, status, sent_at, created_at, comment_status) "
        "values (?, '@c', '{}', 'sent', ?, 5, ?)",
        [("failed", 1000, "failed"), ("no sent_at", None, "failed"), ("sent", 1000, "sent"), ("none", 1000, None)],
    )
    old.commit()
    old.close()
    db.init()
    with store.conn() as c:
        at = dict(c.execute("select id, comment_failed_at from posts").fetchall())
    day = updates.COMMENT_WAIT
    assert at == {"failed": 1000 + day, "no sent_at": 5 + day, "sent": None, "none": None}
