"""Schema steps live one per file: two PRs never edit the same list, and a taken number is caught here."""

import sqlite3

import pytest

import settings
import store
from store import db


def test_steps_are_numbered_one_by_one():
    files = sorted(db.MIGRATIONS_DIR.glob("*.sql"))
    assert [int(f.name[:4]) for f in files] == list(range(1, len(files) + 1))
    assert all(f.name[4] == "_" and f.read_text(encoding="utf-8").startswith("-- ") for f in files)
    assert len(db.MIGRATIONS) == len(files)


def test_a_number_taken_twice_or_skipped_stops_the_start(tmp_path):
    for name in ("0001_a.sql", "0002_b.sql", "0002_c.sql"):
        (tmp_path / name).write_text("select 1;")
    with pytest.raises(RuntimeError, match="without gaps"):
        db.load_migrations(tmp_path)
    (tmp_path / "0002_c.sql").rename(tmp_path / "0004_c.sql")
    with pytest.raises(RuntimeError, match="without gaps"):
        db.load_migrations(tmp_path)


def test_a_fresh_database_runs_every_step():
    with store.conn() as c:
        assert c.execute("pragma user_version").fetchone()[0] == len(db.MIGRATIONS)
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert {"meta", "idempotency", "post_versions", "channels"} <= tables


def test_an_old_database_gets_only_the_new_steps():
    """A database left at step 4 (before post history) gets steps 5 and on; what it had stays."""
    path = settings.get().db_path
    path.unlink()
    for extra in ("-wal", "-shm"):
        path.with_name(path.name + extra).unlink(missing_ok=True)
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA)
    for col, decl in db.EXTRA_COLUMNS.items():
        old.execute(f"alter table posts add column {col} {decl}")
    old.executescript("".join(db.MIGRATIONS[:4]) + "pragma user_version = 4;")
    old.execute("insert into meta(key, value) values ('kept', '1')")
    old.commit()
    old.close()
    db.init()
    with store.conn() as c:
        assert c.execute("pragma user_version").fetchone()[0] == len(db.MIGRATIONS)
        assert c.execute("select value from meta where key='kept'").fetchone()[0] == "1"
        c.execute("select post_id, chat_ids from drafts")


def test_a_step_that_fails_halfway_leaves_nothing_and_runs_again(monkeypatch):
    """The step and user_version go in one transaction: a step stopped halfway is not half applied."""
    done = len(db.MIGRATIONS)
    broken = "-- test\ncreate table half_done(x);\ninsert into no_such_table values (1);"
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS, broken])
    with pytest.raises(sqlite3.OperationalError):
        db.init()
    with store.conn() as c:
        assert c.execute("pragma user_version").fetchone()[0] == done
        assert not c.execute("select 1 from sqlite_master where name='half_done'").fetchone()
    monkeypatch.setattr(db, "MIGRATIONS", [*db.MIGRATIONS[:done], broken.replace("no_such_table", "half_done")])
    db.init()
    with store.conn() as c:
        assert c.execute("pragma user_version").fetchone()[0] == done + 1
        assert c.execute("select x from half_done").fetchone()[0] == 1
