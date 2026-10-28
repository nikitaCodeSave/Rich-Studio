"""Live Photo and paid media: messages of their own next to rich posts, with the same send policy."""

import time

from test_access import token
from test_mcp import call


def _upload(client, name: str, data: bytes = b"x") -> str:
    r = client.post("/api/media", files={"file": (name, data)})
    assert r.status_code == 200, r.text
    return r.json()["ref"]


def test_live_photo_goes_with_both_files(client, tg):
    clip, still = _upload(client, "clip.mp4"), _upload(client, "still.jpg")
    body = {"kind": "live_photo", "params": {"live_photo": clip, "photo": still, "caption": "Закат"}, "pin": True}
    r = client.post("/api/media-post", json=body)
    assert r.status_code == 200, r.text
    rec = r.json()["results"][0]["post"]
    assert rec["kind"] == "live_photo" and rec["status"] == "sent"
    sent = tg.last("sendLivePhoto")
    assert set(sent["files"]) == {"f0", "f1"} and sent["body"]["live_photo"].startswith("attach://")
    assert sent["body"]["photo"].startswith("attach://") and sent["body"]["caption"] == "Закат"
    assert tg.last("pinChatMessage")["body"]["message_id"] == rec["message_id"]
    listed = next(p for p in client.get("/api/posts").json() if p["id"] == rec["id"])
    assert listed["title"] == "Закат" and listed["thumb"] == "/media/still.jpg"
    # not a rich post: it cannot be replaced by a post
    r = client.post(f"/api/posts/{rec['id']}/edit", json={"rich_message": {"markdown": "x"}})
    assert r.status_code == 400 and "Live Photo" in r.json()["detail"]
    # buttons can change; they stay among its own parameters, not next to them
    keyboard = {"inline_keyboard": [[{"text": "Сайт", "url": "https://t.me/telegram"}]]}
    r = client.post(f"/api/posts/{rec['id']}/buttons", json={"reply_markup": keyboard})
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["live_photo"]["reply_markup"] == keyboard and "reply_markup" not in r.json()["payload"]
    assert tg.last("editMessageReplyMarkup")["body"]["reply_markup"]


def test_paid_media_is_checked_and_can_wait(client, tg):
    pic = _upload(client, "pic.jpg")
    bad = client.post("/api/media-post", json={"kind": "paid_media", "params": {"star_count": 5, "media": []}})
    assert bad.status_code == 422 and "от 1 до 10" in str(bad.json())
    missing = client.post(
        "/api/media-post", json={"kind": "paid_media", "params": {"media": [{"type": "photo", "media": pic}]}}
    )
    assert missing.status_code == 422 and "star_count" in str(missing.json())
    for stars in (0, 25001):  # a scheduled one would only fail at its time
        price = {"star_count": stars, "media": [{"type": "photo", "media": pic}]}
        r = client.post("/api/media-post", json={"kind": "paid_media", "params": price, "at": int(time.time()) + 60})
        assert r.status_code == 422 and "25000" in str(r.json())
    assert client.post("/api/media-post", json={"kind": "voice", "params": {}}).status_code == 422
    params = {"star_count": 5, "media": [{"type": "photo", "media": pic}], "caption": "Закрытый кадр"}
    at = int(time.time()) + 3600
    r = client.post("/api/media-post", json={"kind": "paid_media", "params": params, "at": at})
    assert r.status_code == 200 and r.json()["scheduled"][0]["kind"] == "paid_media"
    assert not tg.calls
    # the upload is in use by a scheduled message: deleting it needs force
    assert client.delete("/api/media/pic.jpg").status_code == 409
    now = client.post("/api/media-post", json={"kind": "paid_media", "params": params}).json()
    assert now["results"][0]["ok"] and tg.last("sendPaidMedia")["body"]["star_count"] == "5"


def test_media_post_needs_publish(client, tg):
    draft = token("d", "draft")
    assert client.post("/api/media-post", json={"kind": "live_photo", "params": {}}, headers=draft).status_code == 403
    assert not tg.calls


def test_media_post_through_mcp(client, tg):
    clip, still = _upload(client, "a.mp4"), _upload(client, "a.jpg")
    _, r, text = call(client, "send_media_post", kind="live_photo", params={"live_photo": clip, "photo": still})
    assert r and r["results"][0]["ok"], text
    call(client, "send_media_post", kind="live_photo", params={"live_photo": clip, "photo": still})
    assert len([c for c in tg.calls if c["method"] == "sendLivePhoto"]) == 1  # the same request is not sent twice
