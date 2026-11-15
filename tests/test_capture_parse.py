"""scripts/capture_telegram_parse.py through a running studio (/api/raw), without the bot's token. Only Telegram's
Bad Request is recorded as its verdict on a case; anything else stops the run and leaves captured.json alone."""

import importlib.util
import json

import httpx
import pytest

import settings
from conftest import ROOT

spec_ = importlib.util.spec_from_file_location("capture_parse", ROOT / "scripts" / "capture_telegram_parse.py")
assert spec_ and spec_.loader
capture = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(capture)

CASE = {"name": "c", "format": "html", "source": "<p>a</p>"}


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(capture, "_sleep", lambda s: None)


class Studio:
    """A studio (or a proxy in front of it) that answers every call with the same response."""

    def __init__(self, status: int, body=None, text: str = ""):
        self.status, self.body, self.text = status, body, text
        self.seen: list = []

    def post(self, url, json, headers):
        self.seen.append((url, headers))
        if self.body is None:
            return httpx.Response(self.status, text=self.text)
        return httpx.Response(self.status, json=self.body)


def test_a_case_goes_through_the_studio_and_is_deleted_right_away(client, tg):
    call = capture.through_studio(client, "http://testserver/")
    captured: dict = {}
    assert capture.capture(call, "-100", [CASE], captured, "Bot API 10.4") == 0
    sent, deleted = tg.last("sendRichMessage")["body"], tg.last("deleteMessage")["body"]
    assert sent["rich_message"] == {"html": "<p>a</p>"} and sent["disable_notification"] is True
    assert deleted["chat_id"] == "-100"  # /api/raw runs deleteMessage only with X-Confirm: yes
    assert captured["c"]["blocks"] == [{"type": "paragraph", "text": "<p>a</p>"}]
    assert captured["c"]["spec"] == "Bot API 10.4"


def test_telegrams_bad_request_is_recorded_in_its_own_words(client, tg):
    tg.fail["sendRichMessage"] = "Bad Request: RICH_MESSAGE_DEPTH_INVALID"
    captured: dict = {}
    assert capture.capture(capture.through_studio(client, "http://testserver"), "-100", [CASE], captured, None) == 1
    assert captured["c"]["error"] == "Bad Request: RICH_MESSAGE_DEPTH_INVALID"
    assert not [c for c in tg.calls if c["method"] == "deleteMessage"]


def test_a_400_from_the_studio_is_recorded(tg):
    studio = Studio(422, {"detail": "Telegram: Bad Request: RICH_MESSAGE_TEXT_TOO_LONG", "error_code": 400})
    captured: dict = {}
    assert capture.capture(capture.through_studio(studio, "https://s"), "-100", [CASE], captured, None) == 1  # type: ignore[arg-type]
    assert captured["c"]["error"] == "Bad Request: RICH_MESSAGE_TEXT_TOO_LONG"


@pytest.mark.parametrize(
    "studio",
    [
        Studio(502, {"detail": "Telegram: Bad Gateway", "error_code": None}),
        Studio(502, {"detail": "Telegram: Internal Server Error", "error_code": 500}),
        Studio(502, {"detail": "нет ответа от Telegram. Действие могло выполниться, проверьте в Telegram"}),
        Studio(401, {"detail": "нужен токен: заголовок Authorization: Bearer <токен> или X-API-Key"}),
        Studio(403, {"detail": "нет права admin"}),
        Studio(403, text="<html>Forbidden by the proxy</html>"),
    ],
    ids=["502 error_code null", "502 error_code 500", "502 no answer", "401", "403", "403 page of a proxy"],
)
def test_anything_but_a_bad_request_stops_the_run(studio):
    captured = {"c": {"blocks": ["снятое раньше"]}}
    with pytest.raises(capture.StudioFailed):
        capture.capture(capture.through_studio(studio, "https://s"), "-100", [CASE], captured, None)
    assert captured == {"c": {"blocks": ["снятое раньше"]}}


def test_a_studio_refusal_is_not_recorded_as_telegrams(client, tg, monkeypatch):
    """/api/raw off (or no token, no right): the studio said no, Telegram was not asked."""
    monkeypatch.setattr(settings.get(), "raw_api", False)
    captured: dict = {}
    with pytest.raises(capture.StudioFailed, match="403"):
        capture.capture(capture.through_studio(client, "http://testserver"), "-100", [CASE], captured, None)
    assert captured == {} and not tg.calls


def test_with_the_bot_token_only_a_400_is_recorded_too():
    captured: dict = {}
    answers = iter([{"ok": False, "error_code": 502, "description": "Bad Gateway"}])
    with pytest.raises(capture.CaptureFailed, match="502"):
        capture.capture(lambda m, p: next(answers), "-100", [CASE], captured, None)
    assert captured == {}


class _Closing:
    def __init__(self, studio):
        self.studio = studio

    def __enter__(self):
        return self.studio

    def __exit__(self, *exc):
        return False


def test_a_failed_run_leaves_captured_json_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "captured.json"
    path.write_text('{"c": {"blocks": []}}', "utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps([CASE]), "utf-8")
    monkeypatch.setattr(capture, "CAPTURED", path)
    monkeypatch.setattr(capture, "CASES", cases)
    studio = Studio(502, {"detail": "Telegram: Bad Gateway", "error_code": None})
    monkeypatch.setattr(capture.httpx, "Client", lambda **kw: _Closing(studio))
    monkeypatch.setenv("STUDIO_URL", "https://s")
    monkeypatch.setenv("PARSE_CHAT", "-100")
    monkeypatch.setattr(capture.sys, "argv", ["capture"])
    assert capture.main() == 1
    assert path.read_text("utf-8") == '{"c": {"blocks": []}}'


def test_the_studio_token_goes_as_a_bearer(tg):
    studio = Studio(200, {"result": True})
    capture.through_studio(studio, "https://studio.example", "rs_secret")("deleteMessage", {})  # type: ignore[arg-type]
    assert studio.seen == [
        ("https://studio.example/api/raw/deleteMessage", {"Authorization": "Bearer rs_secret", "X-Confirm": "yes"})
    ]
