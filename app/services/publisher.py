"""Sending posts to Telegram: target chats, validation, send, the steps after a send.

Error policy (see docs/REVIEW_CHECKLIST.md, section 2):
- once a send succeeded, the post is recorded as sent; later steps only add warnings;
- a network error during a send means "may have gone out": never retried automatically;
- one failing channel never stops the others.
"""

import re
import time
from datetime import datetime
from typing import Any

import settings
import spec
import store
import telegram as tg
from errors import Invalid, MayHaveGone, StudioError

RESERVED_PARAMS = {"chat_id", "rich_message", "reply_markup", "message_id"}
MAY_HAVE_GONE = "Пост мог уйти в канал, проверьте перед повтором"


def resolve_chat(chat_id) -> int | str:
    """@username stays a string, numeric ids become int; empty means TG_DEFAULT_CHAT."""
    c = chat_id if chat_id not in (None, "") else settings.get().default_chat
    if c in (None, ""):
        raise StudioError("не указан chat_id и не задан TG_DEFAULT_CHAT")
    if isinstance(c, str) and re.fullmatch(r"-?\d+", c):
        return int(c)
    return c


def targets(chat_id, chat_ids) -> list:
    if chat_ids:
        return [resolve_chat(c) for c in dict.fromkeys(chat_ids)]
    return [resolve_chat(chat_id)]


def options(post: dict) -> dict:
    opts = post.get("options") or {}
    bad = RESERVED_PARAMS & set(opts)
    if bad:
        raise StudioError(f"в options нельзя задавать {', '.join(sorted(bad))}")
    return opts


def validate(post: dict) -> dict:
    """Validation report; raises Invalid if the post or its first comment has errors."""
    options(post)
    r = spec.validate_rich_message(spec.current, post["rich_message"])
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
    return r


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
    """One Bot API call: sendPoll for polls, sendRichMessage for everything else."""
    method, params = request(chat_id, post)
    return await tg.call(method, params)


def request(chat_id, post: dict) -> tuple[str, dict]:
    """The Bot API method and parameters a send makes, exactly as they go to Telegram."""
    if "poll" in post:
        return "sendPoll", {**post["poll"], "chat_id": chat_id}
    params = {**options(post), "chat_id": chat_id, "rich_message": post["rich_message"]}
    if post.get("reply_markup"):
        params["reply_markup"] = post["reply_markup"]
    return "sendRichMessage", params


async def after_send(pid: str, post: dict, res: dict) -> list[str]:
    """Remember the numeric chat id, then pin and queue the first comment. Returns warnings."""
    warnings = []
    fields: dict[str, Any] = {"chat_num": (res.get("chat") or {}).get("id")}
    if res.get("poll"):
        fields["poll_id"] = res["poll"].get("id")
    if post.get("comment"):
        fields["comment_status"] = "waiting"
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
    return warnings


async def publish(chats: list, post: dict, draft_id: str | None = None, kind: str = "rich", actor: str = "api"):
    """Send to every chat; one failing channel does not stop the others. Returns (results, warnings)."""
    out: list[dict] = []
    warnings: list[str] = []
    for chat in chats:
        try:
            res = await send(chat, post)
        except tg.TelegramError as e:
            if len(chats) == 1:
                raise
            out.append({"chat_id": chat, "ok": False, "error": e.description})
            continue
        except StudioError:  # the request itself is wrong (e.g. a missing upload): same for every channel
            raise
        except Exception as e:
            err = f"{type(e).__name__}: {e}. {MAY_HAVE_GONE}"
            if len(chats) == 1:
                raise MayHaveGone(err) from e
            out.append({"chat_id": chat, "ok": False, "error": err})
            continue
        rec = store.add_post(chat, post, draft_id, "sent", res["message_id"], kind=kind, actor=actor)
        w = await after_send(rec["id"], post, res)
        warnings += [f"{chat}: {x}" for x in w] if len(chats) > 1 else w
        out.append({"chat_id": chat, "ok": True, "post": store.get_post(rec["id"]), "message": res})
    return out, warnings
