"""Channels the studio posts to: title, forum topics and the bot's rights as Telegram last reported them."""

import json
import time

from .db import conn, row

# columns save_channel may set; anything else is a programming error, never SQL
FIELDS = {"chat_num", "title", "username", "type", "is_forum", "topics", "can_post", "rights", "checked_at"}


def list_channels() -> list[dict]:
    with conn() as c:
        return [row(r) for r in c.execute("select * from channels order by coalesce(title, chat_id)")]  # type: ignore[misc]


def get_channel(chat_id) -> dict | None:
    """By the id as the user wrote it (@username or -100…) or by Telegram's numeric id."""
    with conn() as c:
        return row(
            c.execute(
                "select * from channels where chat_id=? or chat_num=? or (username is not null and '@' || username=?)",
                (str(chat_id), chat_id, str(chat_id)),
            ).fetchone()
        )


def save_channel(chat_id: str, **fields) -> dict:
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError(f"unknown channel columns: {sorted(bad)}")
    for f in ("topics", "rights"):
        if f in fields and fields[f] is not None:
            fields[f] = json.dumps(fields[f], ensure_ascii=False)
    cols = ["chat_id", "created_at", *fields]
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields) or "chat_id=chat_id"
    with conn() as c:
        c.execute(
            f"insert into channels({','.join(cols)}) values({','.join('?' * len(cols))}) "
            f"on conflict(chat_id) do update set {updates}",
            (chat_id, int(time.time()), *fields.values()),
        )
    return get_channel(chat_id)  # type: ignore[return-value]


def delete_channel(chat_id: str) -> None:
    with conn() as c:
        c.execute("delete from channels where chat_id=?", (chat_id,))
