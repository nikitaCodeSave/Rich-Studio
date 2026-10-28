"""Named invite links the studio made, and the joins and join requests that came through them."""

import time

from .db import conn, row

# columns save may set from Telegram's ChatInviteLink; anything else is a programming error, never SQL
FIELDS = {
    "name",
    "kind",
    "expire_date",
    "member_limit",
    "creates_join_request",
    "subscription_period",
    "subscription_price",
    "is_revoked",
}


def save(invite_link: str, chat_id: str, actor: str | None = None, **fields) -> dict:
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError(f"unknown invite link columns: {sorted(bad)}")
    cols = ["invite_link", "chat_id", "actor", "created_at", *fields]
    updates = ", ".join(f"{k}=excluded.{k}" for k in fields) or "chat_id=chat_id"
    with conn() as c:
        c.execute(
            f"insert into invite_links({','.join(cols)}) values({','.join('?' * len(cols))}) "
            f"on conflict(invite_link) do update set {updates}",
            (invite_link, chat_id, actor, int(time.time()), *fields.values()),
        )
    return get(invite_link)  # type: ignore[return-value]


def get(invite_link: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from invite_links where invite_link=?", (invite_link,)).fetchone(), ())


def listing(chat_id: str | None = None) -> list[dict]:
    """Newest first; only one chat (as the studio stored it) when chat_id is set."""
    with conn() as c:
        rows = c.execute(
            "select * from invite_links where ? is null or chat_id=? order by created_at desc, rowid desc",
            (chat_id, chat_id),
        )
        return [row(r, ()) for r in rows]  # type: ignore[misc]


def count_join(invite_link: str) -> bool:
    """Someone joined through this link; False if the studio did not make it."""
    with conn() as c:
        cur = c.execute(
            "update invite_links set joined=joined+1, last_join_at=? where invite_link=?",
            (int(time.time()), invite_link),
        )
        return cur.rowcount == 1


def count_request(invite_link: str) -> bool:
    """Someone asked to join through this link (creates_join_request)."""
    with conn() as c:
        cur = c.execute("update invite_links set requests=requests+1 where invite_link=?", (invite_link,))
        return cur.rowcount == 1
