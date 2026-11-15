"""Secrets never leave the studio (redact.py): the bot token, STUDIO_API_KEY and named rs_ tokens are masked in API
answers, saved post errors, MCP errors, /api/updates and the log, whatever echoed them (a proxy, a wrong TG_API_BASE,
an own Bot API server)."""

import asyncio
import json
import logging
import time

import httpx
import pytest
from starlette.responses import HTMLResponse

import logs
import redact
import settings
import store
import telegram
from services import scheduler, updates
from test_api import BLOCKS_POST, POLL
from test_mcp import call, rpc

SECRET = "AAH" + "x" * 32  # the part after the colon: what must not show up anywhere
TOKEN = "1234567890:" + SECRET
KEY = "k3y-" + "Q" * 32  # a server-length STUDIO_API_KEY
NAMED = "rs_" + "N" * 43  # like cli.py: rs_ + token_urlsafe(32)


@pytest.fixture
def bot_token(monkeypatch):
    monkeypatch.setattr(settings.get(), "bot_token", TOKEN)
    return TOKEN


def _gateway_echo() -> dict:
    """A 502 that repeats the request URL, as the issue describes it."""
    return {
        "ok": False,
        "error_code": 502,
        "description": f"Bad Gateway: upstream https://api.telegram.org/bot{TOKEN}/sendRichMessage timed out",
    }


def _clean(text: str) -> bool:
    return SECRET not in text and KEY not in text and NAMED not in text


# ---------- the issue's acceptance test: a 502 with the token in its description ----------
def test_token_in_a_telegram_502_reaches_no_answer_no_saved_error_no_mcp(client, tg, bot_token):
    tg.queue["sendRichMessage"] = [_gateway_echo(), _gateway_echo()]
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 502
    assert _clean(r.text) and "/bot<token>/sendRichMessage" in r.text and "Bad Gateway" in r.text
    [row] = store.list_posts()
    assert row["status"] == "failed" and _clean(row["error"]) and "<token>" in row["error"]
    assert _clean(client.get("/api/posts").text)

    res, _, text = call(client, "publish_post", post=BLOCKS_POST, chat_id="@other")
    assert res["isError"] and _clean(text) and "<token>" in text
    assert all(_clean(p["error"] or "") for p in store.list_posts())


def test_token_in_a_getupdates_error_stays_out_of_api_updates(client, tg, bot_token, monkeypatch):
    async def stop(sec):
        raise asyncio.CancelledError

    monkeypatch.setattr(updates, "_sleep", stop)
    tg.queue["getUpdates"] = [_gateway_echo()]
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(updates.loop())
        assert updates.STATUS.state == "error"
        for path in ("/api/updates", "/api/config"):
            text = client.get(path).text
            assert _clean(text) and "<token>" in text, path
        _, status, _ = call(client, "studio_status")
        assert _clean(json.dumps(status)) and "<token>" in status["updates"]["error"]

        # an error that never went through the transport (whatever an exception says) is masked on the way out
        async def reset(method, params=None, timeout=60):
            raise RuntimeError(f"connection to https://api.telegram.org/bot{TOKEN}/getUpdates reset")

        monkeypatch.setattr(updates.tg, "call", reset)
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(updates.loop())
        text = client.get("/api/updates").text
        assert _clean(text) and "RuntimeError" in text and "<token>" in text
    finally:
        updates.reset_status()


def test_refusal_that_names_the_token_is_masked_and_stays_readable(client, tg, bot_token):
    tg.fail["sendRichMessage"] = f"Bad Request: can't load https://x.example/bot{TOKEN}/file"
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 422
    assert r.json()["detail"] == "Telegram: Bad Request: can't load https://x.example/bot<token>/file"
    assert _clean(store.list_posts()[0]["error"])
    with pytest.raises(telegram.TelegramError) as e:  # the raw answer goes up masked too
        asyncio.run(telegram.call("sendRichMessage", {"chat_id": 1}))
    assert _clean(json.dumps(e.value.raw)) and e.value.code == 400


def test_a_network_error_that_names_the_url_is_masked_in_the_answer_and_the_saved_post(client, tg, monkeypatch):
    async def timeout(method, params=None, timeout=60):
        raise httpx.ReadTimeout(f"timed out: https://api.telegram.org/bot{TOKEN}/{method}")

    monkeypatch.setattr(telegram, "call", timeout)
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 502 and "ReadTimeout" in r.text and _clean(r.text)
    assert _clean(store.list_posts()[0]["error"]) and "мог уйти" in store.list_posts()[0]["error"]
    res, _, text = call(client, "publish_post", post=BLOCKS_POST, chat_id="@other")
    assert res["isError"] and _clean(text)
    with store.conn() as c:  # the "may have gone" kept for a retry with the same key
        saved = [r[0] for r in c.execute("select response from idempotency where status='uncertain'")]
    assert saved and all(_clean(r) for r in saved)


def test_api_key_and_named_token_echoed_by_telegram_are_masked(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", KEY)
    tg.fail["sendRichMessage"] = f"Bad Request: header X-API-Key: {KEY}, Authorization: Bearer {NAMED}"
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers={"X-API-Key": KEY})
    assert r.status_code == 422 and _clean(r.text)
    assert "<api-key>" in r.text and "<rs-token>" in r.text and "Bad Request: header X-API-Key" in r.text
    assert _clean(store.list_posts()[0]["error"])
    args = {"name": "publish_post", "arguments": {"post": BLOCKS_POST, "chat_id": "@other"}}
    res = rpc(client, "tools/call", args, headers={"X-API-Key": KEY})["result"]
    assert res["isError"] and _clean(res["content"][0]["text"])


def test_proxy_page_with_an_url_encoded_token_is_masked(client, tg, bot_token):
    page = f"<pre>Cannot POST /bot{TOKEN.replace(':', '%3A')}/getMe</pre>"
    tg.queue["getMe"] = [HTMLResponse(page, status_code=404)]
    r = client.post("/api/raw/getMe", json={})
    assert r.status_code == 502 and _clean(r.text) and "/bot<token>/getMe" in r.text


# ---------- a network error whose text repeats the request (an echo server at a wrong TG_API_BASE) ----------
def _echoing_server(monkeypatch, methods: set[str], chats: set[str] | None = None) -> None:
    """httpx puts the server's status line into RemoteProtocolError: an echo server answers with the request line."""
    import telegram.client as tc

    real = tc._request

    async def request(url, params, files, timeout):
        method = url.rsplit("/", 1)[1]
        if method in methods and (chats is None or params.get("chat_id") in chats):
            raise httpx.RemoteProtocolError(f"illegal status line: bytearray(b'POST /bot{TOKEN}/{method} HTTP/1.1')")
        return await real(url, params, files, timeout)

    monkeypatch.setattr(tc, "_request", request)


def test_network_error_text_is_masked_in_a_200_and_its_replay(client, tg, bot_token, monkeypatch):
    _echoing_server(monkeypatch, {"sendRichMessage"}, {"@b"})
    h = {"Idempotency-Key": "two-channels"}
    body = {"post": BLOCKS_POST, "chat_ids": ["@a", "@b"]}
    r = client.post("/api/send", json=body, headers=h)
    assert r.status_code == 200 and [x["ok"] for x in r.json()["results"]] == [True, False]
    assert _clean(r.text) and "RemoteProtocolError" in r.json()["results"][1]["error"]
    with store.conn() as c:
        saved = [x[0] for x in c.execute("select response from idempotency where status='done'")]
    assert saved and all(_clean(x) for x in saved)
    again = client.post("/api/send", json=body, headers=h)
    assert again.status_code == 200 and _clean(again.text)

    _, data, text = call(client, "publish_post", post=BLOCKS_POST, chat_ids=["@a", "@b"])
    assert data and _clean(text) and "<token>" in text


def test_network_error_text_after_the_send_is_masked_in_the_warnings(client, tg, bot_token, monkeypatch):
    _echoing_server(monkeypatch, {"pinChatMessage", "sendPoll"})
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "pin": True, "poll_after": POLL}})
    assert r.status_code == 200
    warnings = r.json()["warnings"]
    assert len(warnings) == 2 and all("<token>" in w for w in warnings) and _clean(r.text)
    assert all(_clean(p["error"] or "") for p in store.list_posts())


def test_network_error_after_a_scheduled_send_is_masked_in_the_saved_error(client, tg, bot_token, monkeypatch):
    """The scheduler adds the warnings to the post's error by store.add_error, not update_post (issue #158)."""
    _echoing_server(monkeypatch, {"pinChatMessage"})
    pid = store.add_post("@c", {**BLOCKS_POST, "pin": True}, status="scheduled", scheduled_at=int(time.time()))["id"]
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(pid)
    assert p["status"] == "sent" and "не удалось закрепить" in p["error"]
    assert _clean(p["error"]) and "/bot<token>/pinChatMessage" in p["error"]


def test_network_error_of_the_first_comment_is_masked_in_the_saved_error(client, tg, bot_token, monkeypatch):
    """The comment's failure is added to the post's error by store.fail_comment, not update_post (issue #98)."""
    monkeypatch.setattr(settings.get(), "updates_on", True)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "comment": {"html": "<p>x</p>"}}}).json()

    async def timeout(method, params=None, timeout=60):
        raise httpx.ReadTimeout(f"timed out: https://api.telegram.org/bot{TOKEN}/{method}")

    monkeypatch.setattr(telegram, "call", timeout)
    forward = {
        "update_id": 9,
        "message": {
            "message_id": 77,
            "chat": {"id": -2002},
            "is_automatic_forward": True,
            "forward_origin": {"type": "channel", "chat": {"id": -1001}, "message_id": r["message"]["message_id"]},
        },
    }
    assert asyncio.run(updates.handle_update(forward)) == "comment"
    p = store.get_post(r["post"]["id"])
    assert p["comment_status"] == "failed" and "мог уйти" in p["error"]
    assert _clean(p["error"]) and "/bot<token>/sendRichMessage" in p["error"]
    assert _clean(client.get("/api/posts").text)


# ---------- the second line: texts that never went through the transport ----------
def test_answers_mask_what_the_transport_did_not_see(client, tg, monkeypatch):
    async def refused(method, params=None, timeout=60):
        if method == "getMe":
            raise telegram.NoAnswer(f"HTTP 502 from https://api.telegram.org/bot{TOKEN}/getMe")
        raise telegram.TelegramError(f"Bad Request: echo https://api.telegram.org/bot{TOKEN}/{method}", 400)

    monkeypatch.setattr(telegram, "call", refused)
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 422 and _clean(r.text) and "Bad Request: echo" in r.text
    res, _, text = call(client, "publish_post", post=BLOCKS_POST, chat_id="@other")
    assert res["isError"] and _clean(text) and "Bad Request: echo" in text
    r = client.post("/api/raw/getMe", json={})
    assert r.status_code == 502 and _clean(r.text) and "могло выполниться" in r.text


def test_page_is_masked_before_it_is_cut(client, tg, bot_token):
    # not after /bot, and the 300-character limit falls inside the token: cut first, and its head would stay
    page = "x" * (300 - len("1234567890:") - 10) + TOKEN + " echoed"
    tg.queue["getMe"] = [HTMLResponse(page, status_code=404)]
    r = client.post("/api/raw/getMe", json={})
    assert r.status_code == 502 and ":AAH" not in r.text


# ---------- what is masked and what is not ----------
def test_url_encoded_token_is_masked_as_set_and_by_its_shape(monkeypatch):
    monkeypatch.setattr(settings.get(), "bot_token", "123:TEST")  # the tests' token: no real shape
    assert redact.scrub("/bot123%3ATEST/getMe and 123%3aTEST and 123:TEST") == (
        "/bot<token>/getMe and <token> and <token>"
    )
    other = "9876543210%3A" + "B" * 35  # a token that is not ours, URL-encoded
    assert redact.scrub(f"url /bot{other}/x and {other}") == "url /bot<token>/x and <token>"
    assert redact.scrub("/bot9876543210%3ABBB") == "/bot<token>"  # cut short by a truncated text


def test_api_key_is_masked_also_url_encoded(monkeypatch):
    key = "s3cr3t:key/with+chars"
    monkeypatch.setattr(settings.get(), "api_key", key)
    assert redact.scrub(f"key={key}") == "key=<api-key>"
    assert redact.scrub("key=s3cr3t%3Akey%2Fwith%2Bchars") == "key=<api-key>"


def test_named_tokens_are_masked_but_not_words_that_look_alike():
    assert redact.scrub(f"Bearer {NAMED}") == "Bearer <rs-token>"
    assert redact.scrub(f"token={NAMED}&x=1") == "token=<rs-token>&x=1"
    assert redact.scrub(f"Bearer%20{NAMED}&token%3D{NAMED}") == "Bearer%20<rs-token>&token%3D<rs-token>"
    assert redact.scrub("cookie rs_session, users_" + "a" * 30) == "cookie rs_session, users_" + "a" * 30


def test_short_keys_and_telegram_texts_stay_as_they_are(monkeypatch):
    # a local key may be short: masking every "1" or "test" would leave nothing to read
    for key in ("1", "test", "secret"):
        monkeypatch.setattr(settings.get(), "api_key", key)
        monkeypatch.setattr(settings.get(), "bot_token", "")
        text = "Bad Request: chat not found (test 1, secret chat)"
        assert redact.scrub(text) == text
    readable = "Bad Request: message is not modified: specified new message content and reply markup are the same"
    assert redact.scrub(readable) == readable


def test_scrub_all_keeps_the_structure(monkeypatch):
    monkeypatch.setattr(settings.get(), "bot_token", TOKEN)
    value = {"a": [f"x {TOKEN}", 5, None, {"b": TOKEN}], "n": 1.5}
    assert redact.scrub_all(value) == {"a": ["x <token>", 5, None, {"b": "<token>"}], "n": 1.5}


@pytest.fixture
def log_file(fresh_data):
    path = logs.setup()
    yield path
    logs.setup()  # after capsys is gone: the stderr handler must not keep its closed stream


def test_log_masks_the_api_key_and_named_tokens(monkeypatch, log_file, capsys):
    monkeypatch.setattr(settings.get(), "api_key", KEY)
    logging.getLogger("rich-studio.test").warning("got %s and %s", KEY, NAMED)
    logs.flush()
    text = log_file.read_text("utf-8")
    assert _clean(text) and _clean(capsys.readouterr().err) and "<api-key>" in text and "<rs-token>" in text
