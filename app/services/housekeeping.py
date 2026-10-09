"""The studio watching itself: health changes go to the log, old service data is cleaned up.

Service data (idempotency keys) is always cleaned. What the person made (uploads, versions of posts) is
deleted only when MEDIA_UNUSED_DAYS / VERSIONS_KEEP say so; otherwise the health report says it grows.
"""

import asyncio
import logging
import time
from typing import Any

import metrics
import settings
import store

from . import health, media

log = logging.getLogger("rich-studio.health")
INTERVAL = 300  # seconds between health checks
CLEANUP_EVERY = 3600
_sleep = asyncio.sleep  # tests replace this, not asyncio.sleep itself
_last: dict[str, str] = {}  # check name -> status at the previous round


def watch() -> dict:
    """Compare the health report with the previous round; every change is one log line with the reason."""
    rep = health.report()
    for c in rep["checks"]:
        before = _last.get(c["name"], "ok")
        if c["status"] != before:
            level = logging.INFO if c["status"] == "ok" else logging.WARNING if c["status"] == "warn" else logging.ERROR
            log.log(
                level,
                "%s: %s → %s: %s",
                c["name"],
                before,
                c["status"],
                c["summary"],
                extra={"event": "health.change", "check": c["name"], "status": c["status"], "action": c.get("action")},
            )
        _last[c["name"]] = c["status"]
    return rep


def cleanup() -> dict:
    cfg = settings.get()
    done: dict[str, Any] = {"idempotency_keys": store.idempotency.purge()}
    if cfg.versions_keep > 0:
        done["post_versions"] = store.stats.trim_versions(cfg.versions_keep)
    if cfg.media_unused_days > 0:
        done["media"] = media.delete_unused(older_than=time.time() - cfg.media_unused_days * 86400)
    if any(done.values()):
        log.info("cleanup: %s", done, extra={"event": "cleanup", **done})
    return done


async def loop() -> None:
    last_cleanup = 0.0
    while True:
        error = None
        try:
            await asyncio.to_thread(watch)
            if time.time() - last_cleanup >= CLEANUP_EVERY:
                await asyncio.to_thread(cleanup)
                last_cleanup = time.time()
        except Exception as e:
            log.exception("housekeeping failed")
            error = f"{type(e).__name__}: {e}"
        metrics.beat("housekeeping", INTERVAL, error)
        await _sleep(INTERVAL)
