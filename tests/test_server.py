"""Running on a server: settings checked at start, secrets from files, pinned spec, dead man's switch."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import settings
import spec as specmod
from services import housekeeping
from spec import source

SERVER = {"STUDIO_MODE": "server", "STUDIO_URL": "https://studio.example.com", "TG_BOT_TOKEN": "1:x"}


def problems(env: dict) -> list[str]:
    with pytest.raises(settings.SettingsError) as e:
        settings.Settings.from_env(env)
    return e.value.problems


def test_every_bad_value_is_named_at_once():
    found = problems({"LOG_FILES": "x", "SESSION_DAYS": "-1", "LOG_LEVEL": "loud", "STUDIO_MODE": "prod"})
    assert [p.split("=")[0] for p in found] == ["STUDIO_MODE", "SESSION_DAYS", "LOG_LEVEL", "LOG_FILES"]


def test_secrets_come_from_files(tmp_path):
    (tmp_path / "token").write_text("123:FROM_FILE\n")
    s = settings.Settings.from_env({"TG_BOT_TOKEN_FILE": str(tmp_path / "token")})
    assert s.bot_token == "123:FROM_FILE"
    assert problems({"STUDIO_API_KEY_FILE": str(tmp_path / "missing")})[0].startswith("STUDIO_API_KEY_FILE")
    both = {"TG_BOT_TOKEN": "1:x", "TG_BOT_TOKEN_FILE": str(tmp_path / "token")}
    assert "оставьте одно" in problems(both)[0]


def test_server_mode_needs_https_and_a_bot_token():
    assert len(problems({"STUDIO_MODE": "server"})) == 2
    s = settings.Settings.from_env(SERVER)
    assert "studio.example.com" in s.allowed_hosts  # the public name is accepted as Host
    assert s.spec_pinned
    assert not settings.Settings.from_env({**SERVER, "SPEC_PIN": "off"}).spec_pinned
    assert not settings.Settings.from_env({}).spec_pinned


def test_server_mode_refuses_to_start_without_a_token(tmp_path):
    cfg = settings.Settings.from_env({**SERVER, "DATA_DIR": str(tmp_path), "STUDIO_BACKGROUND": "off"})
    old = settings.get()
    try:
        with pytest.raises(settings.SettingsError, match="нет ни токена"), TestClient(main.create_app(cfg)):
            pass
        cfg.api_key = "k"
        with TestClient(main.create_app(cfg), base_url="https://studio.example.com") as c:
            assert c.get("/healthz").status_code == 200
    finally:
        settings.use(old)


def test_unwritable_data_dir_check(tmp_path, monkeypatch):
    cfg = settings.Settings.from_env({"DATA_DIR": str(tmp_path / "data")})
    main._data_dir_writable(cfg)  # made when missing

    def refuse(*a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(type(cfg.data_dir), "write_bytes", refuse)
    with pytest.raises(settings.SettingsError, match="sudo chown -R 10001:10001 data"):
        main._data_dir_writable(cfg)


def test_pinned_spec_is_the_bundled_one_and_never_downloads(monkeypatch, fresh_data):
    cache = fresh_data / "api.json"
    newer = json.loads(source.BUNDLED.read_text("utf-8")) | {"version": "Bot API 99.0"}
    cache.write_text(json.dumps(newer))  # a newer cached copy is what an unpinned studio would pick
    monkeypatch.setattr(settings.get(), "spec_pinned", True)
    s = specmod.Spec()
    s.load_initial()
    assert s.source == "bundled"

    def no_network(**kw):
        raise AssertionError("a pinned spec must not be downloaded")

    monkeypatch.setattr(httpx, "AsyncClient", no_network)
    report = asyncio.run(s.refresh(force=True))
    assert report["pinned"] and not report["updated"] and s.last_check is None


def test_dead_mans_switch(monkeypatch, caplog):
    seen: list[str] = []
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(500 if request.url.path.endswith("/down") else 200)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    asyncio.run(housekeeping.ping("ok"))  # no URL: nothing to do
    monkeypatch.setattr(settings.get(), "deadman_url", "https://hc.example/ping/secret-uuid")
    asyncio.run(housekeeping.ping("warn"))
    asyncio.run(housekeeping.ping("fail"))
    assert seen == ["/ping/secret-uuid", "/ping/secret-uuid/fail"]
    monkeypatch.setattr(settings.get(), "deadman_url", "https://hc.example/down")
    asyncio.run(housekeeping.ping("ok"))  # a failed ping is a warning, and the URL is not in it
    assert "HTTPStatusError" in caplog.text and "hc.example" not in caplog.text
