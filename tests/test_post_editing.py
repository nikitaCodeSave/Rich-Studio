"""Editing a post: versions before each replacement, moving a scheduled or missed post, the working-copy draft."""

import asyncio
import time

import store
from services import posts, scheduler
from test_api import BLOCKS_POST

NEW = {"rich_message": {"markdown": "# Исправлено"}}


def test_every_replacement_keeps_the_previous_content(client, tg):
    pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    client.post(f"/api/posts/{pid}/edit", json=NEW, headers={"X-Studio-Client": "mcp:claude"})
    client.post(f"/api/posts/{pid}/edit", json={"rich_message": {"markdown": "третья"}})
    v = client.get(f"/api/posts/{pid}/versions").json()
    assert [x["payload"]["rich_message"] for x in v] == [NEW["rich_message"], BLOCKS_POST["rich_message"]]
    assert v[1]["actor"] == "mcp:claude" and v[0]["replaced_at"] >= v[1]["replaced_at"]


def test_edit_by_message_id_also_keeps_a_version(client, tg):
    sent = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]
    client.post("/api/edit", json={"post": NEW, "message_id": sent["message_id"]})
    v = client.get(f"/api/posts/{sent['id']}/versions").json()
    assert len(v) == 1 and v[0]["payload"]["rich_message"] == BLOCKS_POST["rich_message"]


def test_scheduled_post_replacement_keeps_a_version(client):
    rec = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    client.post(f"/api/posts/{rec['id']}/edit", json=NEW)
    assert len(client.get(f"/api/posts/{rec['id']}/versions").json()) == 1


def test_versions_of_unknown_post_is_404(client):
    assert client.get("/api/posts/nope/versions").status_code == 404


def test_reschedule_moves_a_waiting_post(client):
    rec = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    at = int(time.time()) + 7200
    p = client.post(f"/api/posts/{rec['id']}/reschedule", json={"at": at}).json()
    assert p["scheduled_at"] == at and p["status"] == "scheduled"


def test_missed_post_can_be_rescheduled_or_cancelled(client):
    a = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    b = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    for rec in (a, b):
        store.update_post(rec["id"], status="missed", error="студия не работала", retry_at=1)
    p = client.post(f"/api/posts/{a['id']}/reschedule", json={"at": "2099-01-01T09:00:00+03:00"}).json()
    assert p["status"] == "scheduled" and p["error"] is None and p["retry_at"] is None
    assert client.delete(f"/api/posts/{b['id']}").json()["status"] == "cancelled"


def test_sent_post_cannot_be_rescheduled_and_past_time_is_refused(client, tg):
    pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    r = client.post(f"/api/posts/{pid}/reschedule", json={"at": int(time.time()) + 60})
    assert r.status_code == 400 and "sent" in r.json()["detail"]
    rec = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    r = client.post(f"/api/posts/{rec['id']}/reschedule", json={"at": int(time.time()) - 3600})
    assert r.status_code == 400 and "прошло" in r.json()["detail"]


def test_draft_remembers_the_post_it_edits(client, tg):
    pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    d = client.post("/api/drafts", json={"title": "Правка", "post": BLOCKS_POST, "post_id": pid}).json()
    assert d["post_id"] == pid
    d = client.put(f"/api/drafts/{d['id']}", json={"title": "Правка", "post": NEW, "version": d["version"]}).json()
    assert d["post_id"] is None  # leaving the post: the next save sends no post_id
    assert client.get("/api/drafts").json()[0]["post_id"] is None


def test_rest_never_sends_an_edit_copy_as_a_second_post_unless_asked(client, tg):
    """A draft with post_id is a working copy of that post: /api/send and /api/schedule refuse it with 409, so an agent
    on the HTTP API replaces the post instead; the editor's «Опубликовать» warns first and passes as_new."""
    p = client.post("/api/send", json={"post": BLOCKS_POST, "chat_id": "@x"}).json()["post"]
    d = client.post("/api/drafts", json={"title": "правка", "post": BLOCKS_POST, "post_id": p["id"]}).json()
    for path, extra in (("/api/send", {}), ("/api/schedule", {"at": int(time.time()) + 3600})):
        for dry in (False, True):
            body = {"post": BLOCKS_POST, "chat_id": "@x", "draft_id": d["id"], "dry_run": dry, **extra}
            r = client.post(path, json=body)
            assert r.status_code == 409 and f"/api/posts/{p['id']}/edit" in r.json()["detail"]
    assert len(store.list_posts()) == 1
    r = client.post("/api/send", json={"post": BLOCKS_POST, "chat_id": "@x", "draft_id": d["id"], "as_new": True})
    assert r.status_code == 200 and len(store.list_posts()) == 2


def test_edit_based_on_an_older_version_is_refused(client, tg):
    """Two agents edit one post: the second, based on what it read before the first edit, gets 409."""
    p = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]
    assert p["version"] == 0
    first = client.post(f"/api/posts/{p['id']}/edit?post_version=0", json=NEW)
    assert first.status_code == 200 and first.json()["version"] == 1
    edits = len([c for c in tg.calls if c["method"] == "editMessageText"])
    late = client.post(f"/api/posts/{p['id']}/edit?post_version=0", json={"rich_message": {"markdown": "чужая"}})
    assert late.status_code == 409 and "версия 1" in late.json()["detail"]
    assert len([c for c in tg.calls if c["method"] == "editMessageText"]) == edits  # Telegram was not asked
    assert store.get_post(p["id"])["payload"]["rich_message"] == NEW["rich_message"]
    # without post_version an edit still goes through, and still moves the version on
    assert client.post(f"/api/posts/{p['id']}/edit", json=BLOCKS_POST).json()["version"] == 2


def test_refused_edit_leaves_the_version_alone(client, tg):
    """Telegram refused the edit: nothing changed, so a retry with the same post_version is not a false 409."""
    p = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]
    tg.fail["editMessageText"] = "Bad Request: message to edit not found"
    refused = client.post(f"/api/posts/{p['id']}/edit?post_version=0", json=NEW)
    assert refused.status_code not in (200, 409)
    assert store.get_post(p["id"])["version"] == 0
    tg.fail.clear()
    retry = client.post(f"/api/posts/{p['id']}/edit?post_version=0", json=NEW)
    assert retry.status_code == 200 and retry.json()["version"] == 1


def test_claim_version_returns_the_version_it_took(client):
    """The number comes from the same statement that took it: a release gives back exactly this version."""
    pid = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()["id"]
    assert store.claim_version(pid, 0) == 1
    assert store.claim_version(pid, 0) is None
    assert store.claim_version(pid, None) == 2
    store.release_version(pid, 1)  # someone took 2 meanwhile: nothing to give back
    assert store.get_post(pid)["version"] == 2
    assert store.claim_version("nope", None) is None


def test_scheduled_post_edit_checks_the_version_too(client):
    rec = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    assert client.post(f"/api/posts/{rec['id']}/edit?post_version=0", json=NEW).status_code == 200
    assert client.post(f"/api/posts/{rec['id']}/edit?post_version=0", json=BLOCKS_POST).status_code == 409
    assert len(client.get(f"/api/posts/{rec['id']}/versions").json()) == 1


def test_scheduling_to_several_channels_is_all_or_nothing(client, monkeypatch):
    import store.posts

    real, n = store.posts._insert, {"calls": 0}

    def second_fails(*a, **kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise RuntimeError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(store.posts, "_insert", second_fails)
    r = client.post(
        "/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600, "chat_ids": ["@a", "@b"]}
    )
    assert r.status_code == 500
    assert store.list_posts() == []


def test_a_missed_post_is_edited_before_it_is_scheduled_again(client, tg):
    """«Править» on a missed post: its new content is what goes out after «Отложить заново»."""
    rec = store.add_scheduled(["@chan"], {"rich_message": {"markdown": "старый"}}, int(time.time()) - 7200)[0]
    store.mark_missed(rec["id"], "студия не работала")
    r = client.post(
        f"/api/posts/{rec['id']}/edit", params={"post_version": 0}, json={"rich_message": {"markdown": "новый"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "missed" and r.json()["version"] == 1
    assert client.get(f"/api/posts/{rec['id']}/versions").json()[0]["payload"]["rich_message"] == {"markdown": "старый"}
    client.post(f"/api/posts/{rec['id']}/reschedule", json={"at": int(time.time()) + 60})
    with store.conn() as c:
        c.execute("update posts set scheduled_at = ? where id = ?", (int(time.time()) - 1, rec["id"]))
    asyncio.run(scheduler.run_due_posts())
    assert tg.last("sendRichMessage")["body"]["rich_message"] == {"markdown": "новый"}


def test_buttons_of_a_scheduled_post_are_a_version_and_wait_for_the_scheduler(client, tg, monkeypatch):
    kb = {"inline_keyboard": [[{"text": "Сайт", "url": "https://t.me/telegram"}]]}
    rec = client.post(
        "/api/schedule", json={"post": {"rich_message": {"markdown": "пост"}}, "at": int(time.time()) + 3600}
    ).json()
    r = client.post(f"/api/posts/{rec['id']}/buttons", json={"reply_markup": kb}, headers={"X-Studio-Client": "ui"})
    assert r.status_code == 200 and r.json()["version"] == 1 and r.json()["payload"]["reply_markup"] == kb
    [before] = client.get(f"/api/posts/{rec['id']}/versions").json()
    assert not before["payload"].get("reply_markup") and before["actor"] == "ui"
    # the same buttons again change nothing
    assert client.post(f"/api/posts/{rec['id']}/buttons", json={"reply_markup": kb}).json()["version"] == 1
    # the scheduler takes it between the read and the write: the buttons that go out stay the record
    read = store.get_post(rec["id"])
    assert store.claim_post(rec["id"])
    monkeypatch.setattr(posts, "_get", lambda pid: read)
    r = client.post(f"/api/posts/{rec['id']}/buttons", json={"reply_markup": None})
    assert r.status_code == 400 and "уже отправляется" in r.json()["detail"]
    p = store.get_post(rec["id"])
    assert p["payload"]["reply_markup"] == kb and p["version"] == 1


def test_refused_buttons_give_the_version_back(client, tg):
    pid = client.post("/api/send", json={"post": {"rich_message": {"markdown": "пост"}}}).json()["post"]["id"]
    tg.fail["editMessageReplyMarkup"] = "Bad Request: message can't be edited"
    kb = {"inline_keyboard": [[{"text": "Сайт", "url": "https://t.me/telegram"}]]}
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": kb})
    assert r.status_code == 422
    p = store.get_post(pid)
    assert p["version"] == 0 and not p["payload"].get("reply_markup")
    assert client.get(f"/api/posts/{pid}/versions").json() == []


def test_buttons_cut_off_mid_request_may_have_changed(client, tg, monkeypatch):
    """The request reached Telegram, then the connection dropped: say so, keep the version taken."""
    import telegram

    pid = client.post("/api/send", json={"post": {"rich_message": {"markdown": "пост"}}}).json()["post"]["id"]

    async def timeout(method, params=None, timeout=60):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(telegram, "call", timeout)
    kb = {"inline_keyboard": [[{"text": "Сайт", "url": "https://t.me/telegram"}]]}
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": kb})
    assert r.status_code != 500 and "Кнопки могли смениться" in str(r.json()["detail"])
    assert store.get_post(pid)["version"] == 1
