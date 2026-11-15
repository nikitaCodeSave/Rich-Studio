"""Read-only questions the health report asks the database, and trimming of old post versions."""

from .db import MIGRATIONS, conn


def schema_ok() -> tuple[int, int]:
    """(version in the file, version this code expects)."""
    with conn() as c:
        return c.execute("pragma user_version").fetchone()[0], len(MIGRATIONS)


def post_counts() -> dict[str, int]:
    with conn() as c:
        return {r[0]: r[1] for r in c.execute("select status, count(*) from posts group by status")}


def _brief(rows) -> list[dict]:
    return [dict(r) for r in rows]


def overdue(before: int) -> list[dict]:
    """Scheduled posts whose time (or retry time) passed before `before` and that are still waiting."""
    with conn() as c:
        return _brief(
            c.execute(
                """select id, chat_id, scheduled_at, retry_at, error from posts
                   where status='scheduled' and coalesce(retry_at, scheduled_at) < ? order by scheduled_at limit 10""",
                (before,),
            )
        )


def problem_posts(
    since: int, comments_sent_since: int, sending_now: set[str] | frozenset[str] = frozenset(), limit: int = 20
) -> tuple[list[dict], int]:
    """Posts that need a person: missed (until rescheduled or cancelled, however old), still sending (except
    `sending_now`, the ones being sent this second), failed since `since`, and first comments that failed on posts
    sent since `comments_sent_since` (a comment fails at the latest when its wait for the forward ends, so the
    caller counts its window from there). Returns the newest `limit` of them and how many there are in all."""
    marks = ",".join("?" * len(sending_now))
    where = f"""status='missed'
                or (status='sending' and id not in ({marks}))
                or (status='failed' and coalesce(sent_at, scheduled_at, created_at) >= ?)
                or (comment_status='failed' and coalesce(sent_at, created_at) >= ?)"""
    args = (*sending_now, since, comments_sent_since)
    with conn() as c:
        total = c.execute(f"select count(*) from posts where {where}", args).fetchone()[0]  # noqa: S608 - placeholders only
        rows = c.execute(
            f"""select id, chat_id, status, comment_status, error,
                       coalesce(sent_at, scheduled_at, created_at) as at from posts
                where {where} order by at desc limit ?""",  # noqa: S608 - placeholders only
            (*args, limit),
        )
        return _brief(rows), total


def uncertain_sends(since: int) -> list[dict]:
    """Idempotency keys whose request ended with "the post may have gone": someone has to look at the channel."""
    with conn() as c:
        return _brief(
            c.execute(
                """select key, scope, response, created_at from idempotency
                   where status='uncertain' and created_at >= ? order by created_at desc limit 10""",
                (since,),
            )
        )


def version_count() -> int:
    with conn() as c:
        return c.execute("select count(*) from post_versions").fetchone()[0]


def trim_versions(keep: int) -> int:
    """Keep only the newest `keep` versions of every post. Returns how many were deleted."""
    with conn() as c:
        cur = c.execute(
            """delete from post_versions where id in (
                 select id from (select id, row_number() over (partition by post_id order by id desc) as n
                                 from post_versions) where n > ?)""",
            (keep,),
        )
        return cur.rowcount
