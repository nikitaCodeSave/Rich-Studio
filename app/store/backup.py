"""Consistent copies of the database, and which uploads the stored content still mentions."""

import re
import sqlite3
from pathlib import Path

import settings

from .db import conn

_UPLOAD = re.compile(r'"upload:([^"\\]+)"')
# posts whose files may still be sent again: waiting, missed (to be rescheduled), or published (an edit re-uploads)
_LIVE = ("scheduled", "sending", "missed", "sent")


def copy_db(dest: Path) -> None:
    """A consistent copy even while the studio writes (SQLite online backup)."""
    src = sqlite3.connect(settings.get().db_path, timeout=10)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def upload_refs() -> set[str]:
    """File names that drafts and live posts mention as upload:<name>."""
    marks = ",".join("?" * len(_LIVE))
    with conn() as c:
        texts = [r[0] for r in c.execute("select payload from drafts")]
        texts += [r[0] for r in c.execute(f"select payload from posts where status in ({marks})", _LIVE)]  # noqa: S608
    return {m for t in texts for m in _UPLOAD.findall(t)}
