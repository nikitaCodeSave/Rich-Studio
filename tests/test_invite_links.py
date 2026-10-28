"""Named invite links: made in Telegram, counted by the studio from chat_member and chat_join_request."""

import asyncio
import time

from services import updates
from test_access import token
from test_mcp import call, call_list


def _member(link: str, old: str, new: str, **extra) -> dict:
    return {
        "chat_member": {
            "chat": {"id": -1001, "type": "channel"},
            "from": {"id": 7},
            "date": int(time.time()),
            "old_chat_member": {"status": old, "user": {"id": 7}},
            "new_chat_member": {"status": new, "user": {"id": 7}, **extra},
            "invite_link": {"invite_link": link, "creator": {"id": 42}, "is_primary": False, "is_revoked": False},
        }
    }


def test_named_link_counts_who_joined_through_it(client, tg):
    r = client.post("/api/invite-links", json={"chat_id": "@chan", "name": "Хабр", "member_limit": 100})
    assert r.status_code == 200, r.text
    link = r.json()
    assert link["name"] == "Хабр" and link["member_limit"] == 100 and link["joined"] == 0
    assert tg.last("createChatInviteLink")["body"] == {"chat_id": "@chan", "name": "Хабр", "member_limit": 100}
    url = link["invite_link"]
    assert asyncio.run(updates.handle_update(_member(url, "left", "member"))) == "join"
    assert asyncio.run(updates.handle_update(_member(url, "kicked", "restricted", is_member=True))) == "join"
    # an admin promoted, a link the studio did not make: nothing to count
    assert asyncio.run(updates.handle_update(_member(url, "member", "administrator"))) is None
    assert asyncio.run(updates.handle_update(_member("https://t.me/+other", "left", "member"))) is None
    listed = client.get("/api/invite-links", params={"chat_id": "@chan"}).json()
    assert [(x["name"], x["joined"]) for x in listed] == [("Хабр", 2)]
    assert client.get("/api/invite-links", params={"chat_id": "@other"}).json() == []
    assert "chat_member" in updates.ALLOWED_UPDATES and "chat_join_request" in updates.ALLOWED_UPDATES


def test_join_requests_expiry_and_revoke(client, tg):
    at = int(time.time()) + 86400
    link = client.post(
        "/api/invite-links", json={"name": "Заявки", "creates_join_request": True, "expire_date": at}
    ).json()
    assert link["creates_join_request"] == 1 and link["expire_date"] == at
    sent = tg.last("createChatInviteLink")["body"]
    assert sent["chat_id"] == "@test_channel" and sent["creates_join_request"] is True  # TG_DEFAULT_CHAT
    req = {
        "chat_join_request": {
            "chat": {"id": -1001},
            "from": {"id": 8},
            "invite_link": {"invite_link": link["invite_link"]},
        }
    }
    assert asyncio.run(updates.handle_update(req)) == "join_request"
    r = client.post("/api/invite-links", json={"creates_join_request": True, "member_limit": 5})
    assert r.status_code == 400 and "заяв" in r.json()["detail"]
    assert client.post("/api/invite-links", json={"name": "x" * 33}).status_code == 400
    edited = client.patch("/api/invite-links", json={"invite_link": link["invite_link"], "name": "Заявки 2"}).json()
    assert edited["name"] == "Заявки 2" and edited["requests"] == 1 and not edited["expire_date"]
    revoked = client.post("/api/invite-links/revoke", json={"invite_link": link["invite_link"]}).json()
    assert revoked["is_revoked"] == 1 and revoked["name"] == "Заявки 2"
    assert client.patch("/api/invite-links", json={"invite_link": link["invite_link"]}).status_code == 400
    assert client.post("/api/invite-links/revoke", json={"invite_link": "https://t.me/+nope"}).status_code == 404


def test_paid_subscription_link(client, tg):
    link = client.post("/api/invite-links", json={"name": "Клуб", "subscription_price": 50}).json()
    assert link["kind"] == "subscription" and link["subscription_price"] == 50
    body = tg.last("createChatSubscriptionInviteLink")["body"]
    assert body["subscription_period"] == 2592000 and body["subscription_price"] == 50
    r = client.post("/api/invite-links", json={"subscription_price": 50, "member_limit": 3})
    assert r.status_code == 400
    r = client.patch("/api/invite-links", json={"invite_link": link["invite_link"], "name": "Клуб+", "member_limit": 3})
    assert r.status_code == 400
    edited = client.patch("/api/invite-links", json={"invite_link": link["invite_link"], "name": "Клуб+"}).json()
    assert edited["name"] == "Клуб+" and tg.last("editChatSubscriptionInviteLink")["body"]["name"] == "Клуб+"


def test_links_need_publish(client, tg):
    draft = token("d", "draft")
    assert client.post("/api/invite-links", json={}, headers=draft).status_code == 403
    assert client.patch("/api/invite-links", json={"invite_link": "x"}, headers=draft).status_code == 403
    assert client.post("/api/invite-links/revoke", json={"invite_link": "x"}, headers=draft).status_code == 403
    assert client.get("/api/invite-links", headers=draft).status_code == 200
    assert not tg.calls


def test_invite_links_through_mcp(client, tg):
    _, link, _ = call(client, "create_invite_link", chat_id="@chan", name="Telegram-чат")
    assert link["invite_link"].startswith("https://t.me/+")
    assert [x["name"] for x in call_list(client, "list_invite_links")] == ["Telegram-чат"]
    _, revoked, _ = call(client, "revoke_invite_link", invite_link=link["invite_link"])
    assert revoked["is_revoked"] == 1
