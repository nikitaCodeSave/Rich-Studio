"""Idempotency keys: one row per key, claimed atomically before the action runs."""

import json
import time

from .db import conn, row

TTL = 24 * 3600  # keys older than a day are forgotten


def claim(key: str, scope: str, fingerprint: str) -> dict | None:
    """Claim the key for this request. None if claimed now, else the existing row."""
    now = int(time.time())
    with conn() as c:
        c.execute("delete from idempotency where created_at < ?", (now - TTL,))
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
    """Forget keys older than TTL (claim does it too, but only when someone sends)."""
    with conn() as c:
        return c.execute("delete from idempotency where created_at < ?", (int(now or time.time()) - TTL,)).rowcount
