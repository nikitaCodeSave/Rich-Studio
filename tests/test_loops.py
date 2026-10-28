"""Background loops: one process runs them (lease in SQLite), and a stop never cuts a send short."""

import asyncio
import sqlite3
import time

import pytest

import settings
import store
from services import health, loops, publisher, scheduler
from test_api import BLOCKS_POST


@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.setattr(loops, "STATE", loops.State(owner="me"))
    monkeypatch.setattr(publisher, "in_flight", set())
    return loops.STATE


def test_lease_is_held_by_one_owner_until_it_runs_out():
    assert store.take_lease("loops", "a", 60)
    assert store.take_lease("loops", "a", 60)  # renewal
    assert not store.take_lease("loops", "b", 60)
    assert store.lease_holder("loops") == "a"
    with store.conn() as c:  # a's process died: its lease runs out
        c.execute("update meta set value=json_set(value, '$.until', 0) where key='lease:loops'")
    assert store.take_lease("loops", "b", 60)
    store.drop_lease("loops", "a")  # not a's any more: nothing happens
    assert store.lease_holder("loops") == "b"
    store.drop_lease("loops", "b")
    assert store.lease_holder("loops") is None


def test_startup_recovery_keeps_what_this_process_is_sending():
    mine = store.add_post("@c", BLOCKS_POST, status="sending")["id"]
    left = store.add_post("@c", BLOCKS_POST, status="sending")["id"]
    assert store.recover_stale_sending(keep={mine}) == 1
    assert store.get_post(mine)["status"] == "sending"
    assert store.get_post(left)["status"] == "failed"


async def _rounds(monkeypatch, n: int):
    """Let the lease keeper run n rounds, then park it."""
    gate = asyncio.Event()
    count = {"n": 0}

    async def fake_sleep(_s):
        count["n"] += 1
        if count["n"] >= n:
            await gate.wait()

    monkeypatch.setattr(loops, "_sleep", fake_sleep)
    for _ in range(50):
        if count["n"] >= n:
            return
        await asyncio.sleep(0.01)


def test_standby_while_another_process_leads_then_takes_over(fresh_state, monkeypatch):
    started = []

    async def work():
        started.append(1)
        await asyncio.Event().wait()

    async def scenario():
        store.take_lease(loops.LEASE_KEY, "other", 60)
        left = store.add_post("@c", BLOCKS_POST, status="sending")["id"]  # the other process is sending it
        runner = loops.Loops([work])
        runner.start()
        await _rounds(monkeypatch, 1)
        assert (fresh_state.role, fresh_state.holder, started) == ("standby", "other", [])
        assert store.get_post(left)["status"] == "sending"  # a standby never touches another process's sends
        monkeypatch.setattr(settings.get(), "background", True)
        assert health.loops()["status"] == "warn"
        monkeypatch.setattr(settings.get(), "background", False)

        with store.conn() as c:  # the other process stopped renewing
            c.execute("update meta set value=json_set(value, '$.until', 0) where key='lease:loops'")
        runner.keeper.cancel()
        runner.start()
        await _rounds(monkeypatch, 1)
        await asyncio.sleep(0)
        assert fresh_state.role == "leader" and started == [1]
        assert store.get_post(left)["status"] == "failed"  # now nobody sends it: it was cut off
        await runner.stop()
        assert fresh_state.role == "off" and store.lease_holder(loops.LEASE_KEY) is None

    asyncio.run(scenario())


def test_stop_waits_for_sends_under_way_and_takes_no_new_posts(fresh_state, monkeypatch, tg):
    monkeypatch.setattr(loops, "SHUTDOWN_GRACE", 5)

    async def scenario():
        runner = loops.Loops([])
        publisher.in_flight.add("busy")

        async def finish():
            await asyncio.sleep(0.3)
            publisher.in_flight.discard("busy")

        t = asyncio.create_task(finish())
        started = time.monotonic()
        await runner.stop()
        await t
        assert time.monotonic() - started >= 0.25  # waited for the send instead of cancelling it
        assert fresh_state.stopping
        due = store.add_post("@c", BLOCKS_POST, status="scheduled", scheduled_at=int(time.time()) - 5)["id"]
        await scheduler.run_due_posts()
        assert store.get_post(due)["status"] == "scheduled"  # left for the next start

    asyncio.run(scenario())
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_scheduler_cancelled_mid_send_marks_may_have_gone(fresh_state, monkeypatch):
    pid = store.add_post("@c", BLOCKS_POST, status="scheduled", scheduled_at=int(time.time()) - 5)["id"]

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
    p = store.get_post(pid)
    assert p["status"] == "failed" and publisher.MAY_HAVE_GONE in p["error"]
    assert not publisher.in_flight


def test_database_uses_wal():
    """Readers (health, agents) never wait for the scheduler's writes."""
    with store.conn() as c:
        assert c.execute("pragma journal_mode").fetchone()[0] == "wal"


def test_database_outage_hands_the_loops_back_once_it_is_over(fresh_state, monkeypatch):
    """Renewal fails longer than the lease: the loops stop, and the keeper survives to start them again."""
    started = []
    clock = {"t": 0.0}
    down = {"on": False}
    take, holder = store.take_lease, store.lease_holder

    def broken(real):
        def call(*a, **kw):
            if down["on"]:
                raise sqlite3.OperationalError("database or disk is full")
            return real(*a, **kw)

        return call

    monkeypatch.setattr(store, "take_lease", broken(take))
    monkeypatch.setattr(store, "lease_holder", broken(holder))
    monkeypatch.setattr(loops, "_now", lambda: clock["t"])
    rounds = asyncio.Queue()

    async def step(_s):
        clock["t"] += loops.RENEW
        await rounds.get()

    monkeypatch.setattr(loops, "_sleep", step)

    async def work():
        started.append(1)
        await asyncio.Event().wait()

    async def go(n):
        """Let the keeper run n more rounds (0: just the first one)."""
        for _ in range(n + 1):
            for _ in range(5):
                await asyncio.sleep(0)
            if n:
                await rounds.put(1)
                n -= 1

    async def scenario():
        runner = loops.Loops([work])
        runner.start()
        await go(0)
        assert fresh_state.role == "leader" and started == [1]
        down["on"] = True
        await go(1)  # 20 s without renewal: the lease still runs, keep leading
        assert fresh_state.role == "leader"
        await go(1)  # 40 s: another process may take over soon, stop the loops
        assert fresh_state.role == "standby" and fresh_state.holder is None
        await go(3)
        assert not runner.keeper.done()
        down["on"] = False
        await go(1)
        assert fresh_state.role == "leader" and started == [1, 1]
        await runner.stop()

    asyncio.run(scenario())
