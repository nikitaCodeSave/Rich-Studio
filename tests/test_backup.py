"""Backups: the export zip, daily database copies, files nothing needs any more."""

import io
import sqlite3
import time
import zipfile

import settings
from services import backup
from test_api import BLOCKS_POST


def _photo(name: str) -> dict:
    return {"rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": f"upload:{name}"}}]}}


def test_export_has_the_database_and_every_file(client, tg):
    client.post("/api/media", files={"file": ("cat.png", b"\x89PNG-cat", "image/png")})
    client.post("/api/drafts", json={"title": "Кот", "post": _photo("cat.png")})
    client.post("/api/send", json={"post": BLOCKS_POST})
    r = client.get("/api/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "rich-studio-" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["media/cat.png", "studio.db"]
    assert z.read("media/cat.png") == b"\x89PNG-cat"
    db = settings.get().data_dir / "restored.db"
    db.write_bytes(z.read("studio.db"))
    c = sqlite3.connect(db)
    assert c.execute("select title from drafts").fetchone()[0] == "Кот"
    assert c.execute("select count(*) from posts where status='sent'").fetchone()[0] == 1
    c.close()


def test_daily_copy_is_made_once_and_old_ones_are_pruned(client, monkeypatch):
    cfg = settings.get()
    monkeypatch.setattr(cfg, "backup_keep", 2)
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    for day in ("20200101", "20200102", "20200103"):
        (cfg.backup_dir / f"studio-{day}.db").write_bytes(b"old")
    made = backup.daily_snapshot()
    assert made and made.name == time.strftime("studio-%Y%m%d.db")
    assert backup.daily_snapshot() is None  # once a day
    left = sorted(p.name for p in cfg.backup_dir.glob("studio-*.db"))
    assert left == ["studio-20200103.db", made.name]
    assert not list(cfg.backup_dir.glob("*.part"))


def test_daily_copy_can_be_turned_off(client, monkeypatch):
    monkeypatch.setattr(settings.get(), "backup_keep", 0)
    assert backup.daily_snapshot() is None
    assert not settings.get().backup_dir.exists()


def test_unused_files_are_marked_and_cleaned_up(client, tg):
    for name in ("draft.png", "sent.png", "old.png", "lonely.png"):
        client.post("/api/media", files={"file": (name, b"\x89PNG", "image/png")})
    client.post("/api/drafts", json={"title": "d", "post": _photo("draft.png")})
    client.post("/api/send", json={"post": _photo("sent.png")})
    gone = client.post("/api/send", json={"post": _photo("old.png")}).json()["post"]["id"]
    client.delete(f"/api/posts/{gone}")  # deleted from the channel: its file is no longer needed
    used = {f["name"]: f["used"] for f in client.get("/api/media").json()}
    assert used == {"draft.png": True, "sent.png": True, "old.png": False, "lonely.png": False}
    r = client.post("/api/media/cleanup").json()
    assert sorted(r["deleted"]) == ["lonely.png", "old.png"]
    assert sorted(f["name"] for f in client.get("/api/media").json()) == ["draft.png", "sent.png"]
