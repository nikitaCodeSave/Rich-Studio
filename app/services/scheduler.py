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

from . import idempotency, loops, publisher

log = logging.getLogger("rich-studio.scheduler")
INTERVAL = 10  # seconds between checks
UNREACHABLE_RETRY = 60  # seconds before the next attempt when Telegram could not be reached


def _missed(p: dict, now: float) -> bool:
    limit = settings.get().schedule_late_min * 60
    return bool(limit) and now - p["scheduled_at"] > limit


async def _send_claimed(queued: dict, now: float) -> None:
    p = store.get_post(queued["id"]) or queued  # the content as it is now, after any edit since the query
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
        store.mark_outcome_unknown(p["id"], f"{type(e).__name__}: {e}. {publisher.MAY_HAVE_GONE}")
        return
    except asyncio.CancelledError:  # stopped mid-send after the grace period: the post may be out
        store.mark_outcome_unknown(p["id"], f"студия остановилась во время отправки. {publisher.MAY_HAVE_GONE}")
        raise
    # the post is in the channel now: whatever happens next must not mark it failed
    sent_at = int(time.time())
    try:
        store.update_post(
            p["id"], status="sent", message_id=res["message_id"], sent_at=sent_at, error=None, retry_at=None
        )
    except Exception as e:  # a full or read-only disk: the log keeps the message id, written later
        publisher.unrecorded_send(p["id"], p["chat_id"], p["payload"], res, sent_at, e)
        raise  # the pass goes on with the other posts and hands this to the loop's heartbeat at its end
    try:
        warnings = await publisher.after_send(p["id"], p["payload"], res, p.get("actor") or "api", p.get("kind"))
    except Exception as e:
        log.exception("after-send steps failed for %s", p["id"])
        warnings = [f"{type(e).__name__}: {e}"]
    if warnings:
        try:
            # added, not replaced: the comment's forward may have come during the steps, and its failure is in there
            store.add_error(p["id"], "; ".join(warnings))
        except Exception:
            log.exception("could not record the warnings of sent post %s: %s", p["id"], "; ".join(warnings))


async def _one(queued: dict) -> None:
    now = time.time()
    # the queue was read before the earlier sends: a post moved or cancelled since then is left alone by both
    # updates below (missed only if it still waits for the time read, taken only if its time has come)
    if _missed(queued, now):  # the service was off: a late "good morning" is worse than none
        late = int((now - queued["scheduled_at"]) // 60)
        late_text = f"{late // 60} ч {late % 60} мин" if late >= 60 else f"{late} мин"
        store.mark_missed(
            queued["id"],
            f"студия не работала в назначенное время (опоздание {late_text}): пост не отправлен, "
            "опубликуйте или отложите заново",
            scheduled_at=queued["scheduled_at"],
        )
        return
    publisher.in_flight.add(queued["id"])  # before the claim: health must never see "sending" without the id here
    try:
        if store.claim_post(queued["id"], due_by=int(now)):  # else taken, cancelled or moved to a later time
            await _send_claimed(queued, now)
    finally:
        publisher.in_flight.discard(queued["id"])


async def run_due_posts() -> int:
    """Send every scheduled post whose time has come. Returns how many were processed. A post the database fails on
    does not hold up the ones after it; the first such error is raised at the end, for the loop's heartbeat."""
    # what the database refused before (sends, outcomes of idempotency keys), first: every pass is a chance
    publisher.write_unrecorded()
    idempotency.write_unwritten()
    due = store.due_posts()
    failure: Exception | None = None
    for queued in due:
        if loops.STATE.stopping:  # shutting down: what is not claimed yet waits for the next start
            break
        try:
            await _one(queued)
        except Exception as e:  # noqa: BLE001 - raised below, the loop logs it with the traceback
            log.warning("scheduled post %s could not be processed: %s: %s", queued["id"], type(e).__name__, e)
            failure = failure or e
    if failure:
        raise failure
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
