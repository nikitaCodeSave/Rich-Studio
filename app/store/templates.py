"""Post templates: a post with {{key}} fields and the description of those fields (services/templates.py)."""

import json
import time
import uuid

from .db import conn, row

JSON_FIELDS = ("payload", "fields")


def list_templates() -> list[dict]:
    """Every template without its post, last saved first."""
    with conn() as c:
        rows = c.execute(
            "select id, title, fields, version, created_at, updated_at from post_templates order by updated_at desc, id"
        )
        return [row(r, JSON_FIELDS) for r in rows]  # type: ignore[misc]


def get_template(tid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from post_templates where id=?", (tid,)).fetchone(), JSON_FIELDS)


def create_template(title: str, payload: dict, fields: list[dict]) -> dict:
    now = int(time.time())
    tid = uuid.uuid4().hex[:12]
    with conn() as c:
        c.execute(
            "insert into post_templates(id, title, payload, fields, created_at, updated_at) values(?,?,?,?,?,?)",
            (tid, title, _dump(payload), _dump(fields), now, now),
        )
    return get_template(tid)  # type: ignore[return-value]


def update_template(tid: str, title: str, payload: dict, fields: list[dict], version: int) -> dict | None:
    """Overwrite the template only if nobody saved it since `version`; None otherwise (or when it is gone)."""
    with conn() as c:
        cur = c.execute(
            "update post_templates set title=?, payload=?, fields=?, updated_at=?, version=version+1"
            " where id=? and version=?",
            (title, _dump(payload), _dump(fields), int(time.time()), tid, version),
        )
        if cur.rowcount != 1:
            return None
    return get_template(tid)


def delete_template(tid: str, version: int) -> bool:
    """False when the template is gone or was saved since `version`."""
    with conn() as c:
        return c.execute("delete from post_templates where id=? and version=?", (tid, version)).rowcount == 1


def templates_using(ref: str) -> list[dict]:
    """Templates whose post or field defaults mention the upload reference (e.g. "upload:cat.png") as a whole value."""
    quoted = json.dumps(ref, ensure_ascii=False)
    with conn() as c:
        rows = c.execute(
            "select id, title from post_templates where instr(payload, ?) > 0 or instr(fields, ?) > 0", (quoted, quoted)
        )
        return [dict(r) for r in rows]


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False)
