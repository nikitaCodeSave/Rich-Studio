"""The studio watching itself: health changes go to the log, old service data is cleaned up.

Service data (idempotency keys) is always cleaned. What the person made (uploads, versions of posts) is
deleted only when MEDIA_UNUSED_DAYS / VERSIONS_KEEP say so; otherwise the health report says it grows.
With DEADMAN_URL each round also tells an outside service it is alive: silence there means the studio is down.
"""

import asyncio
import logging
import time
from typing import Any

import httpx

import logs
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


async def ping(status: str) -> None:
    """Dead man's switch, healthchecks.io style: URL when alive, URL/fail when the report fails. The URL itself is
    the secret, so it never goes to the log; a failed ping is a warning, never a failed round."""
    url = settings.get().deadman_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            (await client.get(url + "/fail" if status == "fail" else url)).raise_for_status()
    except httpx.HTTPError as e:
        log.warning("dead man's switch not reached: %s", type(e).__name__, extra={"event": "deadman.error"})


async def loop() -> None:
    last_cleanup = 0.0
    while True:
        error, status = None, "fail"
        with logs.bind(logs.new_id("housekeeping-")):  # to_thread copies the context: the id goes along
            try:
                status = (await asyncio.to_thread(watch))["status"]
                if time.time() - last_cleanup >= CLEANUP_EVERY:
                    await asyncio.to_thread(cleanup)
                    last_cleanup = time.time()
            except Exception as e:
                log.exception("housekeeping failed")
                error = f"{type(e).__name__}: {e}"
            await ping(status)
        metrics.beat("housekeeping", INTERVAL, error)
        await _sleep(INTERVAL)
