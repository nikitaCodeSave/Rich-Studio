"""Scheduled posts: send the ones whose time has come."""

import asyncio
import logging
import time

import logs
import metrics
import settings
import store
import telegram as tg
from errors import StudioError

from . import publisher

log = logging.getLogger("rich-studio.scheduler")
INTERVAL = 10  # seconds between checks
UNREACHABLE_RETRY = 60  # seconds before the next attempt when Telegram could not be reached
# ids this process is sending right now: their "sending" status is normal, not stuck (health reads it)
in_flight: set[str] = set()


def _missed(p: dict, now: float) -> bool:
    limit = settings.get().schedule_late_min * 60
    return bool(limit) and now - p["scheduled_at"] > limit


async def _send_claimed(queued: dict, now: float) -> None:
    p = store.get_post(queued["id"]) or queued  # the content as it is now, after any edit since the query
    try:
        res = await publisher.send(publisher.resolve_chat(p["chat_id"]), p["payload"])
    except StudioError as e:  # bad settings: nothing was sent
        store.update_post(p["id"], status="failed", error=str(e))
        return
    except tg.TelegramError as e:
        # not sent: no connection, or Telegram asked to wait longer than the client waits itself
        if isinstance(e, tg.Unreachable) or (e.code == 429 and e.retry_after):
            wait = UNREACHABLE_RETRY if isinstance(e, tg.Unreachable) else int(e.retry_after or 0)
            store.retry_later(p["id"], int(now) + wait, f"{e.description}; повтор через {wait} с")
        else:
            store.update_post(p["id"], status="failed", error=e.description)
        return
    except Exception as e:  # noqa: BLE001 - network errors mid-send, a 5xx: never kill the scheduler
        store.update_post(p["id"], status="failed", error=f"{type(e).__name__}: {e}. {publisher.MAY_HAVE_GONE}")
        return
    # the post is in the channel now: whatever happens next must not mark it failed
    store.update_post(
        p["id"], status="sent", message_id=res["message_id"], sent_at=int(time.time()), error=None, retry_at=None
    )
    try:
        warnings = await publisher.after_send(p["id"], p["payload"], res, p.get("actor") or "api")
    except Exception as e:
        log.exception("after-send steps failed for %s", p["id"])
        warnings = [f"{type(e).__name__}: {e}"]
    if warnings:
        store.update_post(p["id"], error="; ".join(warnings))


async def run_due_posts() -> int:
    """Send every scheduled post whose time has come. Returns how many were processed."""
    due = store.due_posts()
    for queued in due:
        now = time.time()
        if _missed(queued, now):  # the service was off: a late "good morning" is worse than none
            late = int((now - queued["scheduled_at"]) // 60)
            late_text = f"{late // 60} ч {late % 60} мин" if late >= 60 else f"{late} мин"
            store.mark_missed(
                queued["id"],
                f"студия не работала в назначенное время (опоздание {late_text}): пост не отправлен, "
                "опубликуйте или отложите заново",
            )
            continue
        in_flight.add(queued["id"])  # before the claim: health must never see "sending" without the id here
        try:
            if not store.claim_post(queued["id"]):  # someone else took it
                continue
            await _send_claimed(queued, now)
        finally:
            in_flight.discard(queued["id"])
    return len(due)


async def loop() -> None:
    while True:
        error = None
        with logs.bind(logs.new_id("scheduler-")):
            try:
                await run_due_posts()
            except Exception as e:
                log.exception("scheduler iteration failed")
                error = f"{type(e).__name__}: {e}"
        metrics.beat("scheduler", INTERVAL, error)
        await asyncio.sleep(INTERVAL)
