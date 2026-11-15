"""Incoming Telegram updates (getUpdates): button clicks, first comments, reactions, poll results, joins,
suggested posts, posts the owner forwards to the bot."""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass

import logs
import metrics
import settings
import store
import telegram as tg
from errors import StudioError

from . import channels, forward_import, invite_links, publisher, suggested_posts

log = logging.getLogger("rich-studio.updates")
# tests replace this one name; patching asyncio.sleep itself would also stop the fake Telegram server
_sleep = asyncio.sleep
OFFSET_KEY = "updates_offset"
PREVIEW_KEY = "preview_callbacks"
PREVIEWS_KEPT = 20  # button answers of the newest previews; older preview buttons get an empty answer
ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "message_reaction_count",
    "poll",
    "chat_member",
    "chat_join_request",
]
# Telegram keeps undelivered updates for about a day; a forward older than that will never arrive
COMMENT_WAIT = 24 * 3600
COMMENT_MAY_HAVE_GONE = "Он мог уйти, проверьте обсуждение"


@dataclass
class Status:
    enabled: bool = True
    state: str = "starting"  # starting | ok | off | no_token | conflict | error
    error: str | None = None
    last_at: int | None = None
    handled: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


STATUS = Status()


def reset_status() -> None:
    on = settings.get().updates_on
    STATUS.__init__(enabled=on, state="starting" if on else "off")  # type: ignore[misc]


def remember_preview(chat_id, message_id: int, callbacks: dict) -> None:
    """Keep the answers to the buttons of a preview in the DM: previews are not posts, but clicks come all the same."""
    saved = json.loads(store.get_meta(PREVIEW_KEY) or "{}")
    saved[f"{chat_id}:{message_id}"] = callbacks
    store.set_meta(PREVIEW_KEY, json.dumps(dict(list(saved.items())[-PREVIEWS_KEPT:]), ensure_ascii=False))


def _preview_callbacks(chat_id, message_id) -> dict:
    return json.loads(store.get_meta(PREVIEW_KEY) or "{}").get(f"{chat_id}:{message_id}") or {}


def _reaction_key(r: dict) -> str:
    t = r.get("type") or {}
    return t.get("emoji") or ("custom:" + t["custom_emoji_id"] if t.get("custom_emoji_id") else t.get("type", "?"))


def poll_results(poll: dict) -> dict:
    return {
        "total": poll.get("total_voter_count", 0),
        "is_closed": poll.get("is_closed", False),
        "options": [{"text": o.get("text"), "voter_count": o.get("voter_count", 0)} for o in poll.get("options", [])],
    }


async def handle_update(u: dict) -> str | None:
    """React to one Telegram update. Unknown updates are ignored. Returns what was handled."""
    if cq := u.get("callback_query"):
        msg = cq.get("message") or {}
        p = store.find_post((msg.get("chat") or {}).get("id"), msg.get("message_id")) if msg else None
        params = {"callback_query_id": cq["id"]}
        data = cq.get("data") or ""
        if p:
            store.bump_click(p["id"], data)
            callbacks = (p["payload"] or {}).get("callbacks") or {}
        else:
            callbacks = _preview_callbacks((msg.get("chat") or {}).get("id"), msg.get("message_id")) if msg else {}
        ans = callbacks.get(data)
        if isinstance(ans, str):
            ans = {"text": ans}
        if isinstance(ans, dict):
            params.update({k: v for k, v in ans.items() if k in ("text", "show_alert", "url", "cache_time")})
        try:
            await tg.call("answerCallbackQuery", params)  # always, or the button keeps spinning
        except tg.TelegramError as e:
            # the click came while the studio was off: Telegram no longer takes an answer, the click still counts
            if "query is too old" not in e.description:
                raise
            log.warning("button click came too late to answer: %s", e.description)
        return "callback"
    if (m := u.get("message") or u.get("edited_message")) and suggested_posts.on_message(m):
        return "suggested_post"
    if (m := u.get("message")) and suggested_posts.on_service(m):
        return "suggested_post_outcome"
    if (m := u.get("message")) and (topic := m.get("forum_topic_created") or m.get("forum_topic_edited")):
        # the Bot API cannot list a forum's topics: the ones created or renamed while the studio runs are learned here
        if topic.get("name"):
            channels.remember_topic((m.get("chat") or {}).get("id"), m.get("message_thread_id"), topic["name"])
            return "topic"
        return None
    if (m := u.get("message")) and m.get("is_automatic_forward"):
        origin = m.get("forward_origin") or {}
        if origin.get("type") != "channel":
            return None
        p = store.find_post(origin["chat"]["id"], origin.get("message_id"))
        if not p or p.get("comment_status") != "waiting":
            return None
        return "comment" if await _send_comment(p, m) else None
    if rc := u.get("message_reaction_count"):
        p = store.find_post(rc["chat"]["id"], rc["message_id"])
        if p:
            store.update_post(
                p["id"], reactions={_reaction_key(r): r.get("total_count", 0) for r in rc.get("reactions", [])}
            )
        return "reactions"
    if poll := u.get("poll"):
        p = store.find_poll(poll["id"])
        if p:
            store.update_post(p["id"], poll_results=poll_results(poll))
        return "poll"
    if cm := u.get("chat_member"):
        return "join" if invite_links.on_member(cm) else None
    if jr := u.get("chat_join_request"):
        return "join_request" if invite_links.on_join_request(jr) else None
    if (m := u.get("message")) and forward_import.wanted(m):  # the owner forwarded a post: a draft of it
        return await forward_import.on_message(m)
    return None


async def _send_comment(p: dict, forward: dict) -> bool:
    """The first comment of post `p`, as a reply to its forward in the discussion group. Sent like a post: taken as
    "sending" before Telegram is asked, the request in publisher.in_flight (a stop waits for it). A stop or a crash
    mid-request leaves "may have gone", never "the forward did not come" a day later; nothing sends it again (the
    update is confirmed already). False: nothing to send, the comment no longer waits."""
    pid = p["id"]
    publisher.in_flight.add(pid)  # before the claim: health must never see "sending" without the id here
    try:
        if not store.claim_comment(pid):  # taken meanwhile (the same forward twice), or no longer waiting
            return False
        try:
            res = await tg.call(
                "sendRichMessage",
                {
                    "chat_id": forward["chat"]["id"],
                    "rich_message": p["payload"].get("comment"),
                    "reply_parameters": {"message_id": forward["message_id"]},
                },
            )
        except tg.TelegramError as e:
            _fail_comment(pid, f"первый комментарий: {e.description}")
            return True
        except StudioError as e:  # the request itself is wrong (e.g. a missing upload): nothing went out
            _fail_comment(pid, f"первый комментарий: {e}")
            return True
        except Exception as e:  # noqa: BLE001 - mid-send: the update is already confirmed, so say it once here
            _fail_comment(pid, f"первый комментарий: {type(e).__name__}: {e}. {COMMENT_MAY_HAVE_GONE}")
            return True
        except asyncio.CancelledError:  # shutdown mid-send: the comment may be in the discussion
            _fail_comment(pid, f"первый комментарий: студия остановилась во время отправки. {COMMENT_MAY_HAVE_GONE}")
            raise
        try:
            store.update_post(pid, comment_status="sent", comment_message_id=res["message_id"])
        except Exception as e:  # noqa: BLE001 - in the discussion all the same: its id goes to the log
            # the comment stays "sending": the recovery after the next start says it may have gone
            publisher.log_unrecorded(forward["chat"]["id"], res["message_id"], pid, e, "first comment of a post")
        return True
    finally:
        publisher.in_flight.discard(pid)


def _fail_comment(pid: str, error: str) -> None:
    """Never raises: a database error must not hide Telegram's own, nor end a cancellation."""
    try:
        store.fail_comment(pid, error)
    except Exception:
        log.exception("could not record the failed first comment of post %s: %s", pid, error)


async def loop() -> None:
    # Telegram re-sends updates that were not confirmed by the next getUpdates call;
    # remembering the offset keeps a restart from counting the same clicks twice
    saved = store.get_meta(OFFSET_KEY)
    offset = int(saved) if saved else None
    while True:
        # one round is a long poll (up to 40 s) or a wait after an error (up to 60 s)
        metrics.beat("updates", 60, STATUS.error if STATUS.state in ("error", "conflict") else None)
        if not settings.get().bot_token:
            STATUS.state = "no_token"
            await _sleep(30)
            continue
        try:
            ups = await tg.call(
                "getUpdates", {"offset": offset, "timeout": 25, "allowed_updates": ALLOWED_UPDATES}, timeout=40
            )
            STATUS.state, STATUS.error = "ok", None
            store.expire_comments(
                int(time.time()) - COMMENT_WAIT, "первый комментарий: пересылка в обсуждение не пришла за сутки"
            )
            for u in ups:
                offset = u["update_id"] + 1
                store.set_meta(OFFSET_KEY, str(offset))
                with logs.bind(f"update-{u['update_id']}"):  # Telegram's own id: easy to find again
                    try:
                        if await handle_update(u):
                            STATUS.handled += 1
                            STATUS.last_at = int(time.time())
                    except Exception:
                        log.exception("update %s failed", u.get("update_id"))
        except tg.TelegramError as e:
            # 409: a webhook is set or another process polls the same bot
            STATUS.state = "conflict" if e.code == 409 else "error"
            STATUS.error = e.description
            await _sleep(60 if e.code == 409 else 10)
        except Exception as e:  # noqa: BLE001 - keep polling whatever happens
            STATUS.state, STATUS.error = "error", f"{type(e).__name__}: {e}"
            await _sleep(10)
