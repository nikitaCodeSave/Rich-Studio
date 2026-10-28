"""Suggested posts: subscribers offer posts (free or paid) in the channel's direct messages, the studio lists
them and approves or declines them.

The bot sees them only as admin of the channel with can_manage_direct_messages (approving also needs
can_post_messages). Telegram publishes an approved post itself, at send_date or right away; the outcome comes back
as service messages (suggested_post_approved, _paid, _refunded…), which keep the state here in step.
"""

import time

import store
import telegram as tg
from errors import MayHaveGone, NotFound, StudioError

from . import publisher

COMMENT_MAX = 128  # Telegram's limit for a decline comment
SEND_DATE_MAX = 2678400  # approveSuggestedPost.send_date: at most 2678400 s ahead (Telegram says 30 days)
MEDIA_KEYS = ("photo", "video", "animation", "audio", "document", "voice", "video_note", "live_photo", "paid_media")
# service message -> the state it moves the suggested post to
OUTCOMES = {
    "suggested_post_approved": "approved",
    "suggested_post_declined": "declined",
    "suggested_post_paid": "paid",
    "suggested_post_refunded": "refunded",
    "suggested_post_approval_failed": "failed",
}


def _price(info: dict) -> dict:
    price = info.get("price") or {}
    return {"price_amount": price.get("amount"), "price_currency": price.get("currency")}


def view(rec: dict) -> dict:
    """What a person needs to decide: the text, which media, the price in stars (or TON), the wanted time."""
    m = rec.get("message") or {}
    return {
        **rec,
        "text": m.get("text") or m.get("caption") or "",
        "media": [k for k in MEDIA_KEYS if m.get(k)],
    }


def on_message(m: dict) -> bool:
    """A message in a direct messages chat with suggested_post_info: a new or edited suggestion."""
    info = m.get("suggested_post_info")
    if not isinstance(info, dict) or not m.get("chat"):
        return False
    store.suggested_posts.save(
        m["chat"]["id"],
        m["message_id"],
        m,
        state=info.get("state") or "pending",
        send_date=info.get("send_date"),
        **_price(info),
    )
    return True


def on_service(m: dict) -> bool:
    """A service message about a suggestion's outcome: approved, declined, paid, refunded, approval failed."""
    for key, state in OUTCOMES.items():
        event = m.get(key)
        if not isinstance(event, dict):
            continue
        original = event.get("suggested_post_message") or m.get("reply_to_message") or {}
        if not original.get("message_id"):
            return False
        sid = store.suggested_posts.key(m["chat"]["id"], original["message_id"])
        fields: dict = {"state": state}
        if key == "suggested_post_approved" and event.get("send_date"):
            fields["send_date"] = event["send_date"]
        if key == "suggested_post_declined" and event.get("comment"):
            fields["comment"] = event["comment"]
        if store.suggested_posts.get(sid):
            store.suggested_posts.update(sid, **fields)
        elif original.get("chat"):  # the suggestion came while the studio was not looking
            store.suggested_posts.save(m["chat"]["id"], original["message_id"], original, **fields)
        else:
            return False
        return True
    return False


def listing(state: str | None = None) -> list[dict]:
    """state: one state, "open" (waiting, being decided, failed approval), or None for all."""
    return [view(r) for r in store.suggested_posts.listing(state or None)]


# a decision can be made on a suggestion still waiting, or again after an approval failed (the author lacked stars)
DECIDABLE = ("pending", "failed")
SENT_UNKNOWN = (
    "решение уже отправлено в Telegram. Обновите список через пару секунд; если итога нет и Telegram не ответил, "
    "проверьте в личных сообщениях канала, дошло ли решение, и если нет, решите там: студия увидит итог"
)


def _get(sid: str) -> dict:
    rec = store.suggested_posts.get(sid)
    if not rec:
        raise NotFound(f"предложенного поста {sid} нет: студия видит только те, что пришли, пока она запущена")
    return rec


def _check(rec: dict) -> None:
    if rec["state"] in ("approving", "declining"):
        raise StudioError(f"предложенный пост: {SENT_UNKNOWN}")
    if rec["state"] not in DECIDABLE:
        raise StudioError(f"предложенный пост уже {rec['state']}: решение принято")


async def _decide(rec: dict, method: str, params: dict, deciding: str) -> None:
    """The record is taken first (pending -> approving/declining), so a second click or a retry never sends the
    decision twice. Telegram refused: it goes back. No answer: it stays taken until the outcome comes as a service
    message, the same rule as a post that may have gone (no repeat)."""
    if not store.suggested_posts.claim(rec["id"], DECIDABLE, deciding):  # another request took it just now
        _check(store.suggested_posts.get(rec["id"]) or rec)
        raise StudioError("предложенный пост решается в другом запросе: обновите список через пару секунд")
    try:
        await tg.call(method, params)
    except (tg.TelegramError, StudioError):
        store.suggested_posts.claim(rec["id"], (deciding,), rec["state"])
        raise
    except Exception as e:  # mid-request: Telegram may have acted on it, the state comes later as a service message
        raise MayHaveGone(
            f"{type(e).__name__}: {e}. Решение могло дойти до Telegram: проверьте в личных сообщениях канала, "
            "прежде чем решать снова"
        ) from e


async def approve(sid: str, send_date=None, actor: str = "api") -> dict:
    """Publish a suggestion in the channel (Telegram does it, at send_date or now). A paid one charges the author.
    send_date only when the author did not set one."""
    rec = _get(sid)
    _check(rec)
    params: dict = {"chat_id": rec["chat_num"], "message_id": rec["message_id"]}
    if send_date not in (None, ""):
        if rec.get("send_date"):
            raise StudioError("время публикации уже выбрал автор предложения")
        params["send_date"] = publisher.parse_at(send_date)
        if params["send_date"] > time.time() + SEND_DATE_MAX:
            raise StudioError("опубликовать предложение можно не позже чем через 30 дней")
    await _decide(rec, "approveSuggestedPost", params, "approving")
    return view(
        store.suggested_posts.update(  # type: ignore[arg-type]
            sid,
            state="approved",
            send_date=params.get("send_date") or rec.get("send_date"),
            actor=actor,
            decided_at=int(time.time()),
        )
    )


def reopen(sid: str, actor: str = "api") -> dict:
    """«Решить снова»: a decision that got no answer from Telegram (approving/declining) goes back to waiting.

    No timer: the person checks the channel's direct messages first. Telegram decides a suggestion once, so a decision
    that did reach it and is made again is refused there, and its outcome still comes as a service message and moves
    the state forward (RANK). An agent's key made from the request itself changes with reopened_at."""
    rec = _get(sid)
    if rec["state"] not in ("approving", "declining"):
        raise StudioError(f"предложенный пост в состоянии {rec['state']}: решить снова можно только решение без ответа")
    if not store.suggested_posts.claim(sid, (rec["state"],), "pending"):  # the outcome came just now
        rec = _get(sid)
        raise StudioError(f"предложенный пост уже {rec['state']}: итог пришёл от Telegram")
    return view(store.suggested_posts.update(sid, reopened_at=int(time.time()), actor=actor))  # type: ignore[arg-type]


def decision_round(sid: str) -> int | None:
    """Which decision this is: the same request after «Решить снова» is a new one, not a retry."""
    rec = store.suggested_posts.get(sid)
    return rec.get("reopened_at") if rec else None


async def decline(sid: str, comment: str | None = None, actor: str = "api") -> dict:
    rec = _get(sid)
    _check(rec)
    comment = (comment or "").strip()
    if len(comment) > COMMENT_MAX:
        raise StudioError(f"комментарий длиннее {COMMENT_MAX} символов: столько Telegram не примет")
    params: dict = {"chat_id": rec["chat_num"], "message_id": rec["message_id"]}
    if comment:
        params["comment"] = comment
    await _decide(rec, "declineSuggestedPost", params, "declining")
    return view(
        store.suggested_posts.update(  # type: ignore[arg-type]
            sid, state="declined", comment=comment or None, actor=actor, decided_at=int(time.time())
        )
    )
