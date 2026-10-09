"""Channels as an entity: titles, forum topics, a draft that knows its channel."""

import asyncio

import settings
from services import updates

BLOCKS_POST = {"rich_message": {"blocks": [{"type": "paragraph", "text": "Привет"}]}}
FORUM = {"ok": True, "result": {"id": -1002627012139, "type": "supergroup", "title": "МОИ ЗАМЕТКИ", "is_forum": True}}


def test_default_channel_is_listed_before_any_check(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "default_chat", "@main")
    [ch] = client.get("/api/channels").json()
    assert ch["chat_id"] == "@main" and ch["is_default"] and ch["title"] is None and ch["topics"] == []


def test_check_remembers_title_forum_and_rights(client, tg):
    tg.queue["getChat"] = [FORUM]
    r = client.post("/api/channels", json={"chat_id": "-1002627012139"})
    assert r.status_code == 200, r.text
    ch = r.json()["channel"]
    assert ch["title"] == "МОИ ЗАМЕТКИ" and ch["is_forum"] == 1 and ch["can_post"] == 1
    assert ch["rights"]["can_post_messages"] is True and ch["rights"]["status"] == "administrator"
    listed = {c["chat_id"]: c for c in client.get("/api/channels").json()}
    assert listed["-1002627012139"]["title"] == "МОИ ЗАМЕТКИ" and listed["-1002627012139"]["is_forum"] is True
    # /api/check (the editor's button and agents) remembers the channel the same way
    client.get("/api/check?chat_id=@other")
    assert "@other" in {c["chat_id"] for c in client.get("/api/channels").json()}


def test_topics_by_hand_and_from_updates(client, tg):
    tg.queue["getChat"] = [FORUM]
    client.post("/api/channels", json={"chat_id": "-1002627012139"})
    r = client.put("/api/channels/-1002627012139/topics", json={"topics": [{"id": 7, "name": "Анонсы"}]})
    assert r.json()["topics"] == [{"id": 7, "name": "Анонсы"}]
    assert client.put("/api/channels/-1002627012139/topics", json={"topics": [{"id": 0}]}).status_code == 400
    assert client.put("/api/channels/@nope/topics", json={"topics": []}).status_code == 404
    created = {
        "update_id": 1,
        "message": {
            "message_id": 50,
            "message_thread_id": 50,
            "chat": {"id": -1002627012139, "type": "supergroup"},
            "forum_topic_created": {"name": "Черновики", "icon_color": 1},
        },
    }
    assert asyncio.run(updates.handle_update(created)) == "topic"
    renamed = {"update_id": 2, "message": {**created["message"], "forum_topic_created": None}}
    renamed["message"]["forum_topic_edited"] = {"name": "Идеи"}
    asyncio.run(updates.handle_update(renamed))
    [ch] = [c for c in client.get("/api/channels").json() if c["chat_id"] == "-1002627012139"]
    assert ch["topics"] == [{"id": 7, "name": "Анонсы"}, {"id": 50, "name": "Идеи"}]


def test_draft_keeps_its_channels_and_publishing_uses_them(client, tg):
    d = client.post("/api/drafts", json={"title": "A", "post": BLOCKS_POST, "chat_ids": ["@chan_a"]}).json()
    assert d["chat_ids"] == ["@chan_a"]
    # a save without chat_ids (an older client, an agent) keeps them
    d = client.put(f"/api/drafts/{d['id']}", json={"title": "A", "post": BLOCKS_POST, "version": d["version"]}).json()
    assert d["chat_ids"] == ["@chan_a"]
    assert client.get("/api/drafts").json()[0]["chat_ids"] == ["@chan_a"]
    r = client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": d["id"]})
    assert r.status_code == 200 and tg.last("sendRichMessage")["body"]["chat_id"] == "@chan_a"
    # an explicit channel still wins
    client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": d["id"], "chat_id": "@chan_b"})
    assert tg.last("sendRichMessage")["body"]["chat_id"] == "@chan_b"
    # [] clears them: the default channel again
    d = client.put(f"/api/drafts/{d['id']}", json={"title": "A", "post": BLOCKS_POST, "chat_ids": []}).json()
    assert d["chat_ids"] is None


def test_topic_goes_to_telegram_as_message_thread_id(client, tg):
    post = {**BLOCKS_POST, "options": {"message_thread_id": 7}}
    client.post("/api/send", json={"post": post, "chat_id": "-1002627012139"})
    body = tg.last("sendRichMessage")["body"]
    assert body["message_thread_id"] == 7 and body["chat_id"] == -1002627012139


def test_deleted_draft_needs_an_explicit_channel(client, tg):
    d = client.post("/api/drafts", json={"title": "A", "post": BLOCKS_POST, "chat_ids": ["@chan_a"]}).json()
    client.delete(f"/api/drafts/{d['id']}")
    r = client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": d["id"]})
    assert r.status_code == 400 and "chat_id" in r.json()["detail"]
    r = client.post("/api/send", json={"post": BLOCKS_POST, "draft_id": d["id"], "chat_id": "@chan_b"})
    assert r.status_code == 200 and tg.last("sendRichMessage")["body"]["chat_id"] == "@chan_b"


def test_topic_needs_one_forum_chat(client, tg):
    post = {**BLOCKS_POST, "options": {"message_thread_id": 7}}
    r = client.post("/api/send", json={"post": post, "chat_ids": ["@a", "@b"]})
    assert r.status_code == 400 and "несколько" in r.json()["detail"]
    tg.queue["getChat"] = [{"ok": True, "result": {"id": -100555, "type": "channel", "title": "Канал"}}]
    client.post("/api/channels", json={"chat_id": "-100555"})
    for path, extra in (("/api/send", {}), ("/api/schedule", {"at": "2099-01-01T09:00:00+03:00"})):
        r = client.post(path, json={"post": post, "chat_id": "-100555", "dry_run": True, **extra})
        assert r.status_code == 400 and "не форум" in r.json()["detail"], path
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_channel_list_is_normalized_and_an_empty_item_refused(client, tg):
    r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_ids": ["@Chan", " @chan ", "-1001", -1001]})
    assert r.status_code == 200, r.text
    assert [c["body"]["chat_id"] for c in tg.calls if c["method"] == "sendRichMessage"] == ["@Chan", -1001]
    for bad in (["@a", ""], ["  "], ["chan"], ["@a b"]):
        r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_ids": bad})
        assert r.status_code == 400, bad
    assert len([c for c in tg.calls if c["method"] == "sendRichMessage"]) == 2
    d = client.post("/api/drafts", json={"title": "A", "post": BLOCKS_POST, "chat_ids": [" @x", "@X", "-100"]}).json()
    assert d["chat_ids"] == ["@x", "-100"]
    assert (
        client.post("/api/drafts", json={"title": "B", "post": BLOCKS_POST, "chat_ids": ["@x", ""]}).status_code == 400
    )
    r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_id": " @trim "})
    assert r.status_code == 200 and tg.last("sendRichMessage")["body"]["chat_id"] == "@trim"
