import asyncio
import json
import re
import time
from pathlib import Path

import pytest

import settings
from services import scheduler, updates

BLOCKS_POST = {
    "rich_message": {
        "blocks": [
            {"type": "heading", "size": 1, "text": "Тест"},
            {"type": "paragraph", "text": ["Привет, ", {"type": "bold", "text": "мир"}]},
        ]
    }
}


def post_json(client, path, body, **kw):
    return client.post(path, json=body, **kw)


# ---------- spec ----------
def test_spec_summary_has_all_rich_blocks(client):
    s = client.get("/api/spec").json()
    assert s["version"] == "Bot API 10.3"
    subtypes = s["types"]["InputRichBlock"]["subtypes"]
    assert len(subtypes) == 24
    consts = {s["types"][n]["const"] for n in subtypes}
    assert {"paragraph", "heading", "table", "buttons", "document", "map", "slideshow"} <= consts
    assert None not in consts
    assert s["types"]["RichMessageButton"]["fields"][1]["enum"] == ["danger", "success", "primary", "link"]


def test_spec_type_lookup(client):
    assert client.get("/api/spec/type/sendRichMessage").json()["fields"]
    assert client.get("/api/spec/type/NoSuchType").status_code == 404


def test_diff_specs_reports_new_rich_features():
    from spec import diff_specs

    old = {
        "types": {
            "InputRichBlock": {"subtypes": ["InputRichBlockParagraph"]},
            "InputRichBlockTable": {"fields": [{"name": "cells", "types": ["X"]}]},
        },
        "methods": {},
    }
    new = {
        "types": {
            "InputRichBlock": {"subtypes": ["InputRichBlockParagraph", "InputRichBlockPoll"]},
            "InputRichBlockPoll": {"description": ["A poll block."], "fields": []},
            "InputRichBlockTable": {
                "fields": [{"name": "cells", "types": ["X"]}, {"name": "is_compact", "types": ["Boolean"]}]
            },
        },
        "methods": {"sendRichPoll": {"description": ["Sends a poll."], "fields": []}},
    }
    kinds = {(i["kind"], i["name"]) for i in diff_specs(old, new)}
    assert ("subtype_added", "InputRichBlock → InputRichBlockPoll") in kinds
    assert ("type_added", "InputRichBlockPoll") in kinds
    assert ("field_added", "InputRichBlockTable.is_compact") in kinds
    assert ("method_added", "sendRichPoll") in kinds


# ---------- validation ----------
def test_examples_are_valid(client, examples):
    blocks = json.loads((examples / "post-blocks.json").read_text("utf-8"))
    r = post_json(client, "/api/validate", {"rich_message": {"blocks": blocks}}).json()
    assert r["ok"], r
    html = (examples / "post-showcase.html").read_text("utf-8")
    r = post_json(client, "/api/validate", {"rich_message": {"html": html}}).json()
    assert r["ok"], r


@pytest.mark.parametrize(
    "rm, fragment",
    [
        ({"blocks": [{"type": "paragraf", "text": "x"}]}, "неизвестный type='paragraf'"),
        ({"blocks": [{"type": "heading", "size": "2", "text": "x"}]}, "ожидалось целое число"),
        ({"blocks": [{"type": "table"}]}, "нет обязательного поля cells"),
        ({"html": '<img src="tg://photo?id=cover">'}, "нет элемента с id='cover'"),
        ({"html": "<p>a</p>", "markdown": "b"}, "ровно одно из html / markdown / blocks"),
        ({}, "ровно одно из html / markdown / blocks"),
        ({"html": "<p>" + "я" * 33000 + "</p>"}, "лимит 32768"),
    ],
)
def test_validation_errors(client, rm, fragment):
    r = post_json(client, "/api/validate", {"rich_message": rm}).json()
    assert not r["ok"]
    assert any(fragment in e for e in r["errors"]), r["errors"]


def test_unknown_field_is_a_warning_not_an_error(client):
    """A field newer than the cached spec must still be sendable."""
    rm = {"blocks": [{"type": "paragraph", "text": "x", "future_field": 1}]}
    r = post_json(client, "/api/validate", {"rich_message": rm}).json()
    assert r["ok"]
    assert any("future_field" in w for w in r["warnings"])


@pytest.mark.parametrize("url", ["https://", "", "http:///path"])
def test_button_without_host_is_an_error(client, tg, url):
    """Telegram rejects the whole post (400 "URL host is empty"), so the check must say so before a send."""
    post = {**BLOCKS_POST, "reply_markup": {"inline_keyboard": [[{"text": "Сайт", "url": url}]]}}
    r = post_json(client, "/api/validate", post).json()
    assert not r["ok"]
    assert any("кнопка «Сайт» (ряд 1, №1)" in e for e in r["errors"]), r["errors"]
    assert post_json(client, "/api/send", {"post": post}).status_code == 422
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_good_button_links_pass(client):
    keyboard = [[{"text": "Сайт", "url": "https://t.me/telegram"}, {"text": "Бот", "url": "tg://resolve?domain=x"}]]
    r = post_json(client, "/api/validate", {**BLOCKS_POST, "reply_markup": {"inline_keyboard": keyboard}}).json()
    assert r["ok"], r


def test_rich_button_without_host_is_a_warning(client):
    rm = {"blocks": [{"type": "buttons", "buttons": [{"text": "Сайт", "url": "https://"}]}]}
    r = post_json(client, "/api/validate", {"rich_message": rm}).json()
    assert any("нет адреса сайта" in w for w in r["warnings"]), r


# ---------- sending ----------
def test_send_passes_payload_through_unchanged(client, tg):
    post = {
        "rich_message": {"html": "<h1>Привет</h1><tg-new-tag x='1'>future</tg-new-tag>", "is_rtl": False},
        "reply_markup": {"inline_keyboard": [[{"text": "Сайт", "url": "https://t.me"}]]},
        "options": {"disable_notification": True, "protect_content": True, "message_effect_id": "123"},
    }
    r = post_json(client, "/api/send", {"post": post})
    assert r.status_code == 200, r.text
    call = tg.last("sendRichMessage")
    assert call["token"] == "123:TEST"
    assert call["body"]["chat_id"] == "@test_channel"
    assert call["body"]["rich_message"] == post["rich_message"]
    assert call["body"]["reply_markup"] == post["reply_markup"]
    assert call["body"]["disable_notification"] is True and call["body"]["message_effect_id"] == "123"
    rec = r.json()["post"]
    assert rec["status"] == "sent" and rec["message_id"] == r.json()["message"]["message_id"]


def test_invalid_post_is_not_sent(client, tg):
    r = post_json(client, "/api/send", {"post": {"rich_message": {"blocks": [{"type": "nope"}]}}})
    assert r.status_code == 422
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_numeric_chat_id_becomes_integer(client, tg):
    post_json(client, "/api/send", {"post": BLOCKS_POST, "chat_id": "-1001234567890"})
    assert tg.last("sendRichMessage")["body"]["chat_id"] == -1001234567890


def test_telegram_error_is_reported(client, tg):
    tg.fail["sendRichMessage"] = "Bad Request: not enough rights to send rich messages"
    r = post_json(client, "/api/send", {"post": BLOCKS_POST, "chat_id": "@chan"})
    assert r.status_code == 422  # Telegram refused the request itself, not a gateway failure
    assert "not enough rights" in r.json()["detail"]
    # the refusal stays in the list of posts, not only in a toast
    [p] = client.get("/api/posts").json()
    assert p["status"] == "failed" and "not enough rights" in p["error"] and p["chat_id"] == "@chan"
    assert p["title"] == "Тест" and p["link"] is None


def test_one_failed_channel_of_two_is_listed(client, tg):
    tg.fail["sendRichMessage"] = "Bad Request: chat not found"
    r = post_json(client, "/api/send", {"post": BLOCKS_POST, "chat_ids": ["@a", "@b"]})
    assert r.status_code == 502
    assert [p["status"] for p in client.get("/api/posts").json()] == ["failed", "failed"]
    assert client.get("/api/posts?status=failed&chat=@a").json()[0]["chat_id"] == "@a"
    assert client.get("/api/posts?status=sent").json() == []


def test_refusal_leaves_the_list_once_the_draft_goes_out(client, tg):
    d = client.post("/api/drafts", json={"title": "A", "post": BLOCKS_POST}).json()
    tg.fail["sendRichMessage"] = "Bad Request: inline keyboard button URL 'https://' is invalid"
    assert (
        post_json(client, "/api/send", {"post": BLOCKS_POST, "draft_id": d["id"], "chat_id": "@c"}).status_code == 422
    )
    post_json(client, "/api/send", {"post": BLOCKS_POST, "draft_id": d["id"], "chat_id": "@other"})
    tg.fail.clear()
    assert (
        post_json(client, "/api/send", {"post": BLOCKS_POST, "draft_id": d["id"], "chat_id": "@c"}).status_code == 200
    )
    # the refusal to @c is gone; the one to @other (not resent) stays and can be removed by hand
    rows = client.get("/api/posts").json()
    assert [(p["status"], p["chat_id"]) for p in rows] == [("sent", "@c"), ("failed", "@other")]
    assert client.delete("/api/posts/" + rows[1]["id"]).json()["status"] == "removed"
    assert [p["status"] for p in client.get("/api/posts").json()] == ["sent"]


def test_failed_row_never_hides_telegrams_error(client, tg, monkeypatch):
    import store

    def broken(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "update_post", broken)
    tg.fail["sendRichMessage"] = "Bad Request: chat not found"
    r = post_json(client, "/api/send", {"post": BLOCKS_POST})
    assert r.status_code == 422 and "chat not found" in r.json()["detail"]


def test_no_send_without_a_row_first(client, tg, monkeypatch):
    """The row is written before Telegram is asked: a database that cannot write means nothing goes out."""
    import store

    def broken(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "add_post", broken)
    assert post_json(client, "/api/send", {"post": BLOCKS_POST}).status_code == 500
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_posts_list_shows_what_the_post_is(client, tg):
    up = client.post("/api/media", files={"file": ("cover.png", b"\x89PNG fake", "image/png")}).json()
    html = '<figure><img src="tg://photo?id=c"></figure><h1>Анонс &amp; эфир</h1><p>Подробности</p>'
    rm = {"html": html, "media": [{"id": "c", "media": {"type": "photo", "media": up["ref"]}}]}
    assert post_json(client, "/api/send", {"post": {"rich_message": rm}, "chat_id": "@my_chan"}).status_code == 200
    post_json(
        client,
        "/api/send",
        {"post": {"rich_message": {"markdown": "## **Итоги** недели\n\nтекст"}}, "chat_id": "-1001234567890"},
    )
    md, first = client.get("/api/posts").json()
    assert first["title"] == "Анонс & эфир" and first["thumb"] == "/media/" + up["ref"][len("upload:") :]
    assert first["link"] == f"https://t.me/my_chan/{first['message_id']}" and first["pinned"] is False
    assert "payload" not in first
    assert md["title"] == "Итоги недели" and md["thumb"] is None
    assert md["link"] == f"https://t.me/c/1234567890/{md['message_id']}"


def test_upload_and_send_as_multipart(client, tg):
    up = client.post("/api/media", files={"file": ("обложка 1.png", b"\x89PNG fake", "image/png")}).json()
    assert up["kind"] == "photo" and up["ref"].startswith("upload:")
    post = {"rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": up["ref"]}}]}}
    r = post_json(client, "/api/send", {"post": post})
    assert r.status_code == 200, r.text
    call = tg.last("sendRichMessage")
    assert call["body"]["rich_message"]["blocks"][0]["photo"]["media"] == "attach://f0"
    assert call["files"]["f0"][1] == len(b"\x89PNG fake")
    # stored record keeps the upload reference, so re-sending works
    assert (
        client.get("/api/posts/" + r.json()["post"]["id"]).json()["payload"]["rich_message"]["blocks"][0]["photo"][
            "media"
        ]
        == up["ref"]
    )


def test_upload_names_are_safe_and_unique(client):
    a = client.post("/api/media", files={"file": ("../../etc/passwd", b"x", "text/plain")}).json()
    assert "/" not in a["name"] and ".." not in a["name"]
    b1 = client.post("/api/media", files={"file": ("same.jpg", b"1", "image/jpeg")}).json()
    b2 = client.post("/api/media", files={"file": ("same.jpg", b"2", "image/jpeg")}).json()
    assert b1["name"] != b2["name"] and b2["name"].endswith(".jpg")
    assert client.get("/media/" + b2["name"]).content == b"2"
    assert client.get("/media/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_missing_upload_is_an_error(client, tg):
    post = {"rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": "upload:nope.png"}}]}}
    r = post_json(client, "/api/send", {"post": post})
    assert r.status_code == 400 and "не найден" in r.json()["detail"]


def test_preview_send_goes_to_preview_chat(client, tg):
    r = post_json(client, "/api/preview-send", {"post": BLOCKS_POST})
    assert r.status_code == 200
    assert tg.last("sendRichMessage")["body"]["chat_id"] == 777


# ---------- edit / delete ----------
def test_edit_published_post(client, tg):
    sent = post_json(client, "/api/send", {"post": BLOCKS_POST}).json()["post"]
    new = {"rich_message": {"markdown": "# Исправлено"}}
    r = post_json(client, f"/api/posts/{sent['id']}/edit", new)
    assert r.status_code == 200, r.text
    call = tg.last("editMessageText")["body"]
    assert call["message_id"] == sent["message_id"] and call["rich_message"] == new["rich_message"]
    assert client.get(f"/api/posts/{sent['id']}").json()["payload"]["rich_message"] == new["rich_message"]


def test_edit_by_message_id(client, tg):
    r = post_json(client, "/api/edit", {"post": BLOCKS_POST, "message_id": 55, "chat_id": "@other"})
    assert r.status_code == 200
    assert tg.last("editMessageText")["body"]["chat_id"] == "@other"


def test_edit_by_message_id_updates_the_studio_record(client, tg):
    rec = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "callbacks": {"like": "Спасибо"}}}).json()["post"]
    new = {"rich_message": {"html": "<p>новый текст</p>"}}
    r = post_json(client, "/api/edit", {"post": new, "message_id": rec["message_id"], "chat_id": rec["chat_id"]})
    assert r.status_code == 200
    payload = client.get("/api/posts/" + rec["id"]).json()["payload"]
    assert payload["rich_message"] == new["rich_message"]
    assert payload["callbacks"] == {"like": "Спасибо"}  # untouched by a text edit


def test_delete_published_post(client, tg):
    sent = post_json(client, "/api/send", {"post": BLOCKS_POST}).json()["post"]
    r = client.delete(f"/api/posts/{sent['id']}")
    assert r.json()["status"] == "deleted"
    assert tg.last("deleteMessage")["body"]["message_id"] == sent["message_id"]


# ---------- scheduling ----------
def test_schedule_rejects_past_time(client):
    r = post_json(client, "/api/schedule", {"post": BLOCKS_POST, "at": int(time.time()) - 3600})
    assert r.status_code == 400


def test_schedule_iso_with_timezone(client):
    r = post_json(client, "/api/schedule", {"post": BLOCKS_POST, "at": "2030-01-01T09:00:00+03:00"})
    assert r.json()["scheduled_at"] == 1893477600
    client.delete("/api/posts/" + r.json()["id"])


def test_scheduler_sends_due_posts_and_records_failures(client, tg):
    import store

    ok = post_json(client, "/api/schedule", {"post": BLOCKS_POST, "at": int(time.time())}).json()
    tg.fail["sendRichMessage"] = "Forbidden: bot was kicked"
    bad = post_json(client, "/api/schedule", {"post": BLOCKS_POST, "at": int(time.time()), "chat_id": "@gone"}).json()
    # first due post fails too (same failure flag), so send them one at a time
    store.update_post(bad["id"], scheduled_at=int(time.time()) + 3600)
    tg.fail.clear()
    assert asyncio.run(scheduler.run_due_posts()) >= 1
    assert client.get("/api/posts/" + ok["id"]).json()["status"] == "sent"
    store.update_post(bad["id"], scheduled_at=int(time.time()) - 1)
    tg.fail["sendRichMessage"] = "Forbidden: bot was kicked"
    asyncio.run(scheduler.run_due_posts())
    rec = client.get("/api/posts/" + bad["id"]).json()
    assert rec["status"] == "failed" and "kicked" in rec["error"]
    # nothing is sent twice
    tg.fail.clear()
    before = len([c for c in tg.calls if c["method"] == "sendRichMessage"])
    asyncio.run(scheduler.run_due_posts())
    assert len([c for c in tg.calls if c["method"] == "sendRichMessage"]) == before


def test_cancel_scheduled(client, tg):
    p = post_json(client, "/api/schedule", {"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    assert client.delete("/api/posts/" + p["id"]).json()["status"] == "cancelled"
    assert not [c for c in tg.calls if c["method"] == "deleteMessage"]


# ---------- drafts / render / misc ----------
def test_drafts_crud(client):
    d = post_json(client, "/api/drafts", {"title": "Анонс", "post": BLOCKS_POST}).json()
    assert client.get("/api/drafts/" + d["id"]).json()["payload"] == BLOCKS_POST
    client.put("/api/drafts/" + d["id"], json={"title": "Анонс 2", "post": {"rich_message": {"html": "<p>x</p>"}}})
    assert [x["title"] for x in client.get("/api/drafts").json() if x["id"] == d["id"]] == ["Анонс 2"]
    client.delete("/api/drafts/" + d["id"])
    assert client.get("/api/drafts/" + d["id"]).status_code == 404


def test_render_cannot_break_out_of_payload_script(client):
    evil = {"rich_message": {"html": "</script><script>alert(1)</script>"}}
    page = client.post("/api/render?theme=dark", json=evil).text
    assert "<script>alert(1)</script>" not in page
    assert "__THEME__" not in page and 'data-theme="dark"' in page


def test_render_theme_is_escaped(client):
    page = client.post('/api/render?theme=x";alert(1)//', json=BLOCKS_POST).text
    assert 'x";alert(1)' not in page


def test_check_bot_rights(client, tg):
    r = client.get("/api/check").json()
    assert r["can_post"] and r["bot"]["username"] == "studio_test_bot"


def test_raw_method_passthrough(client, tg):
    assert client.post("/api/raw/getMe", json={}).json()["result"]["id"] == 42
    assert client.post("/api/raw/get-me;rm", json={}).status_code in (400, 404)


def test_api_key_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    assert client.get("/api/drafts").status_code == 401
    assert client.get("/api/drafts", headers={"X-API-Key": "secret"}).status_code == 200


def test_non_json_bodies_are_rejected(client, tg):
    """A web page cannot post a form or text/plain to the local API (CSRF)."""
    body = json.dumps({"post": BLOCKS_POST})
    r = client.post("/api/send", content=body, headers={"content-type": "text/plain"})
    assert r.status_code in (415, 422)
    r = client.post("/api/raw/deleteMessage", data={"chat_id": "@x", "message_id": "1"})
    assert r.status_code in (415, 422)
    assert not [c for c in tg.calls if c["method"] in ("sendRichMessage", "deleteMessage")]


# ---------- security and robustness (from the code review) ----------
def test_foreign_host_is_rejected(client):
    """DNS rebinding: evil.example resolving to 127.0.0.1 must not reach the API."""
    assert client.get("/api/drafts", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/drafts", headers={"host": "localhost:8080"}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "https://evil.example"},
        {"origin": "null"},
        {"sec-fetch-site": "cross-site"},
        # another project on another port of this computer, from a browser without Sec-Fetch-Site
        {"origin": "http://testserver:8080"},
        {"host": "localhost:7424", "origin": "http://localhost:8080"},
        # the default port depends on the scheme; a browser's same-origin mark does not override Origin
        {"host": "testserver:443", "origin": "http://testserver"},
        {"origin": "https://evil.example", "sec-fetch-site": "same-origin"},
    ],
)
def test_cross_site_writes_are_rejected(client, tg, headers):
    r = client.post("/api/raw/getMe", json={}, headers=headers)
    assert r.status_code == 403
    r = client.post("/api/media", files={"file": ("x.html", b"<script>1</script>", "text/html")}, headers=headers)
    assert r.status_code == 403
    assert not tg.calls


@pytest.mark.parametrize(
    "headers",
    [
        {"origin": "http://testserver", "sec-fetch-site": "same-origin"},
        {"host": "localhost:7424", "origin": "http://localhost:7424", "sec-fetch-site": "same-origin"},
        {"host": "localhost:7424", "origin": "http://localhost:7424"},
        {"host": "[::1]:7424", "origin": "http://[::1]:7424"},
        # a proxy may spell out the default port in Host, the browser never does in Origin
        {"host": "testserver:443", "origin": "https://testserver"},
    ],
)
def test_same_origin_writes_are_allowed(client, tg, headers):
    assert client.post("/api/raw/getMe", json={}, headers=headers).status_code == 200


def test_empty_text_plain_body_is_rejected(client, tg):
    r = client.post("/api/raw/logOut", content=b"", headers={"content-type": "text/plain"})
    assert r.status_code == 415 and not tg.calls


def test_dangerous_raw_methods_need_confirmation(client, tg):
    assert client.post("/api/raw/logOut", json={}).status_code == 400
    assert not tg.calls
    client.post("/api/raw/logOut", json={}, headers={"x-confirm": "yes"})
    assert tg.last("logOut")


@pytest.mark.parametrize(
    "method", ["deleteMessages", "setChatTitle", "unpinAllChatMessages", "getUpdates", "someMethodTelegramAddsLater"]
)
def test_raw_methods_outside_the_allowlist_need_confirmation(client, tg, method):
    assert client.post(f"/api/raw/{method}", json={}).status_code == 400
    assert not tg.calls


@pytest.mark.parametrize(
    "method",
    ["getChat", "sendMessage", "editMessageText", "answerCallbackQuery", "copyMessage", "stopPoll", "pinChatMessage"],
)
def test_reading_and_sending_raw_methods_run_without_confirmation(client, tg, method):
    client.post(f"/api/raw/{method}", json={})
    assert tg.last(method)


def test_upload_over_the_limit_is_refused_and_not_kept(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "max_upload_mb", 0.001)  # ~1 KB
    before = {m["name"] for m in client.get("/api/media").json()}
    r = client.post("/api/media", files={"file": ("big.png", b"x" * 5000, "image/png")})
    assert r.status_code == 413 and "МБ" in r.json()["detail"]
    assert {m["name"] for m in client.get("/api/media").json()} == before


def test_upload_limit_is_larger_with_own_bot_api_server():
    assert settings.Settings.from_env({}).max_upload_mb == 50
    assert settings.Settings.from_env({"TG_API_BASE": "http://localhost:8081"}).max_upload_mb == 2000
    assert settings.Settings.from_env({"MAX_UPLOAD_MB": "10"}).max_upload_mb == 10


def test_uploaded_html_is_never_rendered_inline(client):
    name = client.post("/api/media", files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")}).json()[
        "name"
    ]
    r = client.get("/media/" + name)
    assert r.headers["content-type"] == "application/octet-stream"
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["content-security-policy"].startswith("sandbox")
    img = client.post("/api/media", files={"file": ("a.png", b"png", "image/png")}).json()["name"]
    r = client.get("/media/" + img)
    assert r.headers["content-type"] == "image/png" and r.headers["x-content-type-options"] == "nosniff"


def test_editor_pages_have_csp(client):
    for r in (client.get("/"), client.post("/api/render", json=BLOCKS_POST)):
        assert "script-src 'self'" in r.headers["content-security-policy"]


@pytest.mark.parametrize("key", ["chat_id", "rich_message", "reply_markup"])
def test_options_cannot_override_core_params(client, tg, key):
    post = {**BLOCKS_POST, "options": {key: "@other"}}
    assert client.post("/api/send", json={"post": post}).status_code == 400
    assert not tg.calls


def test_render_page_survives_placeholder_injection(client):
    d = client.post(
        "/api/drafts", json={"title": "x", "post": {"rich_message": {"html": "<p>a</p>"}, ",x=alert(1)//": 1}}
    ).json()
    page = client.get(f"/render/draft/{d['id']}?theme=__PAYLOAD__").text
    assert 'data-theme="auto"' in page
    assert 'data-theme="{' not in page


@pytest.mark.parametrize("at", ["2030-01-01T09:00:00", "завтра"])
def test_schedule_requires_valid_time_with_timezone(client, at):
    r = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": at})
    assert r.status_code == 400


def test_post_state_transitions(client, tg):
    import store

    p = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    store.update_post(p["id"], status="deleted")
    assert client.delete("/api/posts/" + p["id"]).status_code == 400
    assert client.get("/api/posts/" + p["id"]).json()["status"] == "deleted"
    store.update_post(p["id"], status="failed", error="x")
    assert client.post(f"/api/posts/{p['id']}/edit", json=BLOCKS_POST).status_code == 400
    # a failed send is not in the channel: DELETE only drops the row
    assert client.delete("/api/posts/" + p["id"]).json()["status"] == "removed"
    assert client.get("/api/posts/" + p["id"]).status_code == 404


def test_only_media_fields_are_treated_as_uploads(client, tg):
    client.post("/api/media", files={"file": ("dup.png", b"12345", "image/png")})
    post = {
        "rich_message": {
            "blocks": [
                {"type": "paragraph", "text": "upload:dup.png — это просто текст"},
                {"type": "photo", "photo": {"type": "photo", "media": "upload:dup.png"}},
                {"type": "photo", "photo": {"type": "photo", "media": "upload:dup.png"}},
            ]
        }
    }
    assert client.post("/api/send", json={"post": post}).status_code == 200
    call = tg.last("sendRichMessage")
    blocks = call["body"]["rich_message"]["blocks"]
    assert blocks[0]["text"] == "upload:dup.png — это просто текст"
    assert blocks[1]["photo"]["media"] == blocks[2]["photo"]["media"] == "attach://f0"
    assert list(call["files"]) == ["f0"]


@pytest.mark.parametrize(
    "rm",
    [
        {"blocks": [{"type": "list", "items": ["x"]}]},
        {"blocks": [{"type": "table", "cells": [["a"]]}]},
        {"blocks": "not a list"},
    ],
)
def test_malformed_blocks_are_reported_not_crashing(client, rm):
    r = client.post("/api/validate", json={"rich_message": rm})
    assert r.status_code == 200 and not r.json()["ok"]


def test_absurdly_deep_payload_is_an_error_not_a_crash(client):
    blocks: list = []
    node = blocks
    for _ in range(3000):
        inner: list = []
        node.append({"type": "blockquote", "blocks": inner})
        node = inner
    r = client.post("/api/validate", json={"rich_message": {"blocks": blocks}})
    assert r.status_code == 200 and not r.json()["ok"] and "вложенность" in r.json()["errors"][0]


def test_type_constants_are_checked(client):
    rm = {"blocks": [{"type": "photo", "photo": {"type": "video", "media": "https://x/y.mp4"}}]}
    r = client.post("/api/validate", json={"rich_message": rm}).json()
    assert not r["ok"] and any("должно быть 'photo'" in e for e in r["errors"])
    rm = {
        "html": "<video src='tg://video?id=v'></video>",
        "media": [{"id": "v", "media": {"type": "video", "media": "https://x/y.mp4", "supports_streaming": True}}],
    }
    r = client.post("/api/validate", json={"rich_message": rm}).json()
    assert r["ok"] and not r["warnings"], r


def test_scheduler_survives_network_errors(client, tg, monkeypatch):
    import httpx

    import telegram

    async def boom(method, params=None, timeout=60):
        raise httpx.ConnectError("connection refused")

    p = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time())}).json()
    monkeypatch.setattr(telegram, "call", boom)
    asyncio.run(scheduler.run_due_posts())
    rec = client.get("/api/posts/" + p["id"]).json()
    assert rec["status"] == "failed" and "ConnectError" in rec["error"]


def test_stale_sending_posts_are_not_retried(client):
    import store

    p = client.post("/api/schedule", json={"post": BLOCKS_POST, "at": int(time.time()) + 3600}).json()
    store.update_post(p["id"], status="sending")
    assert store.recover_stale_sending() >= 1
    assert client.get("/api/posts/" + p["id"]).json()["status"] == "failed"
    assert not store.claim_post(p["id"])


def test_spec_refresh_rejects_bad_downloads(monkeypatch, tmp_path):
    import httpx

    import spec as specmod

    s = specmod.Spec()
    s.load_initial()
    good_version = s.version

    class Resp:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    for bad in ({}, {"error": "rate limited"}, {"version": "Bot API 9.0", "types": {}, "methods": {}}):

        class C:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def get(self, url, _bad=bad):
                return Resp(_bad)

        monkeypatch.setattr(httpx, "AsyncClient", C)
        r = asyncio.run(s.refresh(force=True))
        assert not r["updated"] and r.get("error")
        assert s.version == good_version


def test_api_key_protects_media_render_and_docs(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    for path in ("/media/a.png", "/render/draft/x", "/docs", "/openapi.json", "/api/spec?key=secret"):
        assert client.get(path).status_code == 401, path
    assert client.get("/static/js/main.js").status_code == 200
    assert client.get("/api/spec", cookies={"rs_key": "secret"}).status_code == 401  # the old script-set cookie
    assert client.post("/api/session", json={"key": "secret"}).status_code == 200  # the editor's login
    assert client.get("/api/spec").status_code == 200


def test_editor_modules_are_served_as_javascript(client):
    # browsers refuse <script type="module"> with a wrong MIME type, and a missing import breaks the whole editor
    static = Path(__file__).resolve().parent.parent / "app" / "static"
    assert '<script type="module" src="/static/js/main.js">' in (static / "index.html").read_text()
    for f in (static / "js").glob("*.js"):
        for name in re.findall(r"from '\./([\w-]+\.js)'", f.read_text()):
            r = client.get(f"/static/js/{name}")
            assert r.status_code == 200, (f.name, name)
            assert "javascript" in r.headers["content-type"], name


# ---------- posting extras: pin, several channels, buttons, polls, updates ----------
def _run(coro):
    return asyncio.run(coro)


def test_publish_with_pin_pins_silently(client, tg):
    r = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "pin": True}}).json()
    pin = tg.last("pinChatMessage")["body"]
    assert pin == {"chat_id": -1001, "message_id": r["message"]["message_id"], "disable_notification": True}
    assert r["post"]["pinned"] == 1 and r["post"]["chat_num"] == -1001
    # studio-only fields never reach Telegram
    assert set(tg.last("sendRichMessage")["body"]) == {"chat_id", "rich_message"}


def test_pin_failure_is_a_warning_not_an_error(client, tg):
    tg.fail["pinChatMessage"] = "Bad Request: not enough rights"
    r = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "pin": True}})
    assert r.status_code == 200
    assert any("закрепить" in w for w in r.json()["warnings"])


POLL = {"question": "Во сколько эфир?", "options": ["Утром", {"text": "Вечером"}]}


def test_poll_after_post_goes_right_after_it(client, tg):
    post = {**BLOCKS_POST, "options": {"disable_notification": True}, "poll_after": POLL}
    r = post_json(client, "/api/send", {"post": post}).json()
    assert r["warnings"] == []
    methods = [c["method"] for c in tg.calls]
    assert methods.index("sendPoll") == methods.index("sendRichMessage") + 1
    # the post itself goes to the Bot API without the studio's poll_after
    assert "poll_after" not in tg.last("sendRichMessage")["body"]
    poll = tg.last("sendPoll")["body"]
    assert poll["question"] == "Во сколько эфир?" and poll["options"] == [{"text": "Утром"}, {"text": "Вечером"}]
    assert poll["disable_notification"] is True and poll["chat_id"] == -1001
    rows = client.get("/api/posts").json()
    assert [x["kind"] for x in rows[:2]] == ["poll", "rich"] and rows[0]["status"] == "sent"
    dry = post_json(client, "/api/send", {"post": post, "dry_run": True}).json()
    assert "sendPoll (poll_after)" in dry["after_send"]


def test_poll_after_post_failure_is_a_warning_and_a_row(client, tg):
    tg.fail["sendPoll"] = "Bad Request: poll can't be sent"
    r = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "poll_after": POLL}})
    assert r.status_code == 200 and r.json()["post"]["status"] == "sent"
    assert any("опрос после поста" in w for w in r.json()["warnings"])
    failed = [x for x in client.get("/api/posts").json() if x["kind"] == "poll"]
    assert failed[0]["status"] == "failed" and "poll can't be sent" in failed[0]["error"]


def test_poll_after_post_is_checked_before_the_post_goes(client, tg):
    r = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "poll_after": {"question": "?", "options": ["один"]}}})
    assert r.status_code == 422 and "два варианта" in r.text
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_scheduled_post_sends_its_poll_after(client, tg, monkeypatch):
    at = int(time.time()) + 600
    post_json(client, "/api/schedule", {"post": {**BLOCKS_POST, "poll_after": POLL}, "at": at})
    monkeypatch.setattr(time, "time", lambda: at + 1)
    _run(scheduler.run_due_posts())
    assert [c["method"] for c in tg.calls][-2:] == ["sendRichMessage", "sendPoll"]


def test_send_to_several_channels(client, tg):
    r = post_json(client, "/api/send", {"post": BLOCKS_POST, "chat_ids": ["@a", "@b", "@a", "-1002"]}).json()
    chats = [c["body"]["chat_id"] for c in tg.calls if c["method"] == "sendRichMessage"]
    assert chats == ["@a", "@b", -1002]
    assert [x["ok"] for x in r["results"]] == [True, True, True]


def test_one_failing_channel_does_not_stop_the_rest(client, tg):
    orig = tg.fail
    calls = {"n": 0}

    class FailFirst(dict):
        def __contains__(self, m):
            if m == "sendRichMessage":
                calls["n"] += 1
                return calls["n"] == 1
            return False

        def __getitem__(self, m):
            return "Forbidden: bot is not a member"

    tg.fail = FailFirst()
    try:
        r = post_json(client, "/api/send", {"post": BLOCKS_POST, "chat_ids": ["@bad", "@good"]}).json()
    finally:
        tg.fail = orig
    assert [x["ok"] for x in r["results"]] == [False, True]
    assert r["post"]["chat_id"] == "@good"


def test_schedule_to_several_channels(client):
    r = post_json(
        client, "/api/schedule", {"post": BLOCKS_POST, "chat_ids": ["@a", "@b"], "at": int(time.time()) + 999}
    ).json()
    assert [x["chat_id"] for x in r["all"]] == ["@a", "@b"]


def test_pin_and_unpin_endpoints(client, tg):
    pid = post_json(client, "/api/send", {"post": BLOCKS_POST}).json()["post"]["id"]
    assert client.post(f"/api/posts/{pid}/pin", json={"disable_notification": False}).json()["pinned"] == 1
    assert tg.last("pinChatMessage")["body"]["disable_notification"] is False
    assert client.delete(f"/api/posts/{pid}/pin").json()["pinned"] == 0
    assert tg.last("unpinChatMessage")["body"]["message_id"]


def test_replace_only_buttons(client, tg):
    pid = post_json(client, "/api/send", {"post": BLOCKS_POST}).json()["post"]["id"]
    kb = {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]}
    p = post_json(client, f"/api/posts/{pid}/buttons", {"reply_markup": kb, "callbacks": {"like": "Спасибо!"}}).json()
    assert tg.last("editMessageReplyMarkup")["body"]["reply_markup"] == kb
    assert p["payload"]["reply_markup"] == kb and p["payload"]["callbacks"] == {"like": "Спасибо!"}
    assert not [c for c in tg.calls if c["method"] == "editMessageText"]
    post_json(client, f"/api/posts/{pid}/buttons", {"reply_markup": None})
    assert "reply_markup" not in tg.last("editMessageReplyMarkup")["body"]
    # the same link rule as a full send: an empty link never reaches Telegram
    calls = len(tg.calls)
    bad = {"inline_keyboard": [[{"text": "Сайт", "url": ""}]]}
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": bad})
    assert r.status_code == 422 and "другой тип" in str(r.json()["detail"]) and len(tg.calls) == calls


def test_callback_click_is_answered_and_counted(client, tg):
    post = {
        **BLOCKS_POST,
        "reply_markup": {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]},
        "callbacks": {"like": {"text": "Голос учтён", "show_alert": True}},
    }
    r = post_json(client, "/api/send", {"post": post}).json()
    mid = r["message"]["message_id"]
    upd = {
        "update_id": 1,
        "callback_query": {
            "id": "cq1",
            "data": "like",
            "from": {"id": 5},
            "message": {"message_id": mid, "chat": {"id": -1001}},
        },
    }
    assert _run(updates.handle_update(upd)) == "callback"
    assert tg.last("answerCallbackQuery")["body"] == {
        "callback_query_id": "cq1",
        "text": "Голос учтён",
        "show_alert": True,
    }
    _run(updates.handle_update({**upd, "callback_query": {**upd["callback_query"], "id": "cq2"}}))
    assert client.get(f"/api/posts/{r['post']['id']}").json()["clicks"] == {"like": 2}
    # the list names a click by its button: the editor makes up keys like "b13xoxo6v"
    [row] = [p for p in client.get("/api/posts").json() if p["id"] == r["post"]["id"]]
    assert row["clicks"] == {"like": 2} and row["buttons"] == {"like": "👍"}
    # a button on a message the studio does not know still stops spinning
    _run(
        updates.handle_update(
            {
                "update_id": 2,
                "callback_query": {"id": "cq3", "data": "x", "message": {"message_id": 1, "chat": {"id": -5}}},
            }
        )
    )
    assert tg.last("answerCallbackQuery")["body"] == {"callback_query_id": "cq3"}


def test_first_comment_goes_under_the_forwarded_post(client, tg, monkeypatch):
    monkeypatch.setattr(settings.get(), "updates_on", True)
    post = {**BLOCKS_POST, "comment": {"html": "<p>Ссылки из эфира</p>"}}
    r = post_json(client, "/api/send", {"post": post}).json()
    assert r["post"]["comment_status"] == "waiting"
    fwd = {
        "update_id": 3,
        "message": {
            "message_id": 77,
            "chat": {"id": -2002, "type": "supergroup"},
            "is_automatic_forward": True,
            "forward_origin": {"type": "channel", "chat": {"id": -1001}, "message_id": r["message"]["message_id"]},
        },
    }
    assert _run(updates.handle_update(fwd)) == "comment"
    body = tg.last("sendRichMessage")["body"]
    assert body == {
        "chat_id": -2002,
        "rich_message": {"html": "<p>Ссылки из эфира</p>"},
        "reply_parameters": {"message_id": 77},
    }
    assert client.get(f"/api/posts/{r['post']['id']}").json()["comment_status"] == "sent"
    n = len(tg.calls)
    _run(updates.handle_update(fwd))  # only once
    assert len(tg.calls) == n


def test_invalid_first_comment_is_rejected(client, tg):
    r = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "comment": {"html": "a", "markdown": "b"}}})
    assert r.status_code == 422 and not tg.calls


def test_reaction_counts_are_stored(client, tg):
    r = post_json(client, "/api/send", {"post": BLOCKS_POST}).json()
    _run(
        updates.handle_update(
            {
                "update_id": 4,
                "message_reaction_count": {
                    "chat": {"id": -1001},
                    "message_id": r["message"]["message_id"],
                    "date": 0,
                    "reactions": [
                        {"type": {"type": "emoji", "emoji": "👍"}, "total_count": 5},
                        {"type": {"type": "custom_emoji", "custom_emoji_id": "123"}, "total_count": 1},
                        {"type": {"type": "paid"}, "total_count": 2},
                    ],
                },
            }
        )
    )
    p = client.get(f"/api/posts/{r['post']['id']}").json()
    assert p["reactions"] == {"👍": 5, "custom:123": 1, "paid": 2}


def test_poll_send_results_and_stop(client, tg):
    poll = {
        "question": "Когда эфир?",
        "options": ["Утром", {"text": "Вечером"}],
        "members_only": True,
        "country_codes": ["RU", "FT"],
        "description": "Голосуют подписчики старше суток",
    }
    r = post_json(client, "/api/poll", {"poll": poll, "pin": True}).json()
    body = tg.last("sendPoll")["body"]
    assert body["options"] == [{"text": "Утром"}, {"text": "Вечером"}]
    assert body["members_only"] is True and body["country_codes"] == ["RU", "FT"] and body["chat_id"] == "@test_channel"
    assert "pin" not in body and tg.last("pinChatMessage")
    rec = r["results"][0]["post"]
    assert rec["kind"] == "poll"
    _run(
        updates.handle_update(
            {
                "update_id": 5,
                "poll": {
                    "id": rec["poll_id"],
                    "total_voter_count": 4,
                    "is_closed": False,
                    "options": [{"text": "Утром", "voter_count": 3}, {"text": "Вечером", "voter_count": 1}],
                },
            }
        )
    )
    assert client.get(f"/api/posts/{rec['id']}").json()["poll_results"]["total"] == 4
    stopped = client.post(f"/api/posts/{rec['id']}/stop-poll").json()
    assert stopped["poll_results"]["is_closed"] is True
    assert post_json(client, f"/api/posts/{rec['id']}/edit", BLOCKS_POST).status_code == 400


def test_poll_needs_question_and_two_options(client, tg):
    assert post_json(client, "/api/poll", {"poll": {"question": "?", "options": ["один"]}}).status_code == 400
    assert (
        post_json(client, "/api/poll", {"poll": {"question": "?", "options": ["a", "b"], "chat_id": "@x"}}).status_code
        == 400
    )
    assert not tg.calls


def test_scheduled_poll_is_sent_by_scheduler(client, tg):
    r = post_json(
        client, "/api/poll", {"poll": {"question": "?", "options": ["a", "b"]}, "at": int(time.time()) + 999}
    ).json()
    pid = r["scheduled"][0]["id"]
    import store

    store.update_post(pid, scheduled_at=int(time.time()) - 1)
    _run(scheduler.run_due_posts())
    p = client.get(f"/api/posts/{pid}").json()
    assert p["status"] == "sent" and p["poll_id"] and tg.last("sendPoll")["body"]["question"] == "?"


def test_updates_status_endpoint(client):
    s = client.get("/api/updates").json()
    assert s["enabled"] is False and s["state"] == "off"


def test_pin_without_body_is_silent(client, tg):
    pid = post_json(client, "/api/send", {"post": BLOCKS_POST}).json()["post"]["id"]
    assert client.post(f"/api/posts/{pid}/pin").status_code == 200
    assert tg.last("pinChatMessage")["body"]["disable_notification"] is True


def test_updates_loop_handles_updates_and_conflicts(monkeypatch):
    import telegram as tgmod

    seen, calls = [], []

    async def fake_call(method, params=None, timeout=60):
        calls.append(params)
        if len(calls) == 1:
            return [
                {"update_id": 10, "poll": {"id": "nope"}},
                {"update_id": 11, "callback_query": {"id": "c", "data": "d"}},
            ]
        raise tgmod.TelegramError("Conflict: terminated by other getUpdates request", 409)

    async def fake_handle(u):
        seen.append(u["update_id"])
        if u["update_id"] == 10:
            raise RuntimeError("boom")  # one bad update must not stop the loop
        return "x"

    async def stop_sleep(sec):
        raise asyncio.CancelledError

    monkeypatch.setattr(tgmod, "call", fake_call)
    monkeypatch.setattr(updates, "handle_update", fake_handle)
    monkeypatch.setattr(updates, "_sleep", stop_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(updates.loop())
    assert seen == [10, 11]
    assert calls[1]["offset"] == 12 and "message_reaction_count" in calls[0]["allowed_updates"]
    assert updates.STATUS.state == "conflict"
    updates.reset_status()


def test_updates_offset_survives_a_restart(monkeypatch):
    import store
    import telegram as tgmod

    store.set_meta(updates.OFFSET_KEY, "500")
    calls = []

    async def fake_call(method, params=None, timeout=60):
        calls.append(params)
        raise asyncio.CancelledError

    monkeypatch.setattr(tgmod, "call", fake_call)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(updates.loop())
    assert calls[0]["offset"] == 500


def test_spec_refresh_on_demand_reports_gaps_and_failures(client, monkeypatch):
    import httpx

    import spec as specmod

    summary = client.get("/api/spec").json()
    assert summary["gaps"] == []  # the bundled spec is fully covered (test_spec_coverage.py)

    class Offline:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError("offline")

        async def __aexit__(self, *a):
            pass

    monkeypatch.setattr(specmod.source.httpx, "AsyncClient", Offline)
    r = client.post("/api/spec/refresh").json()
    assert r["updated"] is False and "offline" in r["error"]
    check = client.get("/api/spec").json()["last_check"]
    assert check["at"] > 0 and "offline" in check["error"]


def test_button_without_an_action_is_an_error(client):
    post = {**BLOCKS_POST, "reply_markup": {"inline_keyboard": [[{"text": "Пусто", "style": "primary"}]]}}
    r = post_json(client, "/api/validate", post).json()
    assert not r["ok"] and any("не задано, что она делает" in e for e in r["errors"])
    # a field the studio does not know yet counts as the action: new Bot API buttons pass
    post["reply_markup"]["inline_keyboard"][0][0]["future_action"] = {"x": 1}
    assert not any("что она делает" in e for e in post_json(client, "/api/validate", post).json()["errors"])


def test_poll_after_post_is_checked_like_sendpoll(client, tg):
    def refused(poll, **kw):
        r = post_json(client, "/api/send", {"post": {**BLOCKS_POST, "poll_after": poll}, **kw})
        return r.status_code, r.text

    status, text = refused({**POLL, "type": "quiz"})
    assert status == 422 and "правильный ответ" in text
    status, text = refused({**POLL, "is_anonymous": "да"})
    assert status == 422 and "is_anonymous" in text
    # a topic of the poll is the topic of the post: one chat only
    status, text = refused({**POLL, "message_thread_id": 5}, chat_ids=["@a", "@b"], dry_run=True)
    assert status == 400 and "тема форума" in text
    assert not [c for c in tg.calls if c["method"].startswith("send")]


def test_a_poll_that_may_have_gone_stays_listed(client, tg, monkeypatch):
    import telegram

    real = telegram.call

    async def flaky(method, params, *a, **kw):
        if method == "sendPoll":
            raise TimeoutError("read timeout")
        return await real(method, params, *a, **kw)

    monkeypatch.setattr(telegram, "call", flaky)
    did = post_json(client, "/api/drafts", {"title": "С опросом", "post": BLOCKS_POST}).json()["id"]
    post = {**BLOCKS_POST, "poll_after": POLL}
    for _ in range(2):
        assert post_json(client, "/api/send", {"post": post, "draft_id": did}).status_code == 200
    gone = [x for x in client.get("/api/posts").json() if x["kind"] == "poll"]
    assert len(gone) == 2 and all("Опрос мог уйти" in x["error"] for x in gone)
