"""Transport to the Bot API: retries on 429, token never in logs."""

import logging

import telegram.client as client_mod
from test_api import BLOCKS_POST, post_json


def _too_many(retry_after):
    return {
        "ok": False,
        "error_code": 429,
        "description": f"Too Many Requests: retry after {retry_after}",
        "parameters": {"retry_after": retry_after},
    }


def test_short_429_is_retried_once_and_the_post_goes_out(client, tg, monkeypatch):
    waits = []

    async def no_sleep(sec):
        waits.append(sec)

    monkeypatch.setattr(client_mod, "_sleep", no_sleep)
    tg.queue["sendRichMessage"] = [_too_many(2)]
    r = post_json(client, "/api/send", {"post": BLOCKS_POST})
    assert r.status_code == 200, r.text
    assert waits == [2]
    assert len([c for c in tg.calls if c["method"] == "sendRichMessage"]) == 2


def test_long_429_is_reported_not_waited(client, tg, monkeypatch):
    async def fail_sleep(sec):
        raise AssertionError("must not wait this long")

    monkeypatch.setattr(client_mod, "_sleep", fail_sleep)
    tg.queue["sendRichMessage"] = [_too_many(600)]
    r = post_json(client, "/api/send", {"post": BLOCKS_POST})
    assert r.status_code == 502 and "Too Many Requests" in r.json()["detail"]


def test_token_is_hidden_in_httpx_logs(caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s"',
            "POST",
            "https://api.telegram.org/bot123:ABC-def_9/sendMessage",
            "HTTP/1.1 200 OK",
        )
    assert "ABC-def_9" not in caplog.text and "/bot<token>/sendMessage" in caplog.text


def test_token_is_hidden_when_httpx_logs_a_url_object(caplog):
    import httpx

    with caplog.at_level(logging.INFO, logger="httpx"):  # what httpx really passes: an httpx.URL, not a str
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"',
            "POST",
            httpx.URL("https://api.telegram.org/bot123:ABC-def_9/sendMessage"),
            "HTTP/1.1",
            200,
            "OK",
        )
    assert "ABC-def_9" not in caplog.text and "/bot<token>/sendMessage" in caplog.text
