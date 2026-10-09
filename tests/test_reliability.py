"""Posts are neither lost nor sent twice: edits, late or unreachable schedules, 5xx, first comments, files, drafts."""

import asyncio
import time

from starlette.responses import PlainTextResponse

import settings
import store
from services import scheduler, updates
from test_api import BLOCKS_POST

BUTTON = {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]}


def sends(tg) -> int:
    return len([c for c in tg.calls if c["method"] == "sendRichMessage"])


def schedule(client, at=None, post=None) -> dict:
    body = {"post": post or BLOCKS_POST, "at": "2099-01-01T09:00:00+03:00"}
    rec = client.post("/api/schedule", json=body).json()
    store.update_post(rec["id"], scheduled_at=at if at is not None else int(time.time()) - 5)
    return rec


# ---------- replacing a published post ----------
def test_edit_keeps_button_answers_and_first_comment(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "updates_on", True)
    post = {
        **BLOCKS_POST,
        "reply_markup": BUTTON,
        "callbacks": {"like": "Спасибо"},
        "comment": {"html": "<p>Ссылки</p>"},
    }
    pid = client.post("/api/send", json={"post": post}).json()["post"]["id"]
    # an agent sends only the new text and the same buttons
    new = {"rich_message": {"markdown": "# Исправлено"}, "reply_markup": BUTTON}
    p = client.post(f"/api/posts/{pid}/edit", json=new).json()
    assert p["payload"]["rich_message"] == new["rich_message"]
    assert p["payload"]["callbacks"] == {"like": "Спасибо"}
    assert p["payload"]["comment"] == {"html": "<p>Ссылки</p>"}
    click = {"id": "cq", "data": "like", "message": {"message_id": p["message_id"], "chat": {"id": -1001}}}
    asyncio.run(updates.handle_update({"update_id": 1, "callback_query": click}))
    assert tg.last("answerCallbackQuery")["body"]["text"] == "Спасибо"


def test_edit_that_changes_only_button_answers_is_not_an_error(client, tg):
    post = {**BLOCKS_POST, "reply_markup": BUTTON, "callbacks": {"like": "Спасибо"}}
    pid = client.post("/api/send", json={"post": post}).json()["post"]["id"]
    r = client.post(f"/api/posts/{pid}/edit", json={**post, "callbacks": {"like": "Голос учтён"}})
    assert r.status_code == 200, r.text  # Telegram says "message is not modified": nothing to change there
    assert r.json()["payload"]["callbacks"] == {"like": "Голос учтён"}


def test_edit_with_nothing_new_adds_no_version(client, tg):
    # found on real Telegram: a repeated edit filled the history with copies of the current post
    post = {**BLOCKS_POST, "reply_markup": BUTTON, "callbacks": {"like": "Спасибо"}}
    pid = client.post("/api/send", json={"post": post}).json()["post"]["id"]
    assert client.post(f"/api/posts/{pid}/edit", json=post).status_code == 200
    assert client.get(f"/api/posts/{pid}/versions").json() == []
    client.post(f"/api/posts/{pid}/edit", json={**post, "callbacks": {"like": "Голос учтён"}})
    assert len(client.get(f"/api/posts/{pid}/versions").json()) == 1


def test_same_buttons_again_is_not_an_error(client, tg):
    # real Telegram answers editMessageReplyMarkup with the same buttons by "message is not modified"
    pid = client.post("/api/send", json={"post": {**BLOCKS_POST, "reply_markup": BUTTON}}).json()["post"]["id"]
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": BUTTON, "callbacks": {"like": "Спасибо"}})
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["callbacks"] == {"like": "Спасибо"}


def test_edit_of_a_post_already_taken_by_the_scheduler_is_refused(client, tg):
    rec = schedule(client, at=int(time.time()) + 3600)
    assert store.claim_post(rec["id"])
    r = client.post(f"/api/posts/{rec['id']}/edit", json={"rich_message": {"markdown": "новое"}})
    assert r.status_code == 400 and "sending" in r.json()["detail"]
    # the same check inside the update itself, for an edit that read the post just before the claim
    assert not store.replace_scheduled(rec["id"], {"rich_message": {"markdown": "новое"}})
    assert store.get_post(rec["id"])["payload"]["rich_message"] == BLOCKS_POST["rich_message"]


# ---------- scheduler ----------
def test_post_far_past_its_time_is_missed_not_sent(client, tg):
    rec = schedule(client, at=int(time.time()) - 3 * 3600)
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "missed" and "3 ч" in p["error"]
    assert sends(tg) == 0


def test_late_limit_can_be_turned_off(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "schedule_late_min", 0)
    rec = schedule(client, at=int(time.time()) - 3 * 3600)
    asyncio.run(scheduler.run_due_posts())
    assert store.get_post(rec["id"])["status"] == "sent"


def test_due_posts_go_out_in_their_order(client, tg):
    now = int(time.time())
    late = schedule(client, at=now - 60, post={"rich_message": {"markdown": "второй"}})
    early = schedule(client, at=now - 120, post={"rich_message": {"markdown": "первый"}})
    asyncio.run(scheduler.run_due_posts())
    sent = [c["body"]["rich_message"]["markdown"] for c in tg.calls if c["method"] == "sendRichMessage"]
    assert sent == ["первый", "второй"]
    assert store.get_post(early["id"])["message_id"] < store.get_post(late["id"])["message_id"]


def test_unreachable_telegram_is_retried_not_failed(client, tg, monkeypatch):
    rec = schedule(client)
    real_base = settings.get().api_base
    monkeypatch.setattr(settings.get(), "api_base", "http://127.0.0.1:9")  # nothing listens: connection refused
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "scheduled" and p["retry_at"] > time.time() and "нет связи" in p["error"]
    asyncio.run(scheduler.run_due_posts())  # not before retry_at
    assert store.get_post(rec["id"])["status"] == "scheduled"
    monkeypatch.setattr(settings.get(), "api_base", real_base)
    store.update_post(rec["id"], retry_at=int(time.time()) - 1)
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "sent" and p["error"] is None and sends(tg) == 1


def test_long_flood_wait_is_retried_later(client, tg):
    rec = schedule(client)
    tg.queue["sendRichMessage"] = [
        {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 120}}
    ]
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "scheduled" and p["retry_at"] >= time.time() + 100


def test_scheduler_sends_the_content_as_it_is_now(client, tg, monkeypatch):
    rec = schedule(client)
    stale = store.due_posts()  # the scheduler read the queue, then the person edited the post
    client.post(f"/api/posts/{rec['id']}/edit", json={"rich_message": {"markdown": "свежий текст"}})
    monkeypatch.setattr(store, "due_posts", lambda: stale)
    asyncio.run(scheduler.run_due_posts())
    assert tg.last("sendRichMessage")["body"]["rich_message"] == {"markdown": "свежий текст"}


# ---------- 5xx from Telegram: may have gone ----------
def test_gateway_error_means_may_have_gone_and_the_key_is_kept(client, tg):
    tg.queue["sendRichMessage"] = [PlainTextResponse("<html>502 Bad Gateway</html>", status_code=502)]
    h = {"Idempotency-Key": "k-502"}
    first = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert first.status_code == 502 and "мог уйти" in first.json()["detail"]
    again = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert again.status_code == 502 and "мог уйти" in again.json()["detail"]
    assert sends(tg) == 1


def test_error_code_500_in_json_is_also_uncertain(client, tg):
    tg.queue["sendRichMessage"] = [{"ok": False, "error_code": 500, "description": "Internal Server Error"}]
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    assert r.status_code == 502 and "мог уйти" in r.json()["detail"]


def test_gateway_error_on_other_calls_is_a_clear_502(client, tg):
    tg.queue["getMe"] = [PlainTextResponse("bad gateway", status_code=502)]
    r = client.get("/api/check")
    assert r.status_code == 502 and "проверьте" in r.json()["detail"]


def test_unreachable_telegram_on_send_frees_the_key(client, tg, monkeypatch):
    real_base = settings.get().api_base
    monkeypatch.setattr(settings.get(), "api_base", "http://127.0.0.1:9")
    h = {"Idempotency-Key": "k-down"}
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers=h)
    assert r.status_code == 502 and "не отправлен" in r.json()["detail"]
    monkeypatch.setattr(settings.get(), "api_base", real_base)
    assert client.post("/api/send", json={"post": BLOCKS_POST}, headers=h).status_code == 200


# ---------- first comment ----------
def test_first_comment_with_updates_off_is_reported_not_left_waiting(client, tg):
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "comment": {"html": "<p>x</p>"}}}).json()
    assert r["post"]["comment_status"] == "skipped"
    assert any("TG_UPDATES" in w for w in r["warnings"])


def _forward(message_id: int) -> dict:
    return {
        "update_id": 9,
        "message": {
            "message_id": 77,
            "chat": {"id": -2002},
            "is_automatic_forward": True,
            "forward_origin": {"type": "channel", "chat": {"id": -1001}, "message_id": message_id},
        },
    }


def test_network_error_on_first_comment_is_recorded(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "updates_on", True)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "comment": {"html": "<p>x</p>"}}}).json()

    import telegram

    async def timeout(method, params=None, timeout=60):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(telegram, "call", timeout)
    asyncio.run(updates.handle_update(_forward(r["message"]["message_id"])))
    p = store.get_post(r["post"]["id"])
    assert p["comment_status"] == "failed" and "мог уйти" in p["error"]


def test_comment_waiting_for_more_than_a_day_expires(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "updates_on", True)
    r = client.post("/api/send", json={"post": {**BLOCKS_POST, "comment": {"html": "<p>x</p>"}}}).json()
    pid = r["post"]["id"]
    assert store.expire_comments(int(time.time()) - updates.COMMENT_WAIT, "старое") == 0
    store.update_post(pid, sent_at=int(time.time()) - updates.COMMENT_WAIT - 10)
    assert store.expire_comments(int(time.time()) - updates.COMMENT_WAIT, "не пришла") == 1
    assert store.get_post(pid)["comment_status"] == "failed"


# ---------- files still in use ----------
def test_file_needed_by_a_scheduled_post_or_draft_is_kept(client, tg):
    client.post("/api/media", files={"file": ("cat.png", b"\x89PNG", "image/png")})
    photo = {"rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": "upload:cat.png"}}]}}
    schedule(client, at=int(time.time()) + 3600, post=photo)
    client.post("/api/drafts", json={"title": "Кот", "post": photo})
    r = client.delete("/api/media/cat.png")
    assert r.status_code == 409
    assert r.json()["detail"]["used_by"] == ["отложенный пост в @test_channel", "черновик «Кот»"]
    assert any(f["name"] == "cat.png" for f in client.get("/api/media").json())
    assert client.delete("/api/media/cat.png?force=true").status_code == 200
    assert not client.get("/api/media").json()


def test_unused_file_is_deleted_at_once(client):
    client.post("/api/media", files={"file": ("dog.png", b"\x89PNG", "image/png")})
    client.post("/api/drafts", json={"title": "Другое", "post": {"rich_message": {"markdown": "upload:dog.png.bak"}}})
    assert client.delete("/api/media/dog.png").status_code == 200


# ---------- the same draft in two tabs ----------
def test_stale_draft_save_is_refused(client):
    d = client.post("/api/drafts", json={"title": "A", "post": {"v": 1}}).json()
    tab1 = client.put(f"/api/drafts/{d['id']}", json={"title": "A", "post": {"v": 2}, "version": d["version"]}).json()
    assert tab1["version"] == d["version"] + 1
    stale = client.put(f"/api/drafts/{d['id']}", json={"title": "A", "post": {"v": 3}, "version": d["version"]})
    assert stale.status_code == 409
    assert stale.json()["detail"]["current"]["payload"] == {"v": 2}
    # "keep mine": a save without a version overwrites
    mine = client.put(f"/api/drafts/{d['id']}", json={"title": "A", "post": {"v": 3}}).json()
    assert mine["payload"] == {"v": 3} and mine["version"] == tab1["version"] + 1


def test_draft_deleted_in_another_tab_does_not_come_back(client):
    d = client.post("/api/drafts", json={"title": "A", "post": {"v": 1}}).json()
    client.delete(f"/api/drafts/{d['id']}")
    r = client.put(f"/api/drafts/{d['id']}", json={"title": "A", "post": {"v": 2}, "version": d["version"]})
    assert r.status_code == 409 and r.json()["detail"]["current"] is None
    assert client.get(f"/api/drafts/{d['id']}").status_code == 404
