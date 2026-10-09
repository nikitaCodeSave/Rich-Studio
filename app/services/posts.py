"""What the studio does with a post: publish, preview, schedule, edit, pin, buttons, delete, polls.

Functions take plain dicts and return what the API returns; they never import FastAPI.
"""

import settings
import store
import telegram as tg
from errors import NotFound, StudioError, Upstream

from . import publisher
from .updates import poll_results


def _get(pid: str) -> dict:
    p = store.get_post(pid)
    if not p:
        raise NotFound("Not Found")
    return p


def _sent(pid: str) -> dict:
    p = _get(pid)
    if p["status"] != "sent" or not p["message_id"]:
        raise StudioError(f"пост в статусе {p['status']}: он ещё не в канале")
    return p


async def send(post: dict, chat_id=None, chat_ids=None, draft_id: str | None = None) -> dict:
    report = publisher.validate(post)
    results, warnings = await publisher.publish(publisher.targets(chat_id, chat_ids), post, draft_id)
    first = next((r for r in results if r["ok"]), None)
    if not first:
        raise Upstream(
            {"message": "ни в один канал не отправлено", "errors": [f"{r['chat_id']}: {r['error']}" for r in results]}
        )
    return {
        "post": first["post"],
        "message": first["message"],
        "results": results,
        "warnings": report["warnings"] + warnings,
    }


async def preview(post: dict, chat_id=None) -> dict:
    publisher.validate(post)
    target = chat_id or settings.get().preview_chat
    if not target:  # never fall back to the default channel: a preview must not go public
        raise StudioError("не задан TG_PREVIEW_CHAT (ваш user id) — некуда отправить предпросмотр")
    chat = publisher.resolve_chat(target)
    res = await publisher.send(chat, post)
    out = {"message_id": res["message_id"], "chat_id": chat}
    if post.get("comment"):  # show the first comment right under the preview
        await tg.call(
            "sendRichMessage",
            {"chat_id": chat, "rich_message": post["comment"], "reply_parameters": {"message_id": res["message_id"]}},
        )
    return out


async def edit_message(post: dict, chat_id, message_id: int) -> dict:
    """Replace the content of any channel message (editMessageText + rich_message)."""
    publisher.validate(post)
    params = {
        "chat_id": publisher.resolve_chat(chat_id),
        "message_id": message_id,
        "rich_message": post["rich_message"],
    }
    if post.get("reply_markup"):
        params["reply_markup"] = post["reply_markup"]
    result = await tg.call("editMessageText", params)
    # if the studio published this message, keep its history in step with the channel
    rec = store.find_sent(params["chat_id"], message_id)
    if rec and rec.get("kind") != "poll":
        # only what editMessageText changed: text and buttons (no reply_markup removes them);
        # callbacks, first comment and pin settings stay as they were
        payload = {**rec["payload"], "rich_message": post["rich_message"]}
        if post.get("reply_markup"):
            payload["reply_markup"] = post["reply_markup"]
        else:
            payload.pop("reply_markup", None)
        store.update_post(rec["id"], payload=payload)
    return {"result": result}


def schedule(post: dict, at, chat_id=None, chat_ids=None, draft_id: str | None = None) -> dict:
    publisher.validate(post)
    when = publisher.parse_at(at)
    recs = [
        store.add_post(c, post, draft_id, "scheduled", scheduled_at=when) for c in publisher.targets(chat_id, chat_ids)
    ]
    return recs[0] if len(recs) == 1 else {**recs[0], "all": recs}


async def edit(pid: str, post: dict) -> dict | None:
    """Replace a post published or scheduled through the studio."""
    p = _get(pid)
    if p["status"] not in ("scheduled", "sent"):
        raise StudioError(f"пост в статусе {p['status']}, заменить нельзя")
    if p.get("kind") == "poll":
        raise StudioError("опрос заменить нельзя; можно закрыть или удалить")
    if p["status"] == "scheduled":
        publisher.validate(post)
        return store.update_post(pid, payload=post)
    if not p["message_id"]:
        raise StudioError(f"пост в статусе {p['status']}, редактировать нечего")
    await edit_message(post, p["chat_id"], p["message_id"])
    return store.update_post(pid, payload=post)


async def delete(pid: str) -> dict | None:
    """Cancel a scheduled post or delete a published one from the channel."""
    p = _get(pid)
    if p["status"] == "sent" and p["message_id"]:
        await tg.call("deleteMessage", {"chat_id": publisher.resolve_chat(p["chat_id"]), "message_id": p["message_id"]})
        return store.update_post(pid, status="deleted")
    if p["status"] == "scheduled" and store.cancel_scheduled(pid):
        return store.get_post(pid)
    raise StudioError(f"пост в статусе {_get(pid)['status']}: удалять или отменять нечего")


async def pin(pid: str, disable_notification: bool = True) -> dict | None:
    p = _sent(pid)
    await tg.call(
        "pinChatMessage",
        {
            "chat_id": publisher.resolve_chat(p["chat_id"]),
            "message_id": p["message_id"],
            "disable_notification": disable_notification,
        },
    )
    return store.update_post(pid, pinned=1)


async def unpin(pid: str) -> dict | None:
    p = _sent(pid)
    await tg.call("unpinChatMessage", {"chat_id": publisher.resolve_chat(p["chat_id"]), "message_id": p["message_id"]})
    return store.update_post(pid, pinned=0)


async def set_buttons(pid: str, reply_markup: dict | None, callbacks: dict | None) -> dict | None:
    """Replace only the buttons (editMessageReplyMarkup); the text is untouched."""
    p = _get(pid)
    if p["status"] not in ("sent", "scheduled"):
        raise StudioError(f"пост в статусе {p['status']}")
    payload = dict(p["payload"])
    if reply_markup and reply_markup.get("inline_keyboard"):
        payload["reply_markup"] = reply_markup
    else:
        payload.pop("reply_markup", None)
    if callbacks is not None:
        payload["callbacks"] = callbacks
    if p["status"] == "sent":
        params = {"chat_id": publisher.resolve_chat(p["chat_id"]), "message_id": p["message_id"]}
        if payload.get("reply_markup"):
            params["reply_markup"] = payload["reply_markup"]
        await tg.call("editMessageReplyMarkup", params)
    return store.update_post(pid, payload=payload)


# ---------- polls ----------
async def send_poll(poll: dict, chat_id=None, chat_ids=None, at=None, pin: bool = False) -> dict:
    poll = dict(poll)
    if "chat_id" in poll:
        raise StudioError("chat_id задаётся не в poll")
    if not poll.get("question") or not isinstance(poll.get("options"), list) or len(poll["options"]) < 2:
        raise StudioError("нужны question и минимум два варианта в options")
    poll["options"] = [o if isinstance(o, dict) else {"text": str(o)} for o in poll["options"]]
    post = {"poll": poll, "pin": pin}
    if at is not None:
        when = publisher.parse_at(at)
        recs = [
            store.add_post(c, post, None, "scheduled", scheduled_at=when, kind="poll")
            for c in publisher.targets(chat_id, chat_ids)
        ]
        return {"scheduled": recs}
    results, warnings = await publisher.publish(publisher.targets(chat_id, chat_ids), post, kind="poll")
    return {"results": results, "warnings": warnings}


async def stop_poll(pid: str) -> dict | None:
    p = _sent(pid)
    if p.get("kind") != "poll":
        raise StudioError("это не опрос")
    res = await tg.call("stopPoll", {"chat_id": publisher.resolve_chat(p["chat_id"]), "message_id": p["message_id"]})
    return store.update_post(pid, poll_results=poll_results(res))
