"""scripts/capture_telegram_parse.py through a running studio (/api/raw), without the bot's token."""

import importlib.util

import httpx
import pytest

import settings
from conftest import ROOT

spec_ = importlib.util.spec_from_file_location("capture_parse", ROOT / "scripts" / "capture_telegram_parse.py")
assert spec_ and spec_.loader
capture = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(capture)

CASE = {"name": "c", "format": "html", "source": "<p>a</p>"}


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(capture.time, "sleep", lambda s: None)


def test_a_case_goes_through_the_studio_and_is_deleted_right_away(client, tg, no_wait):
    call = capture.through_studio(client, "http://testserver/")
    captured: dict = {}
    assert capture.capture(call, "-100", [CASE], captured, "Bot API 10.4") == 0
    sent, deleted = tg.last("sendRichMessage")["body"], tg.last("deleteMessage")["body"]
    assert sent["rich_message"] == {"html": "<p>a</p>"} and sent["disable_notification"] is True
    assert deleted["chat_id"] == "-100"  # /api/raw runs deleteMessage only with X-Confirm: yes
    assert captured["c"]["blocks"] == [{"type": "paragraph", "text": "<p>a</p>"}]
    assert captured["c"]["spec"] == "Bot API 10.4"


def test_telegrams_refusal_is_recorded_in_its_own_words(client, tg, no_wait):
    tg.fail["sendRichMessage"] = "Bad Request: RICH_MESSAGE_DEPTH_INVALID"
    captured: dict = {}
    assert capture.capture(capture.through_studio(client, "http://testserver"), "-100", [CASE], captured, None) == 1
    assert captured["c"]["error"] == "Bad Request: RICH_MESSAGE_DEPTH_INVALID"
    assert not [c for c in tg.calls if c["method"] == "deleteMessage"]


def test_a_studio_refusal_is_not_recorded_as_telegrams(client, tg, monkeypatch, no_wait):
    """/api/raw off (or no token, no right): the studio said no, Telegram was not asked."""
    monkeypatch.setattr(settings.get(), "raw_api", False)
    captured: dict = {}
    with pytest.raises(capture.StudioFailed, match="403"):
        capture.capture(capture.through_studio(client, "http://testserver"), "-100", [CASE], captured, None)
    assert captured == {} and not tg.calls


def test_the_studio_token_goes_as_a_bearer(tg):
    seen = []

    class Client:
        def post(self, url, json, headers):
            seen.append((url, headers))
            return httpx.Response(200, json={"result": True})

    capture.through_studio(Client(), "https://studio.example", "rs_secret")("deleteMessage", {})  # type: ignore[arg-type]
    assert seen == [
        ("https://studio.example/api/raw/deleteMessage", {"Authorization": "Bearer rs_secret", "X-Confirm": "yes"})
    ]
