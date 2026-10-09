"""MCP server at /mcp: tool list, a full JSON-RPC round trip, and that publishing keeps the studio rules."""

import json
import re

import store
from test_api import BLOCKS_POST

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
EXPECTED_TOOLS = {
    "studio_health",
    "studio_logs",
    "bot_api_status",
    "refresh_bot_api",
    "describe_type",
    "validate_post",
    "save_draft",
    "list_drafts",
    "send_preview_to_me",
    "check_channel",
    "list_channels",
    "publish_post",
    "schedule_post",
    "edit_post",
    "list_posts",
    "preview_post",
    "get_draft",
    "list_media",
    "studio_status",
    "reschedule_post",
    "cancel_scheduled_post",
    "get_post",
    "post_history",
}
PUBLISHING = {"publish_post", "schedule_post", "edit_post", "reschedule_post", "cancel_scheduled_post"}


def rpc(client, method, params=None, rid=1, **kw):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    r = client.post("/mcp", json=body, headers={**HEADERS, **kw.pop("headers", {})}, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def call(client, tool, **arguments):
    res = rpc(client, "tools/call", {"name": tool, "arguments": arguments})["result"]
    text = res["content"][0]["text"] if res.get("content") else ""
    if res.get("isError"):
        return res, None, text
    return res, json.loads(text), text


def call_list(client, tool, **arguments) -> list:
    """A tool that returns a list: every item comes as its own content block."""
    res = rpc(client, "tools/call", {"name": tool, "arguments": arguments})["result"]
    assert not res.get("isError"), res
    return [json.loads(c["text"]) for c in res["content"]]


def test_initialize_and_list_tools(client):
    init = rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
    )
    assert init["result"]["serverInfo"]["name"] == "rich-studio"
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    assert set(tools) == EXPECTED_TOOLS
    assert tools["publish_post"]["annotations"]["destructiveHint"] is True
    assert tools["validate_post"]["annotations"]["readOnlyHint"] is True


def test_validate_and_describe(client, tg):
    res, data, _ = call(client, "validate_post", post=BLOCKS_POST)
    assert not res.get("isError") and data["ok"] is True
    res, data, _ = call(client, "describe_type", name="InputRichBlockTable")
    assert data["name"] == "InputRichBlockTable"
    res, _, text = call(client, "describe_type", name="NoSuchType")
    assert res["isError"] and "NoSuchType" in text
    assert not tg.calls


def test_publish_is_idempotent_and_marked_mcp(client, tg):
    for _ in range(2):
        res, _, _ = call(client, "publish_post", post=BLOCKS_POST, idempotency_key="mcp-1")
        assert not res.get("isError"), res
    assert len([c for c in tg.calls if c["method"] == "sendRichMessage"]) == 1
    [p] = store.list_posts()
    assert p["actor"] == "mcp" and p["status"] == "sent"


def test_draft_channel_from_save_draft_is_used_by_publish(client, tg):
    _, d, _ = call(client, "save_draft", post=BLOCKS_POST, title="Анонс", chat_ids=["@agent_chan"])
    assert d["chat_ids"] == ["@agent_chan"]
    _, again, _ = call(client, "save_draft", post=BLOCKS_POST, draft_id=d["id"], version=d["version"])
    assert again["chat_ids"] == ["@agent_chan"]  # kept when not passed
    res, _, _ = call(client, "publish_post", draft_id=d["id"], version=again["version"])
    assert not res.get("isError"), res
    assert tg.last("sendRichMessage")["body"]["chat_id"] == "@agent_chan"
    call(client, "check_channel", chat_id="@agent_chan")
    assert [c["title"] for c in call_list(client, "list_channels") if c["chat_id"] == "@agent_chan"] == ["Тест"]


def test_publish_dry_run_and_validation_errors(client, tg):
    res, data, _ = call(client, "publish_post", post=BLOCKS_POST, chat_id="@x", dry_run=True)
    assert data["dry_run"] is True and data["requests"][0]["params"]["chat_id"] == "@x"
    bad = {"rich_message": {"blocks": [{"type": "heading", "text": "x", "size": "big"}]}}
    res, _, text = call(client, "publish_post", post=bad)
    assert res["isError"] and "проверку" in text
    assert not tg.calls and not store.list_posts()


def test_draft_and_preview(client, tg):
    _, data, _ = call(client, "save_draft", post=BLOCKS_POST, title="Анонс")
    assert data["preview_path"] == f"/render/draft/{data['id']}"
    assert client.get(data["preview_path"]).status_code == 200
    _, sent, _ = call(client, "send_preview_to_me", post=BLOCKS_POST)
    assert tg.last("sendRichMessage")["body"]["chat_id"] == 777
    # the agent gets Telegram's parse once, in the form it can send again
    assert sent["parsed"]["input_blocks"] and "blocks" not in sent["parsed"]


def test_mcp_is_behind_the_api_key(client, monkeypatch):
    import settings

    monkeypatch.setattr(settings.get(), "api_key", "secret")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    assert client.post("/mcp", json=body, headers=HEADERS).status_code == 401
    r = client.post("/mcp", json=body, headers={**HEADERS, "X-API-Key": "secret"})
    assert r.status_code == 200 and json.loads(r.text)["result"]["tools"]


def test_get_mcp_is_405_not_a_stream_that_never_ends(client):
    r = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert r.status_code == 405 and r.headers["allow"] == "POST"


def test_mcp_publish_off_leaves_only_drafts_and_preview(monkeypatch):
    import asyncio

    import mcp_tools
    import settings

    monkeypatch.setattr(settings.get(), "mcp_publish", False)
    names = {t.name for t in asyncio.run(mcp_tools.build().list_tools())}
    assert names == EXPECTED_TOOLS - PUBLISHING


def test_refresh_bot_api_reports_failure_and_keeps_the_spec(client, monkeypatch):
    import httpx

    import spec.source

    class Offline:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError("offline")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(spec.source.httpx, "AsyncClient", Offline)
    _, before, _ = call(client, "bot_api_status")
    res, data, _ = call(client, "refresh_bot_api")
    assert not res.get("isError") and data["updated"] is False and "offline" in data["error"]
    assert data["version"] == before["version"]
    _, after, _ = call(client, "bot_api_status")
    assert after["gaps"] == [] and "offline" in after["last_check"]["error"]


def test_tools_that_reach_telegram_say_so(client):
    tools = {t["name"]: t["annotations"] for t in rpc(client, "tools/list")["result"]["tools"]}
    for name in ("send_preview_to_me", "check_channel", "publish_post", "schedule_post", "edit_post"):
        assert tools[name]["openWorldHint"] is True, name
    assert tools["send_preview_to_me"]["destructiveHint"] is False


def test_preview_app(client, tg):
    """preview_post is bound to a self-contained ui:// page that renders the post with render.js."""
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    uri = tools["preview_post"]["_meta"]["ui"]["resourceUri"]
    assert uri == "ui://rich-studio/preview"
    assert tools["preview_post"]["annotations"]["readOnlyHint"] is True
    page = rpc(client, "resources/read", {"uri": uri})["result"]["contents"][0]
    assert page["mimeType"] == "text/html;profile=mcp-app"
    html = page["text"]
    assert "RichRender" in html and "ui/initialize" in html
    assert "src=" not in html.split("<body>")[0]  # nothing loads from the network
    assert html.count("</script") == html.count("<script")  # inlined code cannot close its tag early

    res, data, _ = call(client, "preview_post", post=BLOCKS_POST)
    assert not res.get("isError")
    assert data["post"]["rich_message"] == BLOCKS_POST["rich_message"]
    assert data["validation"]["ok"] is True
    res, data, _ = call(client, "preview_post", post={"rich_message": {}})
    assert data["validation"]["errors"]
    assert not tg.calls


def test_readme_lists_every_tool():
    """README is where a person learns what agents can do: it must not drift from the server."""
    from pathlib import Path

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text("utf-8")
    line = next(s for s in readme.splitlines() if s.startswith("Инструменты: "))
    listed = set(re.findall(r"`([a-z_]+)`", line)) - {"idempotency_key", "dry_run"}
    assert listed == EXPECTED_TOOLS
    assert f"с {len(EXPECTED_TOOLS)} инструментами" in readme


# ---------- continuing the user's work ----------
def test_agent_continues_a_draft_without_making_copies(client):
    _, d, _ = call(client, "save_draft", post=BLOCKS_POST, title="Анонс")
    _, full, _ = call(client, "get_draft", draft_id=d["id"])
    assert full["payload"]["rich_message"] == BLOCKS_POST["rich_message"]
    new = {"rich_message": {"markdown": "# Правка агента"}}
    _, d2, _ = call(client, "save_draft", post=new, draft_id=d["id"], version=full["version"])
    assert d2["id"] == d["id"] and d2["title"] == "Анонс" and d2["version"] == full["version"] + 1
    assert len(store.list_drafts()) == 1
    # the user saved meanwhile: the agent's stale version is refused, nothing is overwritten
    res, _, text = call(client, "save_draft", post=BLOCKS_POST, draft_id=d["id"], version=full["version"])
    assert res["isError"] and "изменили" in text
    res, _, text = call(client, "save_draft", post=BLOCKS_POST, draft_id="nope")
    assert res["isError"]


def test_agent_save_keeps_the_link_to_the_edited_post(client, tg):
    pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    d = client.post("/api/drafts", json={"title": "Правка", "post": BLOCKS_POST, "post_id": pid}).json()
    call(client, "save_draft", post={"rich_message": {"markdown": "x"}}, draft_id=d["id"])
    assert store.get_draft(d["id"])["post_id"] == pid


def test_agent_reads_a_post_before_editing_and_sees_history(client, tg):
    pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    _, p, _ = call(client, "get_post", post_id=pid)
    assert p["payload"]["rich_message"] == BLOCKS_POST["rich_message"] and p["status"] == "sent"
    call(client, "edit_post", post_id=pid, post={"rich_message": {"markdown": "# Новое"}})
    h = call_list(client, "post_history", post_id=pid)
    assert len(h) == 1 and h[0]["actor"] == "mcp" and h[0]["payload"]["rich_message"] == BLOCKS_POST["rich_message"]


def test_media_list_and_status(client, tg):
    client.post("/api/media", files={"file": ("cat.png", b"\x89PNG", "image/png")})
    files = call_list(client, "list_media")
    assert files[0]["ref"] == "upload:cat.png" and files[0]["used"] is False
    _, st, _ = call(client, "studio_status")
    assert st["token_set"] is True and st["updates"]["state"] == "off" and st["schedule_late_min"] == 60


def test_agent_moves_and_cancels_scheduled_posts_but_never_deletes_from_channel(client, tg):
    _, rec, _ = call(client, "schedule_post", post=BLOCKS_POST, at="2099-01-01T09:00:00+03:00")
    _, moved, _ = call(client, "reschedule_post", post_id=rec["id"], at="2099-01-02T09:00:00+03:00")
    assert moved["scheduled_at"] > rec["scheduled_at"]
    _, gone, _ = call(client, "cancel_scheduled_post", post_id=rec["id"])
    assert gone["status"] == "cancelled"
    sent = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    res, _, text = call(client, "cancel_scheduled_post", post_id=sent)
    assert res["isError"] and "sent" in text
    assert not [c for c in tg.calls if c["method"] == "deleteMessage"]


def test_schedule_dry_run_creates_nothing(client):
    _, r, _ = call(client, "schedule_post", post=BLOCKS_POST, at="2099-01-01T09:00:00+03:00", dry_run=True)
    assert r["dry_run"] is True and r["at"] and not store.list_posts()


def test_publish_without_key_does_not_post_twice(client, tg):
    call(client, "publish_post", post=BLOCKS_POST, chat_id="@a")
    call(client, "publish_post", post=BLOCKS_POST, chat_id="@a")  # the agent retried after a timeout
    call(client, "publish_post", post=BLOCKS_POST, chat_id="@b")  # another channel is another request
    sent = [c["body"]["chat_id"] for c in tg.calls if c["method"] == "sendRichMessage"]
    assert sent == ["@a", "@b"]


def test_flood_wait_is_reported_with_retry_after(client, tg):
    tg.queue["sendRichMessage"] = [
        {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 300}}
    ]
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 502 and r.json()["retry_after"] == 300


def test_agent_sends_the_post_json_once_and_then_works_by_draft_id(client, tg):
    _, d, _ = call(client, "save_draft", post=BLOCKS_POST, title="Один раз")
    assert d["validation"]["ok"] is True  # no separate validate_post needed
    _, prev, _ = call(client, "preview_post", draft_id=d["id"])
    assert prev["post"]["rich_message"] == BLOCKS_POST["rich_message"] and prev["validation"]["ok"]
    _, dry, _ = call(client, "publish_post", draft_id=d["id"], chat_id="@x", dry_run=True)
    assert dry["dry_run"] is True and not tg.calls
    res, _, text = call(client, "publish_post", draft_id=d["id"], chat_id="@x")
    assert res["isError"] and "version" in text and not tg.calls  # what the user saw must be pinned
    res, _, _ = call(client, "publish_post", draft_id=d["id"], version=d["version"], chat_id="@x")
    assert not res.get("isError"), res
    [p] = store.list_posts()
    assert p["draft_id"] == d["id"] and p["actor"] == "mcp"
    assert tg.last("sendRichMessage")["body"]["rich_message"] == BLOCKS_POST["rich_message"]


def test_publishing_a_draft_changed_after_the_preview_is_refused(client, tg):
    _, d, _ = call(client, "save_draft", post=BLOCKS_POST)
    store.save_draft(d["id"], "", {"rich_message": {"markdown": "правка в редакторе"}}, d["version"])
    for tool, extra in (("publish_post", {}), ("schedule_post", {"at": "2030-01-01T09:00:00+03:00"})):
        res, _, text = call(client, tool, draft_id=d["id"], version=d["version"], **extra)
        assert res["isError"] and "изменили" in text
    res, _, text = call(client, "publish_post", post=BLOCKS_POST, draft_id=d["id"], version=d["version"])
    assert res["isError"] and "одно из двух" in text
    assert not tg.calls and not store.list_posts()


def test_save_draft_reports_validation_errors(client):
    bad = {"rich_message": {"blocks": [{"type": "table", "rows": []}]}}
    _, d, _ = call(client, "save_draft", post=bad)
    assert d["validation"]["ok"] is False and "cells" in d["validation"]["errors"][0]


def test_a_draft_that_edits_a_published_post_never_goes_out_as_a_new_post(client, tg):
    call(client, "publish_post", post=BLOCKS_POST, chat_id="@x")
    [p] = store.list_posts()
    d = store.save_draft(None, "правка", {"rich_message": {"markdown": "новый текст"}}, None, p["id"])
    for tool, extra in (("publish_post", {"chat_id": "@x"}), ("schedule_post", {"at": "2030-01-01T09:00:00+03:00"})):
        res, _, text = call(client, tool, draft_id=d["id"], version=d["version"], **extra)
        assert res["isError"] and "edit_post" in text and p["id"] in text
    assert len(store.list_posts()) == 1 and len([c for c in tg.calls if c["method"] == "sendRichMessage"]) == 1
    _, prev, _ = call(client, "preview_post", draft_id=d["id"])
    assert prev["version"] == d["version"]  # the agent publishes exactly the version it showed
    res, _, _ = call(client, "edit_post", post_id=p["id"], draft_id=d["id"], version=prev["version"])
    assert not res.get("isError"), res
    assert tg.last("editMessageText")["body"]["rich_message"] == {"markdown": "новый текст"}
    [after] = store.list_posts()
    assert after["id"] == p["id"] and store.get_post(p["id"])["payload"]["rich_message"] == {"markdown": "новый текст"}


def test_edit_post_by_draft_checks_the_version_and_the_post(client, tg):
    call(client, "publish_post", post=BLOCKS_POST, chat_id="@x")
    call(client, "publish_post", post=BLOCKS_POST, chat_id="@y")
    one, two = store.list_posts()
    d = store.save_draft(None, "правка", BLOCKS_POST, None, one["id"])
    res, _, text = call(client, "edit_post", post_id=two["id"], draft_id=d["id"], version=d["version"])
    assert res["isError"] and one["id"] in text  # a working copy of one post does not replace another
    res, _, text = call(client, "edit_post", post_id=one["id"], draft_id=d["id"])
    assert res["isError"] and "version" in text
    assert not [c for c in tg.calls if c["method"] == "editMessageText"]
