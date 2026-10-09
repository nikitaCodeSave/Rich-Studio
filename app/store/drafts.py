"""Drafts: posts being written in the editor."""

import json
import time
import uuid

from .db import conn, row


def list_drafts() -> list[dict]:
    with conn() as c:
        return [
            dict(r) for r in c.execute("select id, title, updated_at, post_id from drafts order by updated_at desc")
        ]


def get_draft(did: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from drafts where id=?", (did,)).fetchone())


def save_draft(
    did: str | None, title: str, payload: dict, version: int | None = None, post_id: str | None = None
) -> dict | None:
    """Create or overwrite a draft. With `version`, only if nobody saved it since that version
    (another tab, an agent); returns None then, and also when that draft was deleted meanwhile.
    `post_id`: the published or scheduled post this draft is a working copy of."""
    now = int(time.time())
    did = did or uuid.uuid4().hex[:12]
    data = json.dumps(payload, ensure_ascii=False)
    with conn() as c:
        if version is not None:
            cur = c.execute(
                """update drafts set title=?, payload=?, updated_at=?, post_id=?, version=version+1
                   where id=? and version=?""",
                (title, data, now, post_id, did, version),
            )
            if cur.rowcount != 1:
                return None
        else:
            c.execute(
                """insert into drafts(id,title,payload,created_at,updated_at,post_id) values(?,?,?,?,?,?)
                         on conflict(id) do update set title=excluded.title, payload=excluded.payload,
                         updated_at=excluded.updated_at, post_id=excluded.post_id, version=drafts.version+1""",
                (did, title, data, now, now, post_id),
            )
    return get_draft(did)


def drafts_using(ref: str) -> list[dict]:
    """Drafts whose content mentions the upload reference (e.g. "upload:cat.png")."""
    with conn() as c:
        rows = c.execute(
            "select id, title from drafts where instr(payload, ?) > 0", (json.dumps(ref, ensure_ascii=False),)
        )
        return [dict(r) for r in rows]


def delete_draft(did: str) -> None:
    with conn() as c:
        c.execute("delete from drafts where id=?", (did,))
