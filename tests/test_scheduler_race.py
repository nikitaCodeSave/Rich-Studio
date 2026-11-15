"""The scheduler goes through the queue it read once, waiting for each send. Whatever the person does with a post
after that reading (moves it, cancels it) wins: the post goes out only at its time as it is now."""

import asyncio
import sqlite3
import time

import pytest

import store
import telegram
from services import publisher, scheduler
from test_reliability import schedule, sends

DAY = 24 * 3600


def read_queue_then(monkeypatch, *actions) -> None:
    """The scheduler reads the queue, then the person acts before it gets to the posts."""
    stale = store.due_posts()
    for act in actions:
        act()
    monkeypatch.setattr(store, "due_posts", lambda: stale)


def move(client, pid: str, at: int):
    def act():
        r = client.post(f"/api/posts/{pid}/reschedule", json={"at": at})
        assert r.status_code == 200, r.text

    return act


def cancel(client, pid: str):
    def act():
        r = client.delete(f"/api/posts/{pid}")
        assert r.status_code == 200, r.text

    return act


def test_post_moved_to_tomorrow_after_the_queue_was_read_waits(client, tg, monkeypatch):
    pid = schedule(client)["id"]
    tomorrow = int(time.time()) + DAY
    read_queue_then(monkeypatch, move(client, pid, tomorrow))
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(pid)
    assert p["status"] == "scheduled" and p["scheduled_at"] == tomorrow
    assert sends(tg) == 0


def test_post_moved_while_an_earlier_one_is_being_sent_waits(client, tg, monkeypatch):
    """The real race: the pass waits for Telegram on the first post, the person moves the second meanwhile."""
    now = int(time.time())
    schedule(client, at=now - 120, post={"rich_message": {"markdown": "первый"}})
    second = schedule(client, at=now - 60, post={"rich_message": {"markdown": "второй"}})["id"]
    tomorrow = now + DAY
    real = telegram.call
    moved = []

    async def person_moves_the_second(method, params=None, timeout=60):
        if method == "sendRichMessage" and not moved:
            moved.append(second)
            move(client, second, tomorrow)()
        return await real(method, params, timeout)

    monkeypatch.setattr(telegram, "call", person_moves_the_second)
    asyncio.run(scheduler.run_due_posts())
    assert [c["body"]["rich_message"]["markdown"] for c in tg.calls if c["method"] == "sendRichMessage"] == ["первый"]
    p = store.get_post(second)
    assert p["status"] == "scheduled" and p["scheduled_at"] == tomorrow


def test_late_post_moved_after_the_queue_was_read_is_not_missed(client, tg, monkeypatch):
    late = int(time.time()) - 3 * 3600  # far past SCHEDULE_LATE_MIN: the studio was off
    moved = schedule(client, at=late)["id"]
    left = schedule(client, at=late)["id"]
    tomorrow = int(time.time()) + DAY
    read_queue_then(monkeypatch, move(client, moved, tomorrow))
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(moved)
    assert p["status"] == "scheduled" and p["scheduled_at"] == tomorrow and p["error"] is None
    # the one nobody moved is still missed, not sent late (ADR 0005)
    p = store.get_post(left)
    assert p["status"] == "missed" and "3 ч" in p["error"]
    assert sends(tg) == 0


def test_late_post_moved_to_now_after_the_queue_was_read_goes_out_on_the_next_pass(client, tg, monkeypatch):
    """The person asked for now: the post is not late any more, the next reading of the queue sends it."""
    pid = schedule(client, at=int(time.time()) - 3 * 3600)["id"]
    read_queue_then(monkeypatch, move(client, pid, int(time.time())))
    asyncio.run(scheduler.run_due_posts())
    assert store.get_post(pid)["status"] != "missed"
    monkeypatch.setattr(store, "due_posts", store.posts.due_posts)  # the next pass reads the queue anew
    asyncio.run(scheduler.run_due_posts())
    assert store.get_post(pid)["status"] == "sent"
    assert sends(tg) == 1


def test_database_error_on_a_late_post_leaves_it_waiting(client, tg, monkeypatch):
    """Nothing was sent, so nothing may look "may have gone": the post stays scheduled and can be moved."""
    pid = schedule(client, at=int(time.time()) - 3 * 3600)["id"]

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(store, "mark_missed", locked)
        with pytest.raises(sqlite3.OperationalError):
            asyncio.run(scheduler.run_due_posts())
    store.recover_stale_sending()  # what the next lease does first
    assert store.get_post(pid)["status"] == "scheduled" and not publisher.in_flight
    asyncio.run(scheduler.run_due_posts())  # the database is back: missed, as ADR 0005 says
    assert store.get_post(pid)["status"] == "missed"
    assert sends(tg) == 0


def test_post_cancelled_after_the_queue_was_read_is_not_sent(client, tg, monkeypatch):
    due = schedule(client)["id"]
    late = schedule(client, at=int(time.time()) - 3 * 3600)["id"]
    read_queue_then(monkeypatch, cancel(client, due), cancel(client, late))
    asyncio.run(scheduler.run_due_posts())
    assert store.get_post(due)["status"] == "cancelled"
    assert store.get_post(late)["status"] == "cancelled"
    assert sends(tg) == 0
