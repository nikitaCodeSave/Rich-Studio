"""Idempotency keys: one row per key, claimed atomically before the action runs.

Only finished keys (done) are forgotten after TTL. A key after which the post may have gone (uncertain, or pending
left by a crash) stays for good: a retry with it, even a week later, must not post a second time. Such keys are
rare (one per network error or crash mid-send) and small, so the table does not need a limit for them."""

import json
import time

from .db import conn, row

TTL = 24 * 3600  # done keys older than a day are forgotten
EXPIRED = "delete from idempotency where status='done' and created_at < ?"


def claim(key: str, scope: str, fingerprint: str) -> dict | None:
    """Claim the key for this request. None if claimed now, else the existing row."""
    now = int(time.time())
    with conn() as c:
        c.execute(EXPIRED, (now - TTL,))
        cur = c.execute(
            "insert or ignore into idempotency(key, scope, fingerprint, status, created_at) values(?,?,?,'pending',?)",
            (key, scope, fingerprint, now),
        )
        if cur.rowcount == 1:
            return None
        existing = row(c.execute("select * from idempotency where key=?", (key,)).fetchone())
    if existing and existing.get("response"):
        existing["response"] = json.loads(existing["response"])
    return existing


def finish(key: str, status: str, response) -> None:
    """status: done (response is the result) or uncertain (response is the error detail)."""
    with conn() as c:
        c.execute(
            "update idempotency set status=?, response=? where key=?",
            (status, json.dumps(response, ensure_ascii=False), key),
        )


def release(key: str) -> None:
    """Nothing was sent: the same key may be used again."""
    with conn() as c:
        c.execute("delete from idempotency where key=?", (key,))


def purge(now: int | None = None) -> int:
    """Forget done keys older than TTL (claim does it too, but only when someone sends)."""
    with conn() as c:
        return c.execute(EXPIRED, (int(now or time.time()) - TTL,)).rowcount
