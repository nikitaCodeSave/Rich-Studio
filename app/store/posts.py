"""Posts: published, scheduled, failed, cancelled or deleted."""

import json
import time
import uuid

import redact

from .db import EXTRA_COLUMNS, JSON_FIELDS, conn, row

# columns update_post may touch; anything else is a programming error, never SQL
UPDATABLE = {"chat_id", "message_id", "payload", "status", "scheduled_at", "sent_at", "error", *EXTRA_COLUMNS}


def _error(text: str | None) -> str | None:
    """An error text as it is saved: shown in the list of posts, kept in backups, so without secrets (redact.py)."""
    return redact.scrub(text) if text else text


def _insert(
    c,
    chat_id,
    payload: dict,
    draft_id: str | None,
    status: str,
    message_id: int | None,
    scheduled_at: int | None,
    error: str | None,
    kind: str,
    actor: str | None,
    outcome_unknown: bool = False,
) -> str:
    pid = uuid.uuid4().hex[:12]
    now = int(time.time())
    c.execute(
        """insert into posts(id,draft_id,chat_id,message_id,payload,status,scheduled_at,sent_at,error,created_at,kind,actor,
                             outcome_unknown)
                 values(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            pid,
            draft_id,
            str(chat_id),
            message_id,
            json.dumps(payload, ensure_ascii=False),
            status,
            scheduled_at,
            now if status == "sent" else None,
            _error(error),
            now,
            kind,
            actor,
            int(outcome_unknown),
        ),
    )
    return pid


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
    outcome_unknown: bool = False,
) -> dict:
    """`outcome_unknown`: a failed send that may be in the channel all the same (see mark_outcome_unknown)."""
    with conn() as c:
        pid = _insert(
            c, chat_id, payload, draft_id, status, message_id, scheduled_at, error, kind, actor, outcome_unknown
        )
    return get_post(pid)  # type: ignore[return-value]


def add_scheduled(
    chats: list, payload: dict, at: int, draft_id: str | None = None, kind: str = "rich", actor: str | None = None
) -> list[dict]:
    """The same post scheduled to several chats: all rows or none."""
    with conn() as c:
        ids = [_insert(c, chat, payload, draft_id, "scheduled", None, at, None, kind, actor) for chat in chats]
    return [get_post(i) for i in ids]  # type: ignore[misc]


def update_post(pid: str, **fields) -> dict | None:
    bad = set(fields) - UPDATABLE
    if bad:
        raise ValueError(f"unknown post columns: {sorted(bad)}")
    for f in JSON_FIELDS:
        if f in fields and fields[f] is not None:
            fields[f] = json.dumps(fields[f], ensure_ascii=False)
    if "error" in fields:
        fields["error"] = _error(fields["error"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"update posts set {sets} where id=?", (*fields.values(), pid))  # noqa: S608 - names checked above
    return get_post(pid)


def _post(r) -> dict | None:
    """A posts row as every caller sees it: JSON fields parsed, the outcome flag a bool (0/1 in SQLite)."""
    p = row(r)
    if p is not None and "outcome_unknown" in p:
        p["outcome_unknown"] = bool(p["outcome_unknown"])
    return p


def get_post(pid: str) -> dict | None:
    with conn() as c:
        return _post(c.execute("select * from posts where id=?", (pid,)).fetchone())


def list_posts(limit: int = 100, status: str | None = None, chat: str | None = None) -> list[dict]:
    """Newest first; payload is included for the list's title and thumbnail. status and chat filter."""
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if chat:
        where.append("(chat_id=? or chat_num=?)")
        args += [chat, chat]
    cond = ("where " + " and ".join(where)) if where else ""
    with conn() as c:
        rows = c.execute(
            f"""select id, draft_id, chat_id, message_id, status, scheduled_at, sent_at, error, created_at,
                                   kind, actor, version, pinned, comment_status, reactions, clicks, reply_errors, poll_results,
                                   payload, outcome_unknown,
                                   json_extract(payload, '$.poll.question') as poll_question
                            from posts {cond} order by coalesce(scheduled_at, sent_at, created_at) desc, rowid desc limit ?""",  # noqa: S608 - fixed fragments
            (*args, limit),
        )
        return [_post(r) for r in rows]  # type: ignore[misc]


def drop_refused(chat_id, draft_id: str) -> int:
    """After a successful send: earlier refusals of the same draft to the same chat are history, not a problem.
    A send whose outcome is unknown stays, whatever its error says: the channel may hold a duplicate."""
    with conn() as c:
        cur = c.execute(
            "delete from posts where status='failed' and outcome_unknown=0 and draft_id=? and chat_id=?",
            (draft_id, str(chat_id)),
        )
        return cur.rowcount


def mark_outcome_unknown(pid: str, error: str) -> None:
    """A send that may have gone out (no answer from Telegram, a stop mid-send): failed, and flagged, so that the row
    never leaves the list on its own and the health report shows it until a person removes it."""
    with conn() as c:
        c.execute("update posts set status='failed', outcome_unknown=1, error=? where id=?", (_error(error), pid))


def delete_post(pid: str) -> None:
    with conn() as c:
        c.execute("delete from post_versions where post_id=?", (pid,))
        c.execute("delete from posts where id=?", (pid,))


def find_post(chat_num, message_id) -> dict | None:
    """The studio post behind a channel message, by numeric chat id and message id."""
    with conn() as c:
        return _post(
            c.execute(
                "select * from posts where chat_num=? and message_id=? order by created_at desc", (chat_num, message_id)
            ).fetchone()
        )


def find_sent(chat, message_id: int) -> dict | None:
    """A sent studio post by the chat as the caller wrote it (@username or numeric id) and message id."""
    with conn() as c:
        return _post(
            c.execute(
                """select * from posts where message_id=? and status='sent' and (chat_id=? or chat_num=?)
                   order by created_at desc""",
                (message_id, str(chat), chat),
            ).fetchone()
        )


def find_poll(poll_id: str) -> dict | None:
    with conn() as c:
        return _post(c.execute("select * from posts where poll_id=?", (poll_id,)).fetchone())


def bump_click(pid: str, data: str) -> None:
    with conn() as c:  # one statement, so concurrent clicks are not lost
        c.execute(
            """update posts set clicks=json_set(coalesce(clicks,'{}'), '$.' || json_quote(?),
                     coalesce(json_extract(coalesce(clicks,'{}'), '$.' || json_quote(?)), 0) + 1) where id=?""",
            (data, data, pid),
        )


def note_reply_error(pid: str, data: str, error: str) -> None:
    """A rich reply to the button `data` of the post did not go: one more for that button, with the last reason
    (without secrets, redact.py). One statement, so two failed presses at once are both counted."""
    with conn() as c:  # the key as bump_click writes it in clicks
        c.execute(
            """update posts set reply_errors = json_set(coalesce(reply_errors, '{}'), '$.' || json_quote(?),
                     json_object('n', coalesce(json_extract(coalesce(reply_errors, '{}'),
                                                            '$.' || json_quote(?) || '.n'), 0) + 1,
                                 'error', ?, 'at', ?)) where id=?""",
            (data, data, _error(error), int(time.time()), pid),
        )


def claim_post(pid: str, *, due_by: int | None) -> bool:
    """Atomically move a scheduled post to 'sending'; False if it was already taken or cancelled, or if its time or
    retry time is now later than `due_by`: moved after the queue was read, it waits for the new one. `due_by=None`
    takes it whatever its time is (tests only)."""
    with conn() as c:
        cur = c.execute(
            """update posts set status='sending' where id=? and status='scheduled'
               and (? is null or coalesce(retry_at, scheduled_at)<=?)""",
            (pid, due_by, due_by),
        )
        return cur.rowcount == 1


def cancel_scheduled(pid: str) -> bool:
    with conn() as c:
        cur = c.execute("update posts set status='cancelled' where id=? and status in ('scheduled', 'missed')", (pid,))
        return cur.rowcount == 1


def reschedule(pid: str, at: int) -> bool:
    """A new time for a waiting or missed post; False if it is already being sent or gone."""
    with conn() as c:
        cur = c.execute(
            """update posts set status='scheduled', scheduled_at=?, retry_at=null, error=null
               where id=? and status in ('scheduled', 'missed')""",
            (at, pid),
        )
        return cur.rowcount == 1


def _add_version(c, pid: str, payload: dict, actor: str | None) -> None:
    c.execute(
        "insert into post_versions(post_id, payload, replaced_at, actor) values(?,?,?,?)",
        (pid, json.dumps(payload, ensure_ascii=False), int(time.time()), actor),
    )


def add_version(pid: str, payload: dict, actor: str | None) -> None:
    """Keep the content a post had before it was replaced."""
    with conn() as c:
        _add_version(c, pid, payload, actor)


def list_versions(pid: str) -> list[dict]:
    """Earlier contents of a post, newest first."""
    with conn() as c:
        rows = c.execute(
            "select id, payload, replaced_at, actor from post_versions where post_id=? order by id desc", (pid,)
        )
        return [row(r) for r in rows]  # type: ignore[misc]


def recover_stale_sending(keep: set[str] | frozenset[str] = frozenset()) -> int:
    """Posts (the poll after a post too) and first comments stuck in 'sending' after a crash: never retry blindly,
    they may already be out. A post is flagged like mark_outcome_unknown; a comment fails like fail_comment, its post
    is in the channel. `keep`: the ones this process is sending right now. Returns how many rows were marked."""
    marks = ",".join("?" * len(keep))
    with conn() as c:
        cur = c.execute(
            f"""update posts set status='failed', outcome_unknown=1, error=?
                where status='sending' and id not in ({marks})""",  # noqa: S608 - placeholders only
            (_error("сервис остановился во время отправки; проверьте канал, пост мог уйти"), *keep),
        )
        comments = c.execute(
            f"""update posts set comment_status='failed', comment_failed_at=?, error=coalesce(error || '; ', '') || ?
                where comment_status='sending' and id not in ({marks})""",  # noqa: S608 - placeholders only
            (
                int(time.time()),
                _error("первый комментарий: сервис остановился во время отправки; проверьте обсуждение, он мог уйти"),
                *keep,
            ),
        ).rowcount
        return cur.rowcount + comments


def confirm_sent(pid: str, message_id: int, sent_at: int, chat_num=None, poll_id=None) -> bool:
    """A send Telegram accepted whose "sent" the database refused at the time, written down late. Telegram's answer
    settles it, even if the recovery of another process marked the row "may have gone" meanwhile. False: no such row."""
    with conn() as c:
        cur = c.execute(
            """update posts set status='sent', message_id=?, sent_at=?, chat_num=coalesce(?, chat_num),
                                poll_id=coalesce(?, poll_id), error=null, retry_at=null, outcome_unknown=0
               where id=?""",
            (message_id, sent_at, chat_num, poll_id, pid),
        )
        return cur.rowcount == 1


def due_posts() -> list[dict]:
    """Scheduled posts whose time (or retry time) has come, earliest first."""
    with conn() as c:
        rows = c.execute(
            """select * from posts where status='scheduled' and coalesce(retry_at, scheduled_at)<=?
               order by scheduled_at, created_at""",
            (int(time.time()),),
        )
        return [_post(r) for r in rows]  # type: ignore[misc]


def mark_missed(pid: str, error: str, *, scheduled_at: int) -> bool:
    """A scheduled post far past its time: keep it for the person instead of sending it late. Only if it still waits
    for the `scheduled_at` the caller read: a post moved or cancelled since then is left alone (False)."""
    with conn() as c:
        cur = c.execute(
            "update posts set status='missed', error=? where id=? and status='scheduled' and scheduled_at=?",
            (_error(error), pid, scheduled_at),
        )
        return cur.rowcount == 1


def retry_later(pid: str, at: int, error: str) -> None:
    """A claimed post that never reached Telegram goes back to the queue."""
    with conn() as c:
        c.execute(
            "update posts set status='scheduled', retry_at=?, error=? where id=? and status='sending'",
            (at, _error(error), pid),
        )


def claim_version(pid: str, expected: int | None) -> int | None:
    """Take the next version of a post before replacing its content and return it; None if someone replaced it
    since `expected` (None: whatever the version is). One statement, so the number returned is the one this
    caller took. Taken before Telegram is asked, so two edits never both pass."""
    with conn() as c:
        rows = c.execute(
            "update posts set version=version+1 where id=? and (? is null or version=?) returning version",
            (pid, expected, expected),
        ).fetchall()
        return rows[0][0] if rows else None


def release_version(pid: str, taken: int) -> None:
    """Give back a version taken by claim_version when the edit was refused and nothing changed; a no-op if
    someone took a newer one meanwhile."""
    with conn() as c:
        c.execute("update posts set version=version-1 where id=? and version=?", (pid, taken))


def replace_scheduled(pid: str, payload: dict, old: dict, actor: str | None) -> bool:
    """New content for a post that is still waiting (scheduled or missed), and the old one into its history, in one
    transaction; False if the scheduler already took it."""
    with conn() as c:
        cur = c.execute(
            "update posts set payload=? where id=? and status in ('scheduled', 'missed')",
            (json.dumps(payload, ensure_ascii=False), pid),
        )
        if cur.rowcount != 1:
            return False
        _add_version(c, pid, old, actor)
        return True


def replace_sent(pid: str, payload: dict, old: dict, actor: str | None) -> None:
    """A published post after an edit in the channel, and the old content into its history, in one transaction."""
    with conn() as c:
        _add_version(c, pid, old, actor)
        c.execute("update posts set payload=? where id=?", (json.dumps(payload, ensure_ascii=False), pid))


def expire_comments(sent_before: int, error: str) -> int:
    """First comments still waiting for the forward long after the post: Telegram no longer delivers it. Failed now,
    like fail_comment: one being sent ('sending') is not waiting any more."""
    with conn() as c:
        cur = c.execute(
            """update posts set comment_status='failed', comment_failed_at=?, error=coalesce(error || '; ', '') || ?
               where comment_status='waiting' and sent_at<?""",
            (int(time.time()), _error(error), sent_before),
        )
        return cur.rowcount


def claim_comment(pid: str) -> bool:
    """Take the first comment of a post for sending: 'sending' before Telegram is asked, so that a stop or a crash
    mid-request leaves "may have gone" rather than a comment still waiting for its forward. Only if it still waits:
    False if it was taken, failed or never asked for."""
    with conn() as c:
        cur = c.execute("update posts set comment_status='sending' where id=? and comment_status='waiting'", (pid,))
        return cur.rowcount == 1


def fail_comment(pid: str, error: str) -> None:
    """The first comment did not go out, or may have: failed now (`comment_failed_at`, the health report counts its
    day from there). The reason is added to the post's error, never replacing what is there (the scheduler keeps the
    warnings of a sent post in it)."""
    with conn() as c:
        c.execute(
            """update posts set comment_status='failed', comment_failed_at=?, error=coalesce(error || '; ', '') || ?
               where id=?""",
            (int(time.time()), _error(error), pid),
        )


def using_upload(ref: str) -> list[dict]:
    """Posts still waiting (scheduled or missed) whose content mentions the upload reference (e.g. "upload:cat.png")."""
    with conn() as c:
        rows = c.execute(
            "select id, chat_id, scheduled_at, status from posts"
            " where status in ('scheduled', 'missed') and instr(payload, ?) > 0",
            (json.dumps(ref, ensure_ascii=False),),
        )
        return [dict(r) for r in rows]
