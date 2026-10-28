"""Suggested posts from a channel's direct messages: the message as it came, its state and the decision."""

import json
import time

from .db import conn, row

FIELDS = {"state", "price_amount", "price_currency", "send_date", "comment", "actor", "decided_at", "message"}
# how far a suggestion has got: a copy that arrives late (the same update again, an edit) never moves it back
RANK = {
    "pending": 0,
    "approving": 1,
    "declining": 1,
    "approved": 2,
    "declined": 2,
    "paid": 3,
    "refunded": 3,
    "failed": 3,
}
# still the studio's to act on or to watch: waiting for a decision, decided but no answer yet, approval failed
OPEN = ("pending", "approving", "declining", "failed")


def _rank(col: str) -> str:
    whens = " ".join(f"when '{k}' then {v}" for k, v in RANK.items())
    return f"(case {col} {whens} else 0 end)"


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
    # a late copy (the same update delivered again, an edit) never undoes what is already recorded here
    keep = f"state=iif({_rank('excluded.state')} < {_rank('state')}, state, excluded.state)"
    updates = ", ".join(f"{k}=excluded.{k}" if k != "state" else keep for k in rec)
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
    """The state only moves forward: «approved» after approveSuggestedPost answered never undoes a «paid» or
    «failed» that came meanwhile from getUpdates."""
    bad = set(fields) - FIELDS
    if bad:
        raise ValueError(f"unknown suggested post columns: {sorted(bad)}")
    if fields.get("message") is not None:
        fields["message"] = json.dumps(fields["message"], ensure_ascii=False)
    sets = ", ".join(
        f"{k}=iif({_rank('?')} < {_rank('state')}, state, ?)" if k == "state" else f"{k}=?" for k in fields
    )
    values = [v for k, v in fields.items() for v in ((v, v) if k == "state" else (v,))]
    with conn() as c:
        c.execute(f"update suggested_posts set {sets} where id=?", (*values, sid))  # noqa: S608 - names checked above
    return get(sid)


def claim(sid: str, from_states: tuple[str, ...], to: str) -> bool:
    """Move a suggestion to `to` only if it is in one of from_states now: one decision at a time, even from two
    tabs at once. False: someone is deciding already, or has decided."""
    marks = ",".join("?" * len(from_states))
    with conn() as c:
        cur = c.execute(
            f"update suggested_posts set state=? where id=? and state in ({marks})",  # noqa: S608 - only placeholders
            (to, sid, *from_states),
        )
        return cur.rowcount == 1


def listing(state: str | None = None, limit: int = 100) -> list[dict]:
    """Newest first; only one state (pending, approved…) when it is given, "open" for those in OPEN."""
    states = OPEN if state == "open" else (state,) if state else None
    marks = ",".join("?" * len(states or ()))
    where = f"where state in ({marks})" if states else ""
    with conn() as c:
        rows = c.execute(
            f"select * from suggested_posts {where} order by received_at desc, rowid desc limit ?",  # noqa: S608
            (*(states or ()), limit),
        )
        return [row(r, ("message",)) for r in rows]  # type: ignore[misc]
