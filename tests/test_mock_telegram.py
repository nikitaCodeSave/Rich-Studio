"""The fake Bot API's own controls for the browser checks (tests/e2e): /hold keeps answers back until /release."""

import threading
import time

import httpx

import mock_telegram
from conftest import FAKE_PORT

MOCK = f"http://127.0.0.1:{FAKE_PORT}"


def _send() -> float:
    """One sendRichMessage straight to the fake; returns how long its answer took."""
    t = time.monotonic()
    r = httpx.post(f"{MOCK}/bot1:x/sendRichMessage", json={"chat_id": 1, "rich_message": {"html": "x"}}, timeout=10)
    assert r.json()["ok"]
    return time.monotonic() - t


def _hold() -> None:
    httpx.post(f"{MOCK}/hold", json={"method": "sendRichMessage"}).raise_for_status()


def test_a_forgotten_release_holds_only_until_the_hold_runs_out(tg, monkeypatch):
    monkeypatch.setattr(mock_telegram, "HOLD_MAX", 0.5)
    start = time.monotonic()  # the hold's timer starts at /hold, not at the request
    _hold()
    _send()
    assert 0.35 <= time.monotonic() - start < 5  # answered once the hold ran out: not before, not never
    assert not tg.held and _send() < 5  # the hold is over: the next request goes straight through


def test_release_and_reset_let_a_held_request_through(tg, monkeypatch):
    monkeypatch.setattr(mock_telegram, "HOLD_MAX", 10)
    for let_go in (
        lambda: httpx.post(f"{MOCK}/release", json={"method": "sendRichMessage"}),
        tg.reset,  # from this thread, not the fake's: the hold is let go on the fake's own loop
    ):
        _hold()
        took: dict[str, float] = {}
        waiting = threading.Thread(target=lambda: took.update(s=_send()))  # noqa: B023 - joined in this round
        waiting.start()
        deadline = time.monotonic() + 5
        while not tg.calls and time.monotonic() < deadline:  # the request reached the fake and waits there
            time.sleep(0.01)
        let_go()
        waiting.join(5)
        assert not waiting.is_alive() and took["s"] < 5 and not tg.held
        tg.calls.clear()
        assert _send() < 1
        tg.calls.clear()
