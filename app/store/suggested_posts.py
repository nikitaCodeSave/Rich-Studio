"""Suggested posts from a channel's direct messages: the message as it came, its state and the decision."""

import json
import time

from .db import conn, row

FIELDS = {"state", "price_amount", "price_currency", "send_date", "comment", "actor", "decided_at", "message"}


def key(chat_num: int, message_id: int) -> str:
    return f"{chat_num}:{message_id}"


def save(chat_num: int, message_id: int, message: dict, **fields) -> dict:
    """A new suggestion, or a changed one (the author edited it): content and price are replaced, the state
    goes back to what Telegram says."""
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError(f"unknown suggested post columns: {sorted(bad)}")
    chat = message.get("chat") or {}
    author = message.get("from") or {}
    rec = {
        "channel_title": (chat.get("title") or "").removesuffix(" Direct Messages") or None,
        "author": " ".join(x for x in (author.get("first_name"), author.get("last_name")) if x)
        + (f" (@{author['username']})" if author.get("username") else ""),
        "topic_id": (message.get("direct_messages_topic") or {}).get("topic_id"),
        "message": json.dumps(message, ensure_ascii=False),
        **fields,
    }
    cols = ["id", "chat_num", "message_id", "received_at", *rec]
    # a pending copy (the same update delivered again, an edit) never undoes a decision already recorded here
    updates = ", ".join(
        f"{k}=excluded.{k}" if k != "state" else "state=iif(excluded.state='pending', state, excluded.state)"
        for k in rec
    )
    with conn() as c:
        c.execute(
            f"insert into suggested_posts({','.join(cols)}) values({','.join('?' * len(cols))}) "
            f"on conflict(id) do update set {updates}",
            (key(chat_num, message_id), chat_num, message_id, int(time.time()), *rec.values()),
        )
    return get(key(chat_num, message_id))  # type: ignore[return-value]


def get(sid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from suggested_posts where id=?", (sid,)).fetchone(), ("message",))


def update(sid: str, **fields) -> dict | None:
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError(f"unknown suggested post columns: {sorted(bad)}")
    if fields.get("message") is not None:
        fields["message"] = json.dumps(fields["message"], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"update suggested_posts set {sets} where id=?", (*fields.values(), sid))  # noqa: S608 - names checked above
    return get(sid)


def listing(state: str | None = None, limit: int = 100) -> list[dict]:
    """Newest first; only one state (pending, approved…) when it is given."""
    with conn() as c:
        rows = c.execute(
            "select * from suggested_posts where ? is null or state=? order by received_at desc, rowid desc limit ?",
            (state, state, limit),
        )
        return [row(r, ("message",)) for r in rows]  # type: ignore[misc]
