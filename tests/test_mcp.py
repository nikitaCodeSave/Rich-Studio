"""MCP server at /mcp: tool list, a full JSON-RPC round trip, and that publishing keeps the studio rules."""

import json

import store
from test_api import BLOCKS_POST

HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
EXPECTED_TOOLS = {
    "bot_api_status",
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
