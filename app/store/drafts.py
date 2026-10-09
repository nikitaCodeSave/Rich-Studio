"""Drafts: posts being written in the editor."""

import json
import time
import uuid

from .db import conn, row


def list_drafts() -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("select id, title, updated_at from drafts order by updated_at desc")]


def get_draft(did: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from drafts where id=?", (did,)).fetchone())


def save_draft(did: str | None, title: str, payload: dict) -> dict | None:
    now = int(time.time())
    did = did or uuid.uuid4().hex[:12]
    with conn() as c:
        c.execute(
            """insert into drafts(id,title,payload,created_at,updated_at) values(?,?,?,?,?)
                     on conflict(id) do update set title=excluded.title, payload=excluded.payload,
                     updated_at=excluded.updated_at""",
            (did, title, json.dumps(payload, ensure_ascii=False), now, now),
        )
    return get_draft(did)


def delete_draft(did: str) -> None:
    with conn() as c:
        c.execute("delete from drafts where id=?", (did,))
