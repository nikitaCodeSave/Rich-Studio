"""SQLite connection and schema migrations."""

import contextlib
import json
import sqlite3
from collections.abc import Iterator

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

# columns added after the first release; created on start for existing databases
EXTRA_COLUMNS = {
    "kind": "text default 'rich'",  # rich | poll
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
}

# Numbered steps after the columns above (PRAGMA user_version). Append only; never edit a shipped step.
MIGRATIONS: list[str] = [
    # 1: lookups by incoming updates and by the scheduler
    """create index if not exists posts_by_message on posts(chat_num, message_id);
       create index if not exists posts_by_poll on posts(poll_id);
       create index if not exists posts_due on posts(status, scheduled_at);""",
    # 2: small key-value state that must survive a restart (getUpdates offset)
    """create table if not exists meta(key text primary key, value text);""",
    # 3: Idempotency-Key: a retried send returns the first result instead of posting twice
    """create table if not exists idempotency(
         key text primary key, scope text not null, fingerprint text not null,
         status text not null, response text, created_at integer not null);""",
    # 4: a draft open in two tabs: a save based on an older version is refused instead of overwriting
    """alter table drafts add column version integer not null default 0;""",
    # 5: what a published post looked like before each replacement; the draft that edits a post
    """create table if not exists post_versions(
         id integer primary key autoincrement, post_id text not null, payload text not null,
         replaced_at integer not null, actor text);
       create index if not exists post_versions_by_post on post_versions(post_id);
       alter table drafts add column post_id text;""",
    # 6: channels with their titles and forum topics; the channels a draft is meant for
    """create table if not exists channels(
         chat_id text primary key, chat_num integer, title text, username text, type text,
         is_forum integer not null default 0, topics text, can_post integer, rights text,
         checked_at integer, created_at integer not null);
       alter table drafts add column chat_ids text;""",
]

JSON_FIELDS = ("payload", "reactions", "clicks", "poll_results", "chat_ids", "topics", "rights")


@contextlib.contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    """A connection that commits on success, rolls back on error and is always closed."""
    cfg = settings.get()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(cfg.db_path, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init() -> None:
    with conn() as c:
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
