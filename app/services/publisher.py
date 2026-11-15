"""Sending posts to Telegram: target chats, validation, send, the steps after a send.

Error policy (see docs/REVIEW_CHECKLIST.md, section 2):
- once a send succeeded, the post is recorded as sent; later steps only add warnings;
- a send the database refused to record (a full or read-only disk) is in the channel all the same: the answer says
  where, with its message_id, and the record is written later (`unrecorded`);
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

from . import kinds

log = logging.getLogger("rich-studio.publisher")
RESERVED_PARAMS = {"chat_id", "rich_message", "reply_markup", "message_id"}
MAY_HAVE_GONE = "Пост мог уйти в канал, проверьте перед повтором"
POLL_MAY_HAVE_GONE = "Опрос мог уйти в канал, проверьте перед повтором"
UNRECORDED = "опубликован, студия не смогла записать — не публикуйте повторно"
# ids of posts this process is sending right now (now or from the scheduler): their "sending" status is normal,
# not stuck; health reads it, and startup recovery only exists because a crash empties it
in_flight: set[str] = set()
# sends Telegram accepted whose "sent" the database refused (a full or read-only disk): post id → what to write.
# write_unrecorded tries again before each send, each pass of the scheduler and the recovery of "sending" rows, which
# leaves these rows alone meanwhile. Health still shows them as stuck in "sending": the record is behind the channel
unrecorded: dict[str, dict] = {}


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
    if post.get("poll_after") is not None and (bad := kinds.poll_error(post["poll_after"])):
        raise Invalid({"message": "опрос после поста не прошёл проверку", **r, "ok": False, "errors": [bad]})
    return r


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


async def send(chat_id, post: dict, kind: str | None = kinds.RICH) -> dict:
    """One Bot API call, the method of the post's kind (services/kinds.py)."""
    method, params = request(chat_id, post, kind)
    return await tg.call(method, params)


def request(chat_id, post: dict, kind: str | None = kinds.RICH) -> tuple[str, dict]:
    """The Bot API method and parameters a send makes, exactly as they go to Telegram."""
    k = kinds.get(kind)
    if k.key:  # a message with parameters of its own: they go as they are
        return k.method, {**post[k.key], "chat_id": chat_id}
    params = {**options(post), "chat_id": chat_id, "rich_message": post["rich_message"]}
    if post.get("reply_markup"):
        params["reply_markup"] = post["reply_markup"]
    return "sendRichMessage", params


async def after_send(pid: str, post: dict, res: dict, actor: str = "api", kind: str | None = kinds.RICH) -> list[str]:
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
    if post.get("poll_after") and kind == kinds.RICH:  # the poll after a post is a field of a rich post
        warnings += await _send_poll_after(pid, post, res, actor)
    return warnings


async def _send_poll_after(pid: str, post: dict, res: dict, actor: str) -> list[str]:
    """The poll that goes right after the post, as its own row in the list of posts; a failure is only a warning."""
    rec = store.get_post(pid) or {}
    chat = rec.get("chat_id") or res["chat"]["id"]
    sent = {"poll": poll_after(post)}
    try:
        r = await send(res["chat"]["id"], sent, kinds.POLL)
    except tg.TelegramError as e:
        _record_failure(chat, sent, rec.get("draft_id"), e.description, kinds.POLL, actor)
        return [f"опрос после поста не отправлен: {e.description}"]
    except Exception as e:  # noqa: BLE001 - never resent on its own, like any send that may have gone
        err = f"{type(e).__name__}: {e}. {POLL_MAY_HAVE_GONE}"
        _record_failure(chat, sent, rec.get("draft_id"), err, kinds.POLL, actor, unknown=True)
        return [f"опрос после поста: {err}"]
    try:
        poll_rec = store.add_post(
            chat, sent, rec.get("draft_id"), "sent", r["message_id"], kind=kinds.POLL, actor=actor
        )
    except Exception as e:  # noqa: BLE001 - in the channel all the same: its id goes to the log and the answer
        _log_unrecorded(chat, r["message_id"], None, e)
        return [f"опрос после поста {UNRECORDED} (message_id {r['message_id']}): {_reason(e)}"]
    try:
        store.update_post(
            poll_rec["id"], chat_num=(r.get("chat") or {}).get("id"), poll_id=(r.get("poll") or {}).get("id")
        )
    except Exception as e:
        log.exception("could not record the id of the poll after post %s", pid)
        return [f"итоги опроса после поста не будут видны в студии: {_reason(e)}"]
    return []


def _reason(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def _log_unrecorded(chat, message_id: int, pid: str | None, e: BaseException) -> None:
    log.error(
        "sent to %s as message %s, but the studio could not record it: %s",
        chat,
        message_id,
        _reason(e),
        extra={"event": "send.unrecorded", "chat_id": str(chat), "message_id": message_id, "post_id": pid},
    )


def unrecorded_send(pid: str, chat, post: dict, res: dict, sent_at: int, e: BaseException) -> dict:
    """Telegram accepted the post, the database refused its "sent": an error in the log with the message id, the
    record kept for write_unrecorded; the steps after the send (pin, comment, poll) are not done. The result for this
    chat: in the channel (`ok`), not written down (`unrecorded`), why (`error`)."""
    mid = res["message_id"]
    unrecorded[pid] = {
        "message_id": mid,
        "sent_at": sent_at,
        "chat_num": (res.get("chat") or {}).get("id"),
        "poll_id": (res.get("poll") or {}).get("id"),
    }
    _log_unrecorded(chat, mid, pid, e)
    steps = (("закреп", "pin"), ("первый комментарий", "comment"), ("опрос после поста", "poll_after"))
    skipped = [name for name, field in steps if post.get(field)]
    error = _reason(e) + (f"; не сделано: {', '.join(skipped)}" if skipped else "")
    return {
        "chat_id": chat,
        "ok": True,
        "unrecorded": True,
        "post_id": pid,
        "message_id": mid,
        "message": res,
        "error": error,
    }


def write_unrecorded() -> int:
    """Write down the sends the database refused at the time (`unrecorded`). Returns how many still wait."""
    for pid, fields in list(unrecorded.items()):
        try:
            found = store.confirm_sent(pid, **fields)
        except Exception:  # noqa: BLE001 - the database still refuses: the next chance
            break
        del unrecorded[pid]
        extra = {"event": "send.recorded", "post_id": pid, "message_id": fields["message_id"]}
        if found:
            log.info("post %s recorded as sent, late", pid, extra=extra)
        else:  # removed from the list meanwhile (after another process marked it "may have gone")
            log.error(
                "post %s is in the channel as message %s, its row is gone", pid, fields["message_id"], extra=extra
            )
    return len(unrecorded)


async def _after_recorded(
    rec: dict, chat, post: dict, res: dict, sent_at: int, draft_id: str | None, actor: str, kind: str
) -> tuple[dict, list[str]]:
    """The steps after the post is recorded as sent, and the record as it is now. The post is out: a failure of any
    of them is a warning, never an error."""
    warnings: list[str] = []
    if draft_id:
        try:
            store.drop_refused(chat, draft_id)
        except Exception as e:
            log.exception("could not drop the earlier refusals of draft %s in %s", draft_id, chat)
            warnings.append(f"прежние отказы черновика остались в списке постов: {_reason(e)}")
    try:
        warnings += await after_send(rec["id"], post, res, actor, kind)
    except Exception as e:
        log.exception("after-send steps failed for %s", rec["id"])
        warnings.append(f"шаги после отправки прерваны: {_reason(e)}")
    try:
        row = store.get_post(rec["id"])
    except Exception as e:
        log.exception("could not read post %s back", rec["id"])
        warnings.append(f"запись поста не прочитана после отправки: {_reason(e)}")
        row = None
    return row or {**rec, "status": "sent", "message_id": res["message_id"], "sent_at": sent_at}, warnings


def _record_failure(
    chat, post: dict, draft_id: str | None, error: str, kind: str, actor: str, unknown: bool = False
) -> None:
    """A failed send as a row in the list of posts; never hides Telegram's own error behind a database one.
    `unknown`: it may have gone all the same (store.mark_outcome_unknown)."""
    try:
        store.add_post(chat, post, draft_id, "failed", error=error, kind=kind, actor=actor, outcome_unknown=unknown)
    except Exception:
        log.exception("could not record the failed send to %s", chat)


def _mark_failed(pid: str, error: str, unknown: bool = False) -> None:
    """The row written before the send gets the outcome; never hides Telegram's own error behind a database one.
    `unknown`: the post may be in the channel all the same (store.mark_outcome_unknown)."""
    try:
        if unknown:
            store.mark_outcome_unknown(pid, error)
        else:
            store.update_post(pid, status="failed", error=error)
    except Exception:
        log.exception("could not record the failed send %s", pid)


async def publish(chats: list, post: dict, draft_id: str | None = None, kind: str = kinds.RICH, actor: str = "api"):
    """Send to every chat; one failing channel does not stop the others. Returns (results, warnings).

    Each send is written down as "sending" before Telegram is asked, like a scheduled one: if the process dies
    between Telegram's answer and the update, the row is still there, and the next start marks it "may have gone".
    If the database refuses the update, the result says the post is out all the same (unrecorded_send)."""
    out: list[dict] = []
    warnings: list[str] = []
    write_unrecorded()  # what an earlier send could not write down goes first
    for chat in chats:
        try:
            rec = store.add_post(chat, post, draft_id, "sending", kind=kind, actor=actor)
        except Exception as e:
            if not any(r["ok"] for r in out):  # nothing is out anywhere: the request fails as it is, nothing was sent
                raise
            # the post is in an earlier chat already: the answer must still say where, so this one is "not sent"
            log.exception("could not write the row of the send to %s; not sent there", chat)
            error = f"не отправлено: студия не смогла записать строку поста ({_reason(e)})"
            out.append({"chat_id": chat, "ok": False, "error": error})
            continue
        in_flight.add(rec["id"])
        try:
            res = await send(chat, post, kind)
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
            _mark_failed(rec["id"], err, unknown=True)
            if len(chats) == 1:
                raise MayHaveGone(err) from e
            out.append({"chat_id": chat, "ok": False, "error": err})
            continue
        except asyncio.CancelledError:  # shutdown mid-send: the request may have reached Telegram
            _mark_failed(rec["id"], f"студия остановилась во время отправки. {MAY_HAVE_GONE}", unknown=True)
            raise
        finally:
            in_flight.discard(rec["id"])
        sent_at = int(time.time())
        try:
            store.update_post(rec["id"], status="sent", message_id=res["message_id"], sent_at=sent_at)
        except Exception as e:  # noqa: BLE001 - the post is in the channel: say where, never "not sent" or a bare 500
            r = unrecorded_send(rec["id"], chat, post, res, sent_at, e)
            warnings.append(f"{chat}: пост {UNRECORDED} (message_id {r['message_id']}): {r['error']}")
            out.append(r)
            continue
        row, w = await _after_recorded(rec, chat, post, res, sent_at, draft_id, actor, kind)
        warnings += [f"{chat}: {x}" for x in w] if len(chats) > 1 else w
        out.append({"chat_id": chat, "ok": True, "post": row, "message": res})
    return out, warnings
