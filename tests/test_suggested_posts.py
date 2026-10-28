"""Suggested posts: they come from the channel's direct messages and are approved or declined from the studio."""

import asyncio
import time

from services import updates
from test_access import token
from test_mcp import call, call_list

DM = {"id": -1009, "type": "supergroup", "title": "Тест Direct Messages", "is_direct_messages": True}


def _suggestion(message_id: int, info: dict, text: str = "Мой пост для канала") -> dict:
    return {
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "chat": DM,
            "from": {"id": 5, "first_name": "Аня", "username": "anya"},
            "direct_messages_topic": {"topic_id": 77, "user": {"id": 5, "first_name": "Аня"}},
            "text": text,
            "suggested_post_info": info,
        }
    }


def test_a_suggestion_is_listed_and_approved(client, tg):
    paid = {"state": "pending", "price": {"currency": "XTR", "amount": 100}}
    assert asyncio.run(updates.handle_update(_suggestion(10, paid))) == "suggested_post"
    [s] = client.get("/api/suggested-posts", params={"state": "pending"}).json()
    assert s["id"] == "-1009:10" and s["text"] == "Мой пост для канала" and s["price_amount"] == 100
    assert s["author"] == "Аня (@anya)" and s["channel_title"] == "Тест" and s["topic_id"] == 77
    # the author edits it: the same row, new text
    edited = _suggestion(10, paid, "Исправленный пост")
    asyncio.run(updates.handle_update({"edited_message": edited["message"]}))
    assert client.get("/api/suggested-posts").json()[0]["text"] == "Исправленный пост"
    at = int(time.time()) + 3600
    far = client.post("/api/suggested-posts/-1009:10/approve", json={"send_date": int(time.time()) + 2700000})
    assert far.status_code == 400 and "30 дней" in far.json()["detail"]
    r = client.post("/api/suggested-posts/-1009:10/approve", json={"send_date": at}, headers={"X-Studio-Client": "ui"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved" and r.json()["actor"] == "ui"
    assert tg.last("approveSuggestedPost")["body"] == {"chat_id": -1009, "message_id": 10, "send_date": at}
    # the same update delivered again (a restart before the offset was saved) does not undo the decision
    asyncio.run(updates.handle_update(_suggestion(10, paid)))
    assert client.get("/api/suggested-posts").json()[0]["state"] == "approved"
    again = client.post("/api/suggested-posts/-1009:10/approve", json={})
    assert again.status_code == 400 and "решение" in again.json()["detail"]
    # Telegram reports the payment afterwards
    paid_msg = {
        "message_id": 11,
        "date": int(time.time()),
        "chat": DM,
        "suggested_post_paid": {
            "suggested_post_message": {"message_id": 10, "date": 1},
            "currency": "XTR",
            "star_amount": {"amount": 85},
        },
    }
    assert asyncio.run(updates.handle_update({"message": paid_msg})) == "suggested_post_outcome"
    assert client.get("/api/suggested-posts").json()[0]["state"] == "paid"


def test_decline_with_a_comment_and_the_author_time_is_kept(client, tg):
    asyncio.run(updates.handle_update(_suggestion(20, {"state": "pending", "send_date": int(time.time()) + 600})))
    r = client.post("/api/suggested-posts/-1009:20/approve", json={"send_date": int(time.time()) + 7200})
    assert r.status_code == 400 and "автор" in r.json()["detail"]
    assert client.post("/api/suggested-posts/-1009:20/decline", json={"comment": "x" * 129}).status_code == 400
    r = client.post("/api/suggested-posts/-1009:20/decline", json={"comment": "Не по теме канала"})
    assert r.json()["state"] == "declined" and r.json()["comment"] == "Не по теме канала"
    assert tg.last("declineSuggestedPost")["body"]["comment"] == "Не по теме канала"
    assert client.post("/api/suggested-posts/-1:1/decline", json={}).status_code == 404


def test_outcome_of_a_suggestion_the_studio_did_not_see(client, tg):
    original = {"message_id": 30, "date": 1, "chat": DM, "text": "Старый пост", "from": {"id": 6, "first_name": "Боб"}}
    msg = {"message_id": 31, "date": 1, "chat": DM, "suggested_post_declined": {"suggested_post_message": original}}
    assert asyncio.run(updates.handle_update({"message": msg})) == "suggested_post_outcome"
    assert client.get("/api/suggested-posts", params={"state": "declined"}).json()[0]["text"] == "Старый пост"
    # an ordinary message in the same chat is not a suggestion
    plain = {"message_id": 32, "date": 1, "chat": DM, "text": "привет"}
    assert asyncio.run(updates.handle_update({"message": plain})) is None


def test_suggested_posts_need_publish_and_work_through_mcp(client, tg):
    asyncio.run(updates.handle_update(_suggestion(40, {"state": "pending"})))
    assert [s["id"] for s in call_list(client, "list_suggested_posts")] == ["-1009:40"]
    _, s, _ = call(client, "approve_suggested_post", suggestion_id="-1009:40")
    assert s["state"] == "approved" and "send_date" not in tg.last("approveSuggestedPost")["body"]
    draft = token("d", "draft")
    assert client.post("/api/suggested-posts/-1009:40/decline", json={}, headers=draft).status_code == 403


def test_a_decision_goes_to_telegram_once(client, tg, monkeypatch):
    import telegram
    from services import suggested_posts

    asyncio.run(updates.handle_update(_suggestion(30, {"state": "pending"})))
    # Telegram refused: nothing changed, the suggestion waits again
    tg.fail["approveSuggestedPost"] = "Bad Request: not enough rights"
    assert client.post("/api/suggested-posts/-1009:30/approve", json={}).status_code == 422
    assert client.get("/api/suggested-posts").json()[0]["state"] == "pending"
    tg.fail.clear()
    tg.calls.clear()

    async def timeout(method, params=None, timeout=60):
        tg.calls.append({"method": method, "body": params, "files": {}})
        raise TimeoutError("read timeout")

    monkeypatch.setattr(telegram, "call", timeout)
    r = client.post("/api/suggested-posts/-1009:30/approve", json={})
    assert r.status_code == 502 and "могло дойти" in r.json()["detail"]
    [s] = client.get("/api/suggested-posts", params={"state": "open"}).json()
    assert s["state"] == "approving"
    # no repeat after "may have gone": not a retry, not a decline
    for path in ("approve", "decline"):
        again = client.post(f"/api/suggested-posts/-1009:30/{path}", json={})
        assert again.status_code == 400 and "могло дойти" in again.json()["detail"]
    assert len([c for c in tg.calls if c["method"] == "approveSuggestedPost"]) == 1
    # the outcome settles it
    done = {
        "message_id": 31,
        "date": 1,
        "chat": DM,
        "suggested_post_approved": {"suggested_post_message": {"message_id": 30, "date": 1}},
    }
    asyncio.run(updates.handle_update({"message": done}))
    assert client.get("/api/suggested-posts").json()[0]["state"] == "approved"
    # two clicks at once: only one takes the suggestion
    asyncio.run(updates.handle_update(_suggestion(40, {"state": "pending"})))
    from store import suggested_posts as rows

    assert rows.claim("-1009:40", suggested_posts.DECIDABLE, "approving")
    assert not rows.claim("-1009:40", suggested_posts.DECIDABLE, "approving")


def test_failed_approval_can_be_tried_again_and_late_copies_do_not_go_back(client, tg):
    asyncio.run(updates.handle_update(_suggestion(50, {"state": "pending", "price": {"currency": "XTR", "amount": 5}})))
    failed = {
        "message_id": 51,
        "date": 1,
        "chat": DM,
        "suggested_post_approval_failed": {
            "suggested_post_message": {"message_id": 50, "date": 1},
            "price": {"currency": "XTR", "amount": 5},
        },
    }
    asyncio.run(updates.handle_update({"message": failed}))
    assert client.get("/api/suggested-posts", params={"state": "open"}).json()[0]["state"] == "failed"
    r = client.post("/api/suggested-posts/-1009:50/approve", json={})
    assert r.status_code == 200 and r.json()["state"] == "approved"
    paid = {
        "message_id": 52,
        "date": 1,
        "chat": DM,
        "suggested_post_paid": {
            "suggested_post_message": {"message_id": 50, "date": 1},
            "currency": "XTR",
            "star_amount": {"amount": 5},
        },
    }
    asyncio.run(updates.handle_update({"message": paid}))
    # an edit that still says approved comes late: the payment stays recorded
    asyncio.run(updates.handle_update({"edited_message": _suggestion(50, {"state": "approved"})["message"]}))
    assert client.get("/api/suggested-posts").json()[0]["state"] == "paid"
