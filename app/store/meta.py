"""Small service state that must survive a restart."""

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
