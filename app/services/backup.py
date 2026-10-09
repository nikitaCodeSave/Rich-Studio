"""Backups: a zip with everything for the person to keep, and daily database copies next to the data."""

import asyncio
import logging
import tempfile
import time
import zipfile
from pathlib import Path

import settings
import store

log = logging.getLogger("rich-studio.backup")
CHECK_INTERVAL = 3600  # seconds; a copy is made once per calendar day
_sleep = asyncio.sleep  # tests replace this, not asyncio.sleep itself


def export_zip() -> Path:
    """studio.db (a consistent copy) and every uploaded file, in a temporary zip the caller deletes."""
    cfg = settings.get()
    fd, name = tempfile.mkstemp(prefix="rich-studio-", suffix=".zip")
    out = Path(name)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "studio.db"
        store.backup.copy_db(db)
        with open(fd, "wb") as f, zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db, "studio.db")
            if cfg.media_dir.exists():
                for p in sorted(cfg.media_dir.iterdir()):
                    if p.is_file():  # already compressed formats gain nothing from deflate
                        z.write(p, f"media/{p.name}", compress_type=zipfile.ZIP_STORED)
    return out


def export_name() -> str:
    return time.strftime("rich-studio-%Y%m%d-%H%M.zip")


def daily_snapshot() -> Path | None:
    """Today's copy of the database in DATA_DIR/backups, unless it is there already; keeps the newest N."""
    cfg = settings.get()
    if cfg.backup_keep <= 0 or not cfg.db_path.exists():
        return None
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    today = cfg.backup_dir / time.strftime("studio-%Y%m%d.db")
    made = None
    if not today.exists():
        part = today.with_suffix(".part")
        store.backup.copy_db(part)
        part.replace(today)  # never leave a half-written copy under the final name
        made = today
    for old in sorted(cfg.backup_dir.glob("studio-*.db"), reverse=True)[cfg.backup_keep :]:
        old.unlink(missing_ok=True)
    return made


async def loop() -> None:
    while True:
        try:
            if made := await asyncio.to_thread(daily_snapshot):
                log.info("database copy: %s", made.name)
        except Exception:
            log.exception("daily backup failed")
        await _sleep(CHECK_INTERVAL)
