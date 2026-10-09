"""MCP server at /mcp: tool list, a full JSON-RPC round trip, and that publishing keeps the studio rules."""

import json
import re

import store
from test_api import BLOCKS_POST

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
EXPECTED_TOOLS = {
    "bot_api_status",
    "refresh_bot_api",
    "describe_type",
    "validate_post",
    "save_draft",
    "list_drafts",
    "send_preview_to_me",
    "check_channel",
    "publish_post",
    "schedule_post",
    "edit_post",
    "list_posts",
    "preview_post",
}


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
    call(client, "send_preview_to_me", post=BLOCKS_POST)
    assert tg.last("sendRichMessage")["body"]["chat_id"] == 777


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
    assert names == EXPECTED_TOOLS - {"publish_post", "schedule_post", "edit_post"}


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
