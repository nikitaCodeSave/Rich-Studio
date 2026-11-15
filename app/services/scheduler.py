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

from . import loops, publisher

log = logging.getLogger("rich-studio.scheduler")
INTERVAL = 10  # seconds between checks
UNREACHABLE_RETRY = 60  # seconds before the next attempt when Telegram could not be reached


def _missed(p: dict, now: float) -> bool:
    limit = settings.get().schedule_late_min * 60
    return bool(limit) and now - p["scheduled_at"] > limit


async def _send_claimed(p: dict, now: float) -> None:
    try:
        res = await publisher.send(publisher.resolve_chat(p["chat_id"]), p["payload"], p.get("kind"))
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
    except asyncio.CancelledError:  # stopped mid-send after the grace period: the post may be out
        store.update_post(
            p["id"], status="failed", error=f"студия остановилась во время отправки. {publisher.MAY_HAVE_GONE}"
        )
        raise
    # the post is in the channel now: whatever happens next must not mark it failed
    store.update_post(
        p["id"], status="sent", message_id=res["message_id"], sent_at=int(time.time()), error=None, retry_at=None
    )
    try:
        warnings = await publisher.after_send(p["id"], p["payload"], res, p.get("actor") or "api", p.get("kind"))
    except Exception as e:
        log.exception("after-send steps failed for %s", p["id"])
        warnings = [f"{type(e).__name__}: {e}"]
    if warnings:
        store.update_post(p["id"], error="; ".join(warnings))


async def run_due_posts() -> int:
    """Send every scheduled post whose time has come. Returns how many were processed."""
    due = store.due_posts()
    for queued in due:
        if loops.STATE.stopping:  # shutting down: what is not claimed yet waits for the next start
            break
        now = time.time()
        publisher.in_flight.add(queued["id"])  # before the claim: health must never see "sending" without the id here
        try:
            # the time is checked in the claim itself: a post moved to a later time or cancelled after the query
            # (while an earlier one was being sent) is not taken
            if not store.claim_post(queued["id"], int(now)):
                continue
            p = store.get_post(queued["id"]) or queued  # the post as it is now, after any edit or move since the query
            if _missed(p, now):  # the service was off: a late "good morning" is worse than none
                late = int((now - p["scheduled_at"]) // 60)
                late_text = f"{late // 60} ч {late % 60} мин" if late >= 60 else f"{late} мин"
                store.mark_missed(
                    p["id"],
                    f"студия не работала в назначенное время (опоздание {late_text}): пост не отправлен, "
                    "опубликуйте или отложите заново",
                )
                continue
            await _send_claimed(p, now)
        finally:
            publisher.in_flight.discard(queued["id"])
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
