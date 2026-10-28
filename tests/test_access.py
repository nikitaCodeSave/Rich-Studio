"""Named tokens with levels, the editor's session cookie, who is recorded for a post, /api/raw for admin only."""

import cli
import security
import settings
import store
from test_api import BLOCKS_POST
from test_mcp import rpc


def token(name: str, level: str) -> dict:
    secret = f"rs_test_{name}"
    store.add_token(name, secret, level)
    return {"Authorization": f"Bearer {secret}"}


def test_open_studio_stays_open_until_a_key_or_token_exists(client):
    assert client.get("/api/drafts").status_code == 200
    assert client.get("/api/session").json() == {"name": None, "level": "admin", "auth": False}
    token("agent", "read")
    assert client.get("/api/drafts").status_code == 401
    assert client.get("/healthz").status_code == 200


def test_levels(client, tg):
    read, draft, publish = token("r", "read"), token("d", "draft"), token("p", "publish")
    assert client.get("/api/drafts", headers=read).status_code == 200
    assert client.post("/api/drafts", json={"title": "x", "post": BLOCKS_POST}, headers=read).status_code == 403
    assert client.post("/api/drafts", json={"title": "x", "post": BLOCKS_POST}, headers=draft).status_code == 200
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers=draft)
    assert r.status_code == 403 and "publish" in r.json()["detail"]
    assert not tg.calls
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=publish).status_code == 200
    # X-API-Key works the same as Bearer
    assert client.get("/api/drafts", headers={"X-API-Key": "rs_test_r"}).status_code == 200
    assert client.get("/api/drafts", headers={"X-API-Key": "wrong"}).status_code == 401


def test_every_route_that_posts_to_a_channel_asks_for_publish(client):
    draft = token("d", "draft")
    for method, path in [
        ("post", "/api/send"),
        ("post", "/api/edit"),
        ("post", "/api/schedule"),
        ("post", "/api/poll"),
        ("post", "/api/posts/x/stop-poll"),
        ("post", "/api/posts/x/pin"),
        ("delete", "/api/posts/x/pin"),
        ("post", "/api/posts/x/buttons"),
        ("post", "/api/posts/x/edit"),
        ("post", "/api/posts/x/reschedule"),
        ("delete", "/api/posts/x"),
    ]:
        kw = {"json": {}} if method == "post" else {}
        assert getattr(client, method)(path, headers=draft, **kw).status_code == 403, path


def test_actor_is_the_token_name(client, tg):
    publish = token("claude-laptop", "publish")
    client.post("/api/send", json={"post": BLOCKS_POST}, headers={**publish, "X-Studio-Client": "mcp:claude"})
    client.post("/api/send", json={"post": BLOCKS_POST}, headers=publish)
    assert [p["actor"] for p in store.list_posts()] == ["claude-laptop", "mcp:claude@claude-laptop"]


def test_revoked_token_and_its_sessions_stop_working(client):
    read = token("gone", "read")
    assert client.post("/api/session", json={"key": "rs_test_gone"}).status_code == 200
    assert client.get("/api/drafts", headers=read).status_code == 200
    assert store.revoke_token("gone")
    assert client.get("/api/drafts", headers=read).status_code == 401
    assert client.get("/api/drafts").status_code == 401  # the session cookie is gone with it


def test_editor_session_is_an_httponly_cookie(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    assert client.post("/api/session", json={"key": "nope"}).status_code == 401
    r = client.post("/api/session", json={"key": "secret"})
    cookie = r.headers["set-cookie"]
    assert r.status_code == 200 and "rs_session=" in cookie and "secret" not in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" not in cookie  # plain http locally
    assert client.get("/api/session").json()["level"] == "admin"
    assert client.delete("/api/session").status_code == 200
    assert client.get("/api/drafts").status_code == 401
    https = client.post("/api/session", json={"key": "secret"}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in https.headers["set-cookie"]


def test_raw_is_for_admin_and_can_be_switched_off(client, tg, monkeypatch):
    publish, admin = token("p", "publish"), token("a", "admin")
    assert client.post("/api/raw/getMe", json={}, headers=publish).status_code == 403
    assert client.post("/api/raw/getMe", json={}, headers=admin).status_code == 200
    monkeypatch.setattr(settings.get(), "raw_api", False)
    assert client.post("/api/raw/getMe", json={}, headers=admin).status_code == 403


def test_backup_download_is_for_admin(client):
    publish = token("p", "publish")
    assert client.get("/api/export", headers=publish).status_code == 403


def test_mcp_tools_check_the_level_of_the_token(client, tg):
    draft = token("agent", "draft")
    res, _ = call_as(client, draft, "save_draft", post=BLOCKS_POST, title="Анонс")
    assert not res.get("isError")
    res, text = call_as(client, draft, "publish_post", post=BLOCKS_POST)
    assert res["isError"] and "publish" in text and not tg.calls
    read = token("reader", "read")
    res, _ = call_as(client, read, "list_drafts")
    assert not res.get("isError")
    res, text = call_as(client, read, "save_draft", post=BLOCKS_POST)
    assert res["isError"] and "draft" in text
    publish = token("pub", "publish")
    call_as(client, publish, "publish_post", post=BLOCKS_POST)
    assert store.list_posts()[0]["actor"] == "mcp@pub"


def call_as(client, headers, tool, **arguments) -> tuple[dict, str]:
    res = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, headers=headers)["result"]
    return res, res["content"][0]["text"] if res.get("content") else ""


def test_cli_makes_lists_and_revokes_tokens(capsys):
    assert cli.main(["token", "add", "ci-bot", "--level", "publish"]) == 0
    secret = capsys.readouterr().out.strip()
    assert secret.startswith("rs_") and security.by_secret(secret) == security.Principal("ci-bot", "publish")
    assert cli.main(["token", "add", "ci-bot"]) == 2  # the name is taken
    assert cli.main(["token", "add", "bad name"]) == 2
    cli.main(["token", "list"])
    assert "ci-bot" in capsys.readouterr().out
    assert cli.main(["token", "revoke", "ci-bot"]) == 0
    assert security.by_secret(secret) is None
    assert cli.main(["token", "revoke", "ci-bot"]) == 1
