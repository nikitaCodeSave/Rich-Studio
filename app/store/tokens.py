"""Access tokens and editor sessions. Only SHA-256 hashes are stored: both are long random strings."""

import hashlib
import time

from .db import conn

USED_EVERY = 60  # seconds between writes of last_used_at: not a write per request


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def add_token(name: str, secret: str, level: str) -> None:
    with conn() as c:
        c.execute(
            "insert into tokens(name, hash, level, created_at) values(?, ?, ?, ?)",
            (name, _hash(secret), level, int(time.time())),
        )


def list_tokens() -> list[dict]:
    with conn() as c:
        rows = c.execute("select name, level, created_at, last_used_at, revoked_at from tokens order by name")
        return [dict(r) for r in rows]


def revoke_token(name: str) -> bool:
    """Revoke a token and end the editor sessions opened with it."""
    with conn() as c:
        cur = c.execute("update tokens set revoked_at=? where name=? and revoked_at is null", (int(time.time()), name))
        c.execute("delete from sessions where token_name=?", (name,))
        return cur.rowcount == 1


def any_tokens() -> bool:
    """Revoked ones count: revoking the last token must not open the studio to everyone."""
    with conn() as c:
        return c.execute("select 1 from tokens limit 1").fetchone() is not None


def token_by_secret(secret: str) -> dict | None:
    """The live token with this secret, or None; notes when it was last used."""
    now = int(time.time())
    with conn() as c:
        r = c.execute(
            "select name, level, last_used_at from tokens where hash=? and revoked_at is null", (_hash(secret),)
        ).fetchone()
        if r and (r["last_used_at"] or 0) < now - USED_EVERY:
            c.execute("update tokens set last_used_at=? where name=?", (now, r["name"]))
        return dict(r) if r else None


def add_session(secret: str, token_name: str | None, ttl: float) -> None:
    now = int(time.time())
    with conn() as c:
        c.execute("delete from sessions where expires_at<?", (now,))
        c.execute(
            "insert into sessions(hash, token_name, created_at, expires_at) values(?, ?, ?, ?)",
            (_hash(secret), token_name, now, int(now + ttl)),
        )


def session_by_secret(secret: str) -> dict | None:
    """A live session: its token's name and level (None for one opened with STUDIO_API_KEY)."""
    with conn() as c:
        r = c.execute(
            """select s.token_name, t.level from sessions s left join tokens t on t.name = s.token_name
               where s.hash=? and s.expires_at>=? and (s.token_name is null or t.revoked_at is null)""",
            (_hash(secret), int(time.time())),
        ).fetchone()
        return dict(r) if r else None


def drop_session(secret: str) -> None:
    with conn() as c:
        c.execute("delete from sessions where hash=?", (_hash(secret),))
