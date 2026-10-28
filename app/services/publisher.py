"""Sending posts to Telegram: target chats, validation, send, the steps after a send.

Error policy (see docs/REVIEW_CHECKLIST.md, section 2):
- once a send succeeded, the post is recorded as sent; later steps only add warnings;
- a network error during a send means "may have gone out": never retried automatically;
- one failing channel never stops the others;
- a send Telegram refused, or that may have gone, is recorded as failed with the error.
"""

import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Any

import settings
import spec
import store
import telegram as tg
from errors import Invalid, MayHaveGone, StudioError

log = logging.getLogger("rich-studio.publisher")
RESERVED_PARAMS = {"chat_id", "rich_message", "reply_markup", "message_id"}
MAY_HAVE_GONE = "Пост мог уйти в канал, проверьте перед повтором"
POLL_MAY_HAVE_GONE = "Опрос мог уйти в канал, проверьте перед повтором"
# media messages that are not rich posts: the post's key (and the post's kind) -> the Bot API method
MEDIA_METHODS = {"live_photo": "sendLivePhoto", "paid_media": "sendPaidMedia"}
MEDIA_LABELS = {"live_photo": "Live Photo", "paid_media": "платный контент"}
# ids of posts this process is sending right now (now or from the scheduler): their "sending" status is normal,
# not stuck; health reads it, and startup recovery only exists because a crash empties it
in_flight: set[str] = set()


_CHAT = re.compile(r"@\w+|-?\d+")


def resolve_chat(chat_id) -> int | str:
    """@username stays a string, numeric ids become int; empty means TG_DEFAULT_CHAT."""
    c = chat_id.strip() if isinstance(chat_id, str) else chat_id
    if c in (None, ""):
        c = settings.get().default_chat
    if c in (None, ""):
        raise StudioError("не указан chat_id и не задан TG_DEFAULT_CHAT")
    if isinstance(c, str) and re.fullmatch(r"-?\d+", c):
        return int(c)
    return c


def normalize_chats(chat_ids) -> list[str]:
    """A list of channels as the person typed it: trimmed, checked, each channel once (@Chan and @chan,
    -100… as a string and as a number are the same). An empty item is an error, never the default channel."""
    out: dict[str, str] = {}
    for raw in chat_ids or []:
        c = str(raw).strip()
        if not c:
            raise StudioError("в списке каналов пустой элемент: уберите его или впишите канал")
        if not _CHAT.fullmatch(c):
            raise StudioError(f"канал {c!r}: нужен @username или числовой id (-100…)")
        out.setdefault(c.lower(), c)
    return list(out.values())


def targets(chat_id, chat_ids, draft_id: str | None = None) -> list:
    """Where a post goes: the chats given, else the ones its draft is meant for, else TG_DEFAULT_CHAT."""
    if not chat_ids and chat_id in (None, "") and draft_id:
        d = store.get_draft(draft_id)
        if d is None:  # a deleted draft must not silently send to the default channel
            raise StudioError(f"черновика {draft_id} нет: укажите chat_id явно")
        chat_ids = d.get("chat_ids")
    if chat_ids:
        return [resolve_chat(c) for c in normalize_chats(chat_ids)]
    return [resolve_chat(chat_id)]


def check_thread(chats: list, post: dict) -> None:
    """A forum topic belongs to one chat: refuse it for several chats or for a known non-forum one."""
    tid = (post.get("options") or {}).get("message_thread_id")
    if tid in (None, "") and isinstance(post.get("poll_after"), dict):
        tid = post["poll_after"].get("message_thread_id")  # the poll after the post goes to the same chat
    if tid in (None, ""):
        return
    if len(chats) > 1:
        raise StudioError("тема форума (message_thread_id) задаётся для одного канала, а каналов несколько")
    ch = store.get_channel(chats[0])
    if ch and ch.get("type") and not ch.get("is_forum"):
        raise StudioError(f"{ch.get('title') or chats[0]} не форум: уберите message_thread_id из options")


def options(post: dict) -> dict:
    opts = post.get("options") or {}
    bad = RESERVED_PARAMS & set(opts)
    if bad:
        raise StudioError(f"в options нельзя задавать {', '.join(sorted(bad))}")
    return opts


def check(post: dict) -> dict:
    """Validation report of the text and of the buttons under it; raises nothing."""
    r = spec.validate_rich_message(spec.current, post["rich_message"])
    bad_buttons = spec.keyboard_errors(post.get("reply_markup"))
    if bad_buttons:
        r = {**r, "ok": False, "errors": r["errors"] + bad_buttons}
    return r


def validate(post: dict) -> dict:
    """Validation report; raises Invalid if the post or its first comment has errors."""
    options(post)
    r = check(post)
    if not r["ok"]:
        raise Invalid({"message": "пост не прошёл проверку", **r})
    if post.get("comment"):
        c = spec.validate_rich_message(spec.current, post["comment"])
        if not c["ok"]:
            raise Invalid(
                {
                    "message": "первый комментарий не прошёл проверку",
                    **c,
                    "errors": ["комментарий: " + e for e in c["errors"]],
                }
            )
        r = {**r, "warnings": r["warnings"] + ["комментарий: " + w for w in c["warnings"]]}
    if post.get("poll_after") is not None and (bad := poll_error(post["poll_after"])):
        raise Invalid({"message": "опрос после поста не прошёл проверку", **r, "ok": False, "errors": [bad]})
    return r


def poll_error(poll, label: str = "опрос после поста") -> str | None:
    """What sendPoll would refuse before it is asked: a poll after a post goes out only once the post is sent,
    a scheduled poll only at its time."""
    if not isinstance(poll, dict):
        return f"{label}: ожидался объект sendPoll"
    if "chat_id" in poll:
        return f"{label}: chat_id задаётся не в опросе"
    if not str(poll.get("question") or "").strip():
        return f"{label}: пустой вопрос"
    opts = poll.get("options")
    texts = [o.get("text") if isinstance(o, dict) else o for o in opts] if isinstance(opts, list) else []
    if len([t for t in texts if str(t or "").strip()]) < 2:
        return f"{label}: нужно минимум два варианта"
    if len(texts) > 12:
        return f"{label}: больше 12 вариантов"
    if len(str(poll["question"])) > 300:
        return f"{label}: вопрос длиннее 300 символов"
    if poll.get("type") == "quiz" and not poll.get("correct_option_ids") and poll.get("correct_option_id") is None:
        return f"{label}: у викторины не отмечен правильный ответ"
    normalized = {**poll, "options": [o if isinstance(o, dict) else {"text": str(o)} for o in opts or []]}
    bad = spec.method_errors(spec.current, "sendPoll", normalized, label, frozenset({"chat_id"}))
    return bad[0] if bad else None


STARS_MAX = 25000  # sendPaidMedia.star_count: 1-25000


def media_error(kind: str, params) -> str | None:
    """What sendLivePhoto / sendPaidMedia would refuse before it is asked (a scheduled one only at its time)."""
    label = MEDIA_LABELS[kind]
    if not isinstance(params, dict):
        return f"{label}: ожидался объект {MEDIA_METHODS[kind]}"
    if "chat_id" in params:
        return f"{label}: chat_id задаётся не здесь"
    if kind == "paid_media":
        media = params.get("media")
        if not isinstance(media, list) or not 1 <= len(media) <= 10:
            return f"{label}: в media от 1 до 10 фото или видео"
        stars = params.get("star_count")
        if isinstance(stars, int) and not isinstance(stars, bool) and not 1 <= stars <= STARS_MAX:
            return f"{label}: star_count от 1 до {STARS_MAX} звёзд"
    bad = spec.method_errors(spec.current, MEDIA_METHODS[kind], params, label, frozenset({"chat_id"}))
    return bad[0] if bad else None


def media_kind(post: dict) -> str | None:
    """live_photo or paid_media when the post is one of those media messages, else None."""
    return next((k for k in MEDIA_METHODS if k in post), None)


def poll_after(post: dict) -> dict:
    """sendPoll parameters of the poll that follows a post: the post's topic and silence unless the poll sets its own."""
    poll = dict(post["poll_after"])
    poll["options"] = [o if isinstance(o, dict) else {"text": str(o)} for o in poll["options"]]
    opts = post.get("options") or {}
    for key in ("message_thread_id", "disable_notification"):
        if key in opts and key not in poll:
            poll[key] = opts[key]
    return poll


def parse_at(at: int | str) -> int:
    """Unix time or ISO 8601 with a time zone; not in the past."""
    if isinstance(at, str):
        if at.isdigit():
            at = int(at)
        else:
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                raise StudioError("время: unix или ISO 8601, например 2026-10-10T09:00:00+03:00") from None
            if dt.tzinfo is None:
                raise StudioError("укажите часовой пояс, например 2026-10-10T09:00:00+03:00")
            at = int(dt.timestamp())
    if at < time.time() - 60:
        raise StudioError("время публикации уже прошло")
    return at


async def send(chat_id, post: dict) -> dict:
    """One Bot API call: sendPoll for polls, sendLivePhoto / sendPaidMedia for those, sendRichMessage otherwise."""
    method, params = request(chat_id, post)
    return await tg.call(method, params)


def request(chat_id, post: dict) -> tuple[str, dict]:
    """The Bot API method and parameters a send makes, exactly as they go to Telegram."""
    if "poll" in post:
        return "sendPoll", {**post["poll"], "chat_id": chat_id}
    if kind := media_kind(post):
        return MEDIA_METHODS[kind], {**post[kind], "chat_id": chat_id}
    params = {**options(post), "chat_id": chat_id, "rich_message": post["rich_message"]}
    if post.get("reply_markup"):
        params["reply_markup"] = post["reply_markup"]
    return "sendRichMessage", params


async def after_send(pid: str, post: dict, res: dict, actor: str = "api") -> list[str]:
    """Remember the numeric chat id, then pin, queue the first comment and send the poll after it. Returns warnings."""
    warnings = []
    fields: dict[str, Any] = {"chat_num": (res.get("chat") or {}).get("id")}
    if res.get("poll"):
        fields["poll_id"] = res["poll"].get("id")
    if post.get("comment"):
        if settings.get().updates_on:
            fields["comment_status"] = "waiting"
        else:  # nobody will see the forward to the discussion group
            fields["comment_status"] = "skipped"
            warnings.append("первый комментарий не будет опубликован: приём событий выключен (TG_UPDATES=off)")
    store.update_post(pid, **fields)
    if post.get("pin"):
        try:
            await tg.call(
                "pinChatMessage",
                {
                    "chat_id": res["chat"]["id"],
                    "message_id": res["message_id"],
                    "disable_notification": bool((post.get("options") or {}).get("disable_notification", True)),
                },
            )
            store.update_post(pid, pinned=1)
        except tg.TelegramError as e:
            warnings.append(f"не удалось закрепить: {e.description}")
        except Exception as e:  # noqa: BLE001 - network: the post itself is already sent
            warnings.append(f"не удалось закрепить: {type(e).__name__}: {e}")
    if post.get("poll_after") and "poll" not in post:
        warnings += await _send_poll_after(pid, post, res, actor)
    return warnings


async def _send_poll_after(pid: str, post: dict, res: dict, actor: str) -> list[str]:
    """The poll that goes right after the post, as its own row in the list of posts; a failure is only a warning."""
    rec = store.get_post(pid) or {}
    chat = rec.get("chat_id") or res["chat"]["id"]
    sent = {"poll": poll_after(post)}
    try:
        r = await tg.call("sendPoll", {**sent["poll"], "chat_id": res["chat"]["id"]})
    except tg.TelegramError as e:
        _record_failure(chat, sent, rec.get("draft_id"), e.description, "poll", actor)
        return [f"опрос после поста не отправлен: {e.description}"]
    except Exception as e:  # noqa: BLE001 - never resent on its own, like any send that may have gone
        err = f"{type(e).__name__}: {e}. {POLL_MAY_HAVE_GONE}"
        _record_failure(chat, sent, rec.get("draft_id"), err, "poll", actor)
        return [f"опрос после поста: {err}"]
    poll_rec = store.add_post(chat, sent, rec.get("draft_id"), "sent", r["message_id"], kind="poll", actor=actor)
    store.update_post(poll_rec["id"], chat_num=(r.get("chat") or {}).get("id"), poll_id=(r.get("poll") or {}).get("id"))
    return []


def _record_failure(chat, post: dict, draft_id: str | None, error: str, kind: str, actor: str) -> None:
    """A failed send as a row in the list of posts; never hides Telegram's own error behind a database one."""
    try:
        store.add_post(chat, post, draft_id, "failed", error=error, kind=kind, actor=actor)
    except Exception:
        log.exception("could not record the failed send to %s", chat)


def _mark_failed(pid: str, error: str) -> None:
    """The row written before the send gets the outcome; never hides Telegram's own error behind a database one."""
    try:
        store.update_post(pid, status="failed", error=error)
    except Exception:
        log.exception("could not record the failed send %s", pid)


async def publish(chats: list, post: dict, draft_id: str | None = None, kind: str = "rich", actor: str = "api"):
    """Send to every chat; one failing channel does not stop the others. Returns (results, warnings).

    Each send is written down as "sending" before Telegram is asked, like a scheduled one: if the process dies
    between Telegram's answer and the update, the row is still there, and the next start marks it "may have gone"."""
    out: list[dict] = []
    warnings: list[str] = []
    for chat in chats:
        rec = store.add_post(chat, post, draft_id, "sending", kind=kind, actor=actor)
        in_flight.add(rec["id"])
        try:
            res = await send(chat, post)
        except tg.TelegramError as e:
            # kept in the list of posts, so a refusal is not lost with the toast that showed it
            _mark_failed(rec["id"], e.description)
            if len(chats) == 1:
                raise
            out.append({"chat_id": chat, "ok": False, "error": e.description})
            continue
        except StudioError:  # the request itself is wrong (e.g. a missing upload): nothing went out anywhere
            store.delete_post(rec["id"])
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {e}. {MAY_HAVE_GONE}"
            _mark_failed(rec["id"], err)
            if len(chats) == 1:
                raise MayHaveGone(err) from e
            out.append({"chat_id": chat, "ok": False, "error": err})
            continue
        except asyncio.CancelledError:  # shutdown mid-send: the request may have reached Telegram
            _mark_failed(rec["id"], f"студия остановилась во время отправки. {MAY_HAVE_GONE}")
            raise
        finally:
            in_flight.discard(rec["id"])
        store.update_post(rec["id"], status="sent", message_id=res["message_id"], sent_at=int(time.time()))
        if draft_id:
            store.drop_refused(chat, draft_id, MAY_HAVE_GONE, POLL_MAY_HAVE_GONE)
        w = await after_send(rec["id"], post, res, actor)
        warnings += [f"{chat}: {x}" for x in w] if len(chats) > 1 else w
        out.append({"chat_id": chat, "ok": True, "post": store.get_post(rec["id"]), "message": res})
    return out, warnings
