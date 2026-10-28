"""Small service state that must survive a restart."""

import json
import time

from .db import conn


def get_meta(key: str) -> str | None:
    with conn() as c:
        r = c.execute("select value from meta where key=?", (key,)).fetchone()
        return r["value"] if r else None


def set_meta(key: str, value: str) -> None:
    with conn() as c:
        c.execute(
            "insert into meta(key, value) values(?, ?) on conflict(key) do update set value=excluded.value",
            (key, value),
        )


def take_lease(name: str, owner: str, ttl: float) -> bool:
    """Take or renew the lease `name` for `owner` for `ttl` seconds; False while another owner's lease is valid.
    One statement, so two processes never both get it."""
    now = time.time()
    value = json.dumps({"owner": owner, "until": now + ttl})
    with conn() as c:
        cur = c.execute(
            """insert into meta(key, value) values(?, ?) on conflict(key) do update set value=excluded.value
               where json_extract(meta.value, '$.owner') = ? or json_extract(meta.value, '$.until') < ?""",
            ("lease:" + name, value, owner, now),
        )
        return cur.rowcount == 1


def lease_holder(name: str) -> str | None:
    raw = get_meta("lease:" + name)
    return json.loads(raw).get("owner") if raw else None


def drop_lease(name: str, owner: str) -> None:
    """Give the lease up at shutdown, so the next process takes over at once instead of after the ttl."""
    with conn() as c:
        c.execute(
            "delete from meta where key=? and json_extract(value, '$.owner') = ?",
            ("lease:" + name, owner),
        )
