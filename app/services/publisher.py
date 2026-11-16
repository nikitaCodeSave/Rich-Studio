"""Sending posts to Telegram: target chats, validation, send, the steps after a send.

Error policy (see docs/REVIEW_CHECKLIST.md, section 2):
- once a send succeeded, the post is recorded as sent; later steps only add warnings;
- a send the database refused to record (a full or read-only disk) is in the channel all the same: the answer says
  where, with its message_id, and the record is written later (`unrecorded`);
- a network error during a send means "may have gone out": never retried automatically;
- one failing channel never stops the others, nor undoes the ones the post went to: a refusal (Telegram's or the
  studio's) after that is the result of its channel, never an error of the request;
- a send Telegram or the studio refused, or that may have gone, is recorded as failed with the error.
"""

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Collection, Iterable, Iterator
from datetime import datetime
from typing import Any

import settings
import spec
import store
import telegram as tg
from errors import Invalid, MayHaveGone, StudioError

from . import callbacks, kinds, media

log = logging.getLogger("rich-studio.publisher")
# parameters of sendRichMessage the studio sets itself, never options: where the post goes and what it is
RESERVED_PARAMS = {"chat_id", "rich_message", "reply_markup", "message_id", "ephemeral_message_parameters"}
# why, where it is more than «the studio sets it»
RESERVED_WHY = {
    # with callback_query_id Telegram ignores chat_id and shows the message to whoever pressed that button, with
    # receiver_user_id to one member of the chat: either way not a post of the channel, yet the studio would record
    # one, pin it, comment under it. A rich reply to a button press is callbacks[…].reply (#163, ADR 0021)
    "ephemeral_message_parameters": "с ним сообщение видит один человек, а не канал, и студия записала бы его "
    "постом; rich-ответ на нажатие кнопки задаётся в callbacks[…].reply",
}
MAY_HAVE_GONE = "Пост мог уйти в канал, проверьте перед повтором"
POLL_MAY_HAVE_GONE = "Опрос мог уйти в канал, проверьте перед повтором"
UNRECORDED = "опубликован, студия не смогла записать — не публикуйте повторно"


class InFlight:
    """Ids being sent right now, each with a count of its owners; reads like a set (`pid in`, iteration, len).

    One id can have two owners: the scheduler holds a post's id until its steps after the send are done (pin, the
    poll after it), and getUpdates holds the same id while it sends the post's first comment, whose forward may come
    meanwhile. The first to finish must not take the id from the other."""

    def __init__(self, ids: Iterable[str] = ()):
        self._owners: Counter[str] = Counter(ids)

    def add(self, pid: str) -> None:
        self._owners[pid] += 1

    def discard(self, pid: str) -> None:
        if self._owners[pid] > 1:
            self._owners[pid] -= 1
        else:
            self._owners.pop(pid, None)

    def __contains__(self, pid: object) -> bool:
        return pid in self._owners

    def __iter__(self) -> Iterator[str]:
        return iter(list(self._owners))

    def __len__(self) -> int:
        return len(self._owners)


# ids of posts this process is sending right now (now or from the scheduler, the poll after a post too, and posts
# whose first comment it is sending): their "sending" status is normal, not stuck; health reads it, a stop waits for
# it to empty, and startup recovery only exists because a crash empties it
in_flight = InFlight()
# ids of posts whose first comment this process is sending right now, in in_flight as well (a stop waits for them
# there). Their comment's "sending" is normal; the list of posts and health read this one, not in_flight: the
# scheduler holds a post's id there for its pin and poll after the send, and a comment gone unconfirmed meanwhile
# must not look under way
comments_in_flight = InFlight()
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


def checked_targets(chat_ids=None, draft_id: str | None = None) -> list:
    """Where a post would go, for its check: as targets() says, but a wrong or missing chat is left out, the others
    stay (a check raises nothing; publishing tells what is wrong)."""
    if not chat_ids and draft_id:
        d = store.get_draft(draft_id)
        if d is None:  # a deleted draft goes nowhere, not to the default channel
            return []
        chat_ids = d.get("chat_ids")
    if chat_ids:
        good = []
        for c in chat_ids:
            try:
                normalize_chats([c])
            except StudioError:
                continue
            good.append(c)
        return targets(None, good) if good else []
    try:
        return targets(None, None)
    except StudioError:  # no default channel
        return []


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


def options_error(post: dict) -> str | None:
    """What options must not hold (RESERVED_PARAMS), or None."""
    opts = post.get("options") or {}
    bad = sorted(RESERVED_PARAMS & set(opts)) if isinstance(opts, dict) else []
    if not bad:
        return None
    why = "; ".join(f"{k}: {RESERVED_WHY[k]}" for k in bad if k in RESERVED_WHY)
    return f"в options нельзя задавать {', '.join(bad)}" + (f" ({why})" if why else "")


# the top-level fields of a post the studio reads (models.Post; tests/test_regressions.py keeps them in step)
POST_FIELDS = frozenset({"rich_message", "reply_markup", "options", "pin", "comment", "callbacks", "poll_after"})
# where the post goes, put in the post itself
TARGET_FIELDS = ("chat_id", "chat_ids")
# where the chats go instead, said by the request that has them (target_errors): this one is a publish, a schedule or
# a draft (/api/send, /api/schedule, /api/drafts, their MCP tools); a preview, an edit and /api/validate say their own
CHATS_NEXT_TO_POST = "chat_id или chat_ids рядом с post"
# the same for a person in the editor (`ui`, the X-Studio-Client header the api layer reads): where to take the field out,
# and where the channels are chosen
UI_CHATS = "уберите поле во вкладке JSON; каналы — в окне «Каналы»"
UI_FIELD = "уберите поле во вкладке JSON или перенесите его в options"


def target_errors(post: dict, chats_hint: str = CHATS_NEXT_TO_POST, ui: bool = False) -> list[str]:
    """A chat inside the post. Not an unknown field but a wrong address: the post would go to the draft's channels or
    the default one. Where a message goes is the request's to say, so it is an error like chat_id in options
    (RESERVED_PARAMS): in the check, and a 400 before any send (options()). `chats_hint`: where that request takes
    the chats; `ui`: the person in the editor, who is told where to take the field out instead."""
    chats_hint = UI_CHATS if ui else chats_hint
    return [
        f"поле «{k}» на верхнем уровне поста — ошибка: куда уходит пост, задаёт запрос, а не пост ({chats_hint})"
        for k in TARGET_FIELDS
        if k in post
    ]


def options(post: dict, chats_hint: str = CHATS_NEXT_TO_POST, ui: bool = False) -> dict:
    """The options a send takes; refused (StudioError, 400) before anything goes out if they or the post itself say
    where it goes."""
    if bad := [e for e in (options_error(post), *target_errors(post, chats_hint, ui)) if e]:
        raise StudioError("; ".join(bad))
    return post.get("options") or {}


def unused_fields(post: dict, ui: bool = False) -> list[str]:
    """A warning for each top-level field the studio does not read. It stays in the post as it came (AGENTS.md rule
    1), never reaches Bot API (request() takes only what it knows) and is not an error: an agent that put a parameter
    in the wrong place learns it did nothing. `_studio`, a draft's notes, is not a field of the post; a chat inside
    the post is an error (target_errors)."""
    return [
        f"поле «{k}» на верхнем уровне поста не используется и в Telegram не уходит; {UI_FIELD if ui else 'параметры Bot API кладите в options'}"
        for k in post
        if k not in POST_FIELDS and k not in TARGET_FIELDS and k != "_studio"
    ]


def check_uploads(post: dict, must_exist: bool = True, fields: Collection[str] | None = None) -> None:
    """upload:<name> in whatever of the post goes to Telegram, now or later (the post or a kind's own parameters, its
    first comment, the poll after it, the rich replies to its buttons) names a file of the uploads; StudioError (400)
    as a send gives, before anything is written. A field the studio does not read never goes out, so is not looked
    at. must_exist=False, a draft or a template: only a name that is a path is refused (media.check_refs).
    `fields`: only these go out (an edit of a sent post, edit_uploads)."""
    if fields is None:
        fields = POST_FIELDS | {k.key for k in kinds.KINDS.values() if k.key}
    media.check_refs({k: v for k, v in post.items() if k in fields}, must_exist)


# what an edit of a sent post sends: the text and the buttons now (editMessageText), the replies to them at a press
EDIT_SENDS = frozenset({"rich_message", "reply_markup", "callbacks"})


def edit_uploads(rec: dict | None) -> frozenset[str]:
    """The fields of an edit of the sent post `rec` that still go to Telegram, whose files are checked: EDIT_SENDS,
    and the first comment while it waits for its forward. The poll after the post and the pin were done at publish
    time (the editor sends poll_after with every edit all the same): a file of theirs deleted since is no reason to
    refuse."""
    return EDIT_SENDS | ({"comment"} if rec and rec.get("comment_status") == "waiting" else set())


# the first comment goes to the discussion group of the channel, a supergroup
DISCUSSION = ("supergroup", "в группе обсуждения")


def places(chats: Iterable) -> list[tuple[str, str]]:
    """The chats a post goes to as spec.chats.warnings reads them: (type, where in words). Only a chat the studio
    checked (services/channels.py) has a type; one nobody checked gets no warnings. Raises nothing."""
    out = []
    for c in chats:
        try:
            ch = store.get_channel(c)
        except Exception:  # a warning is not worth a failed check
            log.exception("could not read channel %s for the chat rules", c)
            continue
        if ch and ch.get("type"):
            out.append(spec.chats.place(ch["type"], bool(ch.get("is_forum")), ch.get("title"), c))
    return list(dict.fromkeys(out))


def _check_post(
    post: dict, where: list[tuple[str, str]], chats_hint: str = CHATS_NEXT_TO_POST, ui: bool = False
) -> dict:
    """Validation report of the text, of the buttons under it, of the options and of a chat inside the post
    (`chats_hint`: where the caller takes the chats), with what Telegram refuses in the chats it goes to (`where`,
    from places()) and the top-level fields that do nothing (unused_fields)."""
    r = spec.validate_rich_message(spec.current, post["rich_message"])
    bad = spec.keyboard_errors(post.get("reply_markup")) + ([e] if (e := options_error(post)) else [])
    bad += target_errors(post, chats_hint, ui)
    if bad:
        r = {**r, "ok": False, "errors": r["errors"] + bad}
    refused = spec.chats.warnings(spec.current, post, where)
    return {**r, "warnings": unused_fields(post, ui) + r["warnings"] + refused}


def _check_comment(post: dict) -> dict | None:
    """The first comment's own report, its messages marked as the comment's; None without a comment."""
    if not post.get("comment"):
        return None
    c = spec.validate_rich_message(spec.current, post["comment"])
    refused = spec.chats.warnings(spec.current, {"rich_message": post["comment"]}, [DISCUSSION])
    return {
        **c,
        "errors": ["комментарий: " + e for e in c["errors"]],
        "warnings": ["комментарий: " + w for w in c["warnings"] + refused],
    }


def _check_poll_after(post: dict) -> dict | None:
    if post.get("poll_after") is None:
        return None
    bad = kinds.poll_error(post["poll_after"])
    return {"errors": [bad] if bad else [], "warnings": []}


def check(post: dict, chats: Iterable = (), chats_hint: str = CHATS_NEXT_TO_POST, ui: bool = False) -> dict:
    """Validation report of the post, its first comment, the rich replies to its buttons and the poll after it (a
    draft is saved with it, the editor shows it), with warnings for what Telegram refuses in `chats`, the ones it goes
    to; raises nothing. The stats are the post's own. `chats_hint`: where the caller takes the chats, for chat_ids put
    in the post."""
    where = places(chats)
    r = _check_post(post, where, chats_hint, ui)
    for part in (_check_comment(post), callbacks.check(post, where), _check_poll_after(post)):
        if part:
            r = {
                **r,
                "ok": r["ok"] and not part["errors"],
                "errors": r["errors"] + part["errors"],
                "warnings": r["warnings"] + part["warnings"],
            }
    return r


def validate(
    post: dict,
    chats: Iterable = (),
    chats_hint: str = CHATS_NEXT_TO_POST,
    uploads_in: Collection[str] | None = None,
    ui: bool = False,
) -> dict:
    """Validation report; raises Invalid if the post, its first comment or a reply to its buttons has errors, and
    StudioError if a file it names is not in the uploads (check_uploads; `uploads_in`: only these fields go out). What
    Telegram refuses in `chats`, the ones it goes to, is a warning. `chats_hint`, `ui`: as in check()."""
    options(post, chats_hint, ui)
    where = places(chats)
    r = _check_post(post, where, chats_hint, ui)
    if not r["ok"]:
        raise Invalid({"message": "пост не прошёл проверку", **r})
    if c := _check_comment(post):
        if not c["ok"]:
            raise Invalid({"message": "первый комментарий не прошёл проверку", **c})
        r = {**r, "warnings": r["warnings"] + c["warnings"]}
    cb = callbacks.check(post, where)
    r = {**r, "warnings": r["warnings"] + cb["warnings"]}  # the reply's own warnings too, refused or not
    if cb["errors"]:
        raise Invalid({"message": "ответ на кнопку не прошёл проверку", **r, "ok": False, "errors": cb["errors"]})
    if post.get("poll_after") is not None and (bad := kinds.poll_error(post["poll_after"])):
        raise Invalid({"message": "опрос после поста не прошёл проверку", **r, "ok": False, "errors": [bad]})
    check_uploads(post, fields=uploads_in)
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
    """The poll that goes right after the post, as its own row in the list of posts; a failure is only a warning.

    Sent like a post: its row is written as "sending" before Telegram is asked and the request is in `in_flight`, so
    a stop or a crash mid-request leaves "may have gone" (and the flag), never no trace; it is never resent."""
    rec = store.get_post(pid) or {}
    chat = rec.get("chat_id") or res["chat"]["id"]
    sent = {"poll": poll_after(post)}
    try:
        poll_pid = store.add_post(chat, sent, rec.get("draft_id"), "sending", kind=kinds.POLL, actor=actor)["id"]
    except Exception as e:  # no row, no request: nothing went out
        log.exception("could not write the row of the poll after post %s; not sent", pid)
        return [f"опрос после поста не отправлен: студия не смогла записать его строку ({_reason(e)})"]
    in_flight.add(poll_pid)
    try:
        r = await send(res["chat"]["id"], sent, kinds.POLL)
    except tg.TelegramError as e:
        _mark_failed(poll_pid, e.description)
        return [f"опрос после поста не отправлен: {e.description}"]
    except StudioError as e:  # the request itself is wrong (e.g. a missing upload): nothing went out
        _mark_failed(poll_pid, str(e))
        return [f"опрос после поста не отправлен: {e}"]
    except Exception as e:  # noqa: BLE001 - never resent on its own, like any send that may have gone
        err = f"{_reason(e)}. {POLL_MAY_HAVE_GONE}"
        _mark_failed(poll_pid, err, unknown=True)
        return [f"опрос после поста: {err}"]
    except asyncio.CancelledError:  # shutdown mid-send: the request may have reached Telegram
        _mark_failed(poll_pid, f"студия остановилась во время отправки. {POLL_MAY_HAVE_GONE}", unknown=True)
        raise
    finally:
        in_flight.discard(poll_pid)
    sent_at = int(time.time())
    try:
        store.update_post(
            poll_pid,
            status="sent",
            message_id=r["message_id"],
            sent_at=sent_at,
            chat_num=(r.get("chat") or {}).get("id"),
            poll_id=(r.get("poll") or {}).get("id"),
        )
    except Exception as e:  # noqa: BLE001 - in the channel all the same: written later, like an unrecorded post
        gone = unrecorded_send(poll_pid, chat, sent, r, sent_at, e)
        return [f"опрос после поста {UNRECORDED} (message_id {gone['message_id']}): {gone['error']}"]
    return []


def _reason(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


def log_unrecorded(chat, message_id: int, pid: str | None, e: BaseException, what: str = "post") -> None:
    """Telegram accepted it, the database refused the record: the message id stays in the log (send.unrecorded)."""
    log.error(
        "%s sent to %s as message %s, but the studio could not record it: %s",
        what,
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
        "chat_id": str(chat),
        "message_id": mid,
        "sent_at": sent_at,
        "chat_num": (res.get("chat") or {}).get("id"),
        "poll_id": (res.get("poll") or {}).get("id"),
    }
    log_unrecorded(chat, mid, pid, e)
    skipped = _steps_asked(post)
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
            found = store.confirm_sent(
                pid, fields["message_id"], fields["sent_at"], fields["chat_num"], fields["poll_id"]
            )
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


UNRECORDED_TELL = (
    "Пост в канале: скажите об этом пользователю и не публикуйте его заново; message_id есть и в журнале студии "
    "(событие send.unrecorded)."
)
UNRECORDED_LATER = (
    "Запись студия допишет сама, когда база снова будет принимать запись (место на диске, права на папку данных)."
)
UNRECORDED_DONE = "Студия уже дописала запись: в списке постов он отправлен."
UNRECORDED_LOST = (
    "Студию перезапустили раньше, чем база приняла запись: в списке постов он «Исход неизвестен» (или запись уже "
    "убрали), хотя вышел точно; заменить, закрепить или удалить его из студии нельзя, только в Telegram."
)


def unrecorded_message(gone: list[dict]) -> str:
    """The headline of an Unrecorded answer, as things are now: `gone` are its entries (chat_id, message_id, post_id,
    error). The first answer and a retry with the same key say whether the record is still to come, done or lost."""
    where = "; ".join(f"{r['chat_id']}, message_id {r['message_id']} ({r['error']})" for r in gone)
    return f"Пост {UNRECORDED}: {where}. {UNRECORDED_TELL} {_record_state(gone)}".rstrip()


def _record_state(gone: list[dict]) -> str:
    if any(r.get("post_id") in unrecorded for r in gone):  # this process still holds the confirmation
        return UNRECORDED_LATER
    try:
        rows = [store.get_post(r["post_id"]) for r in gone]
    except Exception:  # noqa: BLE001 - the database cannot even be read: say only what is known for sure
        return ""
    return UNRECORDED_DONE if all(p and p["status"] == "sent" for p in rows) else UNRECORDED_LOST


def _steps_asked(post: dict) -> list[str]:
    """The steps after the send this post asks for, by name."""
    steps = (("закреп", "pin"), ("первый комментарий", "comment"), ("опрос после поста", "poll_after"))
    return [name for name, field in steps if post.get(field)]


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
        asked = _steps_asked(post)
        maybe_not = f", могли не выполниться: {', '.join(asked)}" if asked else ""
        warnings.append(f"шаги после отправки прерваны ({_reason(e)}){maybe_not}")
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


def may_be_out(r: dict) -> bool:
    """A result of publish() for one chat: the post is there, or may be (a send cut off mid-request)."""
    return bool(r["ok"]) or MAY_HAVE_GONE in (r.get("error") or "")


async def publish(chats: list, post: dict, draft_id: str | None = None, kind: str = kinds.RICH, actor: str = "api"):
    """Send to every chat; one failing channel does not stop the others. Returns (results, warnings).

    Each send is written down as "sending" before Telegram is asked, like a scheduled one: if the process dies
    between Telegram's answer and the update, the row is still there, and the next start marks it "may have gone".
    If the database refuses the update, the result says the post is out all the same (unrecorded_send).
    Once the post is (or may be) in one chat, a failure in the next is that chat's result, never an exception: a
    refusal raised from here (StudioError, TelegramError) means nothing went out anywhere, and frees the
    Idempotency-Key for a retry that sends the post again (#212)."""
    out: list[dict] = []
    warnings: list[str] = []
    write_unrecorded()  # what an earlier send could not write down goes first
    for chat in chats:
        try:
            rec = store.add_post(chat, post, draft_id, "sending", kind=kind, actor=actor)
        except Exception as e:
            if not any(may_be_out(r) for r in out):  # nothing is out anywhere: the request fails as it is
                raise
            # the post is (or may be) in an earlier chat: the answer must still say where, so this one is "not sent"
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
        except StudioError as e:  # the request itself is wrong (a file deleted since the check): not sent here
            if not any(may_be_out(r) for r in out):  # nor anywhere: no trace, and the request fails as it is
                store.delete_post(rec["id"])
                raise
            # an earlier chat has the post: kept as sent, this one is a failure of the send like a refusal
            _mark_failed(rec["id"], str(e))
            out.append({"chat_id": chat, "ok": False, "error": str(e)})
            continue
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
