"""SQLite connection and schema migrations."""

import contextlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import settings

SCHEMA = """
create table if not exists drafts(
  id text primary key, title text, payload text not null,
  created_at integer, updated_at integer);
create table if not exists posts(
  id text primary key, draft_id text, chat_id text not null, message_id integer,
  payload text not null, status text not null, scheduled_at integer,
  sent_at integer, error text, created_at integer);
"""

# columns added after the first release; created on start for existing databases. Closed: a new column is a
# migration file (alter table) below
EXTRA_COLUMNS = {
    "kind": "text default 'rich'",  # services/kinds.py: rich | poll | live_photo | paid_media
    "chat_num": "integer",  # numeric chat id from Telegram (chat_id may be @username)
    "pinned": "integer default 0",
    "comment_status": "text",  # waiting | sent | failed
    "comment_message_id": "integer",
    "reactions": "text",  # {"👍": 3, ...} from message_reaction_count
    "clicks": "text",  # {"callback_data": n}
    "poll_id": "text",
    "poll_results": "text",  # {"total": n, "options": [{"text", "voter_count"}], "is_closed"}
    "actor": "text",  # who asked for the post: ui | api | the X-Studio-Client value (e.g. mcp:claude-code)
    "retry_at": "integer",  # scheduled post that could not reach Telegram: next attempt (nothing was sent)
    "version": "integer not null default 0",  # +1 on every replacement of the content: edits with an older one get 409
}

# Numbered steps after the columns above (PRAGMA user_version): one file per step in migrations/,
# NNNN_name.sql. Add a new file with the next number; never edit a shipped one. New columns go here too:
# EXTRA_COLUMNS is closed.
MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def load_migrations(folder: Path = MIGRATIONS_DIR) -> list[str]:
    """The steps in order; numbers must run 1, 2, 3... without gaps or repeats (two PRs that took one number)."""
    files = sorted(folder.glob("*.sql"))
    nums = [int(f.name.split("_", 1)[0]) for f in files]
    if nums != list(range(1, len(files) + 1)):
        raise RuntimeError(f"migration numbers must be 1..{len(files)} without gaps or repeats: {nums}")
    return [f.read_text(encoding="utf-8") for f in files]


MIGRATIONS = load_migrations()

JSON_FIELDS = ("payload", "reactions", "clicks", "poll_results", "chat_ids", "topics", "rights")


@contextlib.contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    """A connection that commits on success, rolls back on error and is always closed."""
    cfg = settings.get()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(cfg.db_path, timeout=10)
    c.row_factory = sqlite3.Row
    # FULL even with WAL (set once in init): the "sending" row written before a send must survive a power cut too;
    # the studio writes a handful of rows a minute, so it costs nothing
    c.execute("pragma synchronous = FULL")
    try:
        with c:
            yield c
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        # health and agents read while the scheduler writes; stays in the file. A filesystem that cannot do WAL
        # (some network mounts) answers with the old mode, and the studio works as before
        c.execute("pragma journal_mode = WAL")
        c.executescript(SCHEMA)
        have = {r["name"] for r in c.execute("pragma table_info(posts)")}
        for col, decl in EXTRA_COLUMNS.items():
            if col not in have:
                c.execute(f"alter table posts add column {col} {decl}")
        version = c.execute("pragma user_version").fetchone()[0]
        for i, step in enumerate(MIGRATIONS[version:], start=version + 1):
            c.executescript(step)
            c.execute(f"pragma user_version = {i}")


def row(r, json_fields=JSON_FIELDS) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for f in json_fields:
        if d.get(f):
            d[f] = json.loads(d[f])
    return d
