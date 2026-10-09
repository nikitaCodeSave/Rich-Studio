"""Posts: published, scheduled, failed, cancelled or deleted."""

import json
import time
import uuid

from .db import EXTRA_COLUMNS, JSON_FIELDS, conn, row

# columns update_post may touch; anything else is a programming error, never SQL
UPDATABLE = {"chat_id", "message_id", "payload", "status", "scheduled_at", "sent_at", "error", *EXTRA_COLUMNS}


def add_post(
    chat_id,
    payload: dict,
    draft_id: str | None = None,
    status: str = "sent",
    message_id: int | None = None,
    scheduled_at: int | None = None,
    error: str | None = None,
    kind: str = "rich",
    actor: str | None = None,
) -> dict:
    pid = uuid.uuid4().hex[:12]
    now = int(time.time())
    with conn() as c:
        c.execute(
            """insert into posts(id,draft_id,chat_id,message_id,payload,status,scheduled_at,sent_at,error,created_at,kind,actor)
                     values(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                draft_id,
                str(chat_id),
                message_id,
                json.dumps(payload, ensure_ascii=False),
                status,
                scheduled_at,
                now if status == "sent" else None,
                error,
                now,
                kind,
                actor,
            ),
        )
    return get_post(pid)  # type: ignore[return-value]


def update_post(pid: str, **fields) -> dict | None:
    bad = set(fields) - UPDATABLE
    if bad:
        raise ValueError(f"unknown post columns: {sorted(bad)}")
    for f in JSON_FIELDS:
        if f in fields and fields[f] is not None:
            fields[f] = json.dumps(fields[f], ensure_ascii=False)
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"update posts set {sets} where id=?", (*fields.values(), pid))  # noqa: S608 - names checked above
    return get_post(pid)


def get_post(pid: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from posts where id=?", (pid,)).fetchone())


def list_posts(limit: int = 100) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            """select id, draft_id, chat_id, message_id, status, scheduled_at, sent_at, error, created_at,
                                   kind, actor, pinned, comment_status, reactions, clicks, poll_results,
                                   json_extract(payload, '$.poll.question') as poll_question
                            from posts order by coalesce(scheduled_at, sent_at, created_at) desc limit ?""",
            (limit,),
        )
        return [row(r) for r in rows]  # type: ignore[misc]


def find_post(chat_num, message_id) -> dict | None:
    """The studio post behind a channel message, by numeric chat id and message id."""
    with conn() as c:
        return row(
            c.execute(
                "select * from posts where chat_num=? and message_id=? order by created_at desc", (chat_num, message_id)
            ).fetchone()
        )


def find_sent(chat, message_id: int) -> dict | None:
    """A sent studio post by the chat as the caller wrote it (@username or numeric id) and message id."""
    with conn() as c:
        return row(
            c.execute(
                """select * from posts where message_id=? and status='sent' and (chat_id=? or chat_num=?)
                   order by created_at desc""",
                (message_id, str(chat), chat),
            ).fetchone()
        )


def find_poll(poll_id: str) -> dict | None:
    with conn() as c:
        return row(c.execute("select * from posts where poll_id=?", (poll_id,)).fetchone())


def bump_click(pid: str, data: str) -> None:
    with conn() as c:  # one statement, so concurrent clicks are not lost
        c.execute(
            """update posts set clicks=json_set(coalesce(clicks,'{}'), '$.' || json_quote(?),
                     coalesce(json_extract(coalesce(clicks,'{}'), '$.' || json_quote(?)), 0) + 1) where id=?""",
            (data, data, pid),
        )


def claim_post(pid: str) -> bool:
    """Atomically move a scheduled post to 'sending'; False if it was already taken or cancelled."""
    with conn() as c:
        cur = c.execute("update posts set status='sending' where id=? and status='scheduled'", (pid,))
        return cur.rowcount == 1


def cancel_scheduled(pid: str) -> bool:
    with conn() as c:
        cur = c.execute("update posts set status='cancelled' where id=? and status='scheduled'", (pid,))
        return cur.rowcount == 1


def recover_stale_sending() -> int:
    """Posts stuck in 'sending' after a crash: never retry blindly, they may already be in the channel."""
    with conn() as c:
        cur = c.execute(
            "update posts set status='failed', error=? where status='sending'",
            ("сервис остановился во время отправки; проверьте канал, пост мог уйти",),
        )
        return cur.rowcount


def due_posts() -> list[dict]:
    with conn() as c:
        rows = c.execute("select * from posts where status='scheduled' and scheduled_at<=?", (int(time.time()),))
        return [row(r) for r in rows]  # type: ignore[misc]
