"""Editing a post: versions before each replacement, moving a scheduled or missed post, the working-copy draft."""

import time

import store
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
