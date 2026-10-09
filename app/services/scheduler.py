"""Scheduled posts: send the ones whose time has come."""

import asyncio
import logging
import time

import store
import telegram as tg
from errors import StudioError

from . import publisher

log = logging.getLogger("rich-studio.scheduler")
INTERVAL = 10  # seconds between checks


async def run_due_posts() -> int:
    """Send every scheduled post whose time has come. Returns how many were processed."""
    due = store.due_posts()
    for p in due:
        if not store.claim_post(p["id"]):  # someone else took it
            continue
        try:
            res = await publisher.send(publisher.resolve_chat(p["chat_id"]), p["payload"])
        except StudioError as e:  # bad settings: nothing was sent
            store.update_post(p["id"], status="failed", error=str(e))
            continue
        except tg.TelegramError as e:
            store.update_post(p["id"], status="failed", error=e.description)
            continue
        except Exception as e:  # noqa: BLE001 - network errors etc.: never kill the scheduler
            store.update_post(p["id"], status="failed", error=f"{type(e).__name__}: {e}. {publisher.MAY_HAVE_GONE}")
            continue
        # the post is in the channel now: whatever happens next must not mark it failed
        store.update_post(p["id"], status="sent", message_id=res["message_id"], sent_at=int(time.time()), error=None)
        try:
            warnings = await publisher.after_send(p["id"], p["payload"], res)
        except Exception as e:
            log.exception("after-send steps failed for %s", p["id"])
            warnings = [f"{type(e).__name__}: {e}"]
        if warnings:
            store.update_post(p["id"], error="; ".join(warnings))
    return len(due)


async def loop() -> None:
    while True:
        try:
            await run_due_posts()
        except Exception:
            log.exception("scheduler iteration failed")
        await asyncio.sleep(INTERVAL)
