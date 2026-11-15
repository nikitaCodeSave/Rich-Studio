"""Consistent copies of the database, and which uploads the stored content still mentions."""

import json
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


def refs_in(payload: str) -> set[str]:
    """File names a stored payload mentions as upload:<name>, also inside the editor's unapplied JSON
    (`_studio.json.text`: a string that may not parse, so it is searched as text, not decoded)."""
    found = set(_UPLOAD.findall(payload))
    try:
        text = json.loads(payload)["_studio"]["json"]["text"]
    except (ValueError, KeyError, TypeError):
        return found
    if isinstance(text, str):
        found |= set(_UPLOAD.findall(text))
    return found


def upload_refs() -> set[str]:
    """File names that drafts, templates (their post and field defaults) and live posts mention as upload:<name>."""
    marks = ",".join("?" * len(_LIVE))
    with conn() as c:
        texts = [r[0] for r in c.execute("select payload from drafts")]
        texts += [t for r in c.execute("select payload, fields from post_templates") for t in r]
        texts += [r[0] for r in c.execute(f"select payload from posts where status in ({marks})", _LIVE)]  # noqa: S608
    return {m for t in texts for m in refs_in(t)}
