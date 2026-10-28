"""Kinds of messages (services/kinds.py, ADR 0013): the kind stored with a post decides how it goes out, whether it
can be replaced and how the list shows it; nothing guesses it from the payload's keys."""

import asyncio
import json
import sqlite3
import time
import typing

import models
import settings
import store
from services import kinds, listing, publisher, scheduler
from store import db
from test_mcp import rpc


def test_every_kind_sends_with_its_own_method():
    assert publisher.request(-1, {"rich_message": {"markdown": "x"}})[0] == "sendRichMessage"
    for k in kinds.KINDS.values():
        if k.key:
            method, params = publisher.request(-1, {k.key: {"a": 1}, "pin": True}, k.name)
            assert (method, params) == (k.method, {"a": 1, "chat_id": -1})


def test_media_kinds_match_the_api_and_mcp_schemas(client):
    """The schemas list them by hand (a Literal is static): a new media kind must be added there too."""
    api = typing.get_args(models.MediaPostReq.model_fields["kind"].annotation)
    assert sorted(api) == sorted(kinds.MEDIA)
    tools = {t["name"]: t for t in rpc(client, "tools/list")["result"]["tools"]}
    assert sorted(tools["send_media_post"]["inputSchema"]["properties"]["kind"]["enum"]) == sorted(kinds.MEDIA)


def test_config_describes_the_kinds_for_the_editor(client):
    described = client.get("/api/config").json()["kinds"]
    assert set(described) == set(kinds.KINDS)
    assert described["rich"]["editable"] and not described["poll"]["editable"]
    assert described["poll"]["stoppable"] and described["paid_media"]["badge"] == "за звёзды"


def test_unknown_kind_fails_the_scheduled_send_without_telegram(client, tg):
    """A row of a kind this version does not know never goes out as something else."""
    rec = store.add_scheduled(["@chan"], {"rich_message": {"markdown": "x"}}, int(time.time()) - 1, kind="story")[0]
    calls = len(tg.calls)
    asyncio.run(scheduler.run_due_posts())
    p = store.get_post(rec["id"])
    assert p["status"] == "failed" and "story" in p["error"]
    assert len(tg.calls) == calls


def test_list_reads_buttons_where_the_kind_keeps_them():
    keyboard = {"inline_keyboard": [[{"text": "Да", "callback_data": "yes"}]]}
    assert listing.button_texts({"live_photo": {"reply_markup": keyboard}}, "live_photo") == {"yes": "Да"}
    assert listing.button_texts({"reply_markup": keyboard}) == {"yes": "Да"}
    assert listing.title({"poll": {"question": "Как?"}}, "poll") == "Как?"
    assert listing.title({"paid_media": {}}, "paid_media") == "Платный контент"


def test_migration_gives_old_rows_the_kind_of_their_content():
    path = settings.get().db_path
    path.unlink()
    for extra in ("-wal", "-shm"):
        path.with_name(path.name + extra).unlink(missing_ok=True)
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    for col, decl in db.EXTRA_COLUMNS.items():
        old.execute(f"alter table posts add column {col} {decl}")
    old.executescript("".join(db.MIGRATIONS[:9]) + "pragma user_version = 9;")
    rows = {
        "a": ("rich", {"poll": {"question": "?"}}),
        "b": ("rich", {"live_photo": {"caption": "c"}}),
        "c": (None, {"paid_media": {"star_count": 1}}),
        "d": (None, {"rich_message": {"markdown": "x"}}),
        "e": ("poll", {"poll": {"question": "?"}}),
    }
    for pid, (kind, payload) in rows.items():
        old.execute(
            "insert into posts(id, chat_id, payload, status, kind) values (?, '@c', ?, 'sent', ?)",
            (pid, json.dumps(payload), kind),
        )
    old.execute("insert into posts(id, chat_id, payload, status, kind) values ('f', '@c', 'not json', 'sent', 'rich')")
    old.commit()
    old.close()
    db.init()
    with store.conn() as c:
        got = dict(c.execute("select id, kind from posts").fetchall())
    assert got == {"a": "poll", "b": "live_photo", "c": "paid_media", "d": "rich", "e": "poll", "f": "rich"}
