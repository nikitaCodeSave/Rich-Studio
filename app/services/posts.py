"""What the studio does with a post: publish, preview, schedule, edit, pin, buttons, delete, polls.

Functions take plain dicts and return what the API returns; they never import FastAPI.
"""

import logging

import settings
import spec
import store
import telegram as tg
from errors import Conflict, Invalid, MayHaveGone, NotFound, StudioError, Upstream
from telegram.uploads import MissingUpload, collect_uploads

from . import channels, publisher
from .updates import poll_results, remember_preview

log = logging.getLogger("rich-studio.posts")


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


def _not_an_edit_copy(draft_id: str | None, as_new: bool) -> None:
    """A draft with post_id is a working copy of that post: sending it would put a second post in the channel.
    The editor does that on purpose after its own warning (as_new); anyone else meant to replace the post."""
    if not draft_id or as_new:
        return
    d = store.get_draft(draft_id)
    if d and d.get("post_id"):
        raise Conflict(
            f"черновик {draft_id} — правка поста {d['post_id']}: замените пост (POST /api/posts/{d['post_id']}/edit, "
            "MCP edit_post) или передайте as_new: true, чтобы опубликовать копию новым постом"
        )


async def send(
    post: dict,
    chat_id=None,
    chat_ids=None,
    draft_id: str | None = None,
    actor: str = "api",
    dry_run: bool = False,
    as_new: bool = False,
) -> dict:
    _not_an_edit_copy(draft_id, as_new)
    report = publisher.validate(post)
    chats = publisher.targets(chat_id, chat_ids, draft_id)
    publisher.check_thread(chats, post)
    if dry_run:
        return dry_run_report(chats, post, report["warnings"])
    results, warnings = await publisher.publish(chats, post, draft_id, actor=actor)
    for r in results:  # the agent and the editor name the channel, not only its id
        r["chat_title"] = channels.title_of(r["chat_id"])
    first = next((r for r in results if r["ok"]), None)
    if not first:
        detail = {
            "message": "ни в один канал не отправлено",
            "errors": [f"{r['chat_id']}: {r['error']}" for r in results],
        }
        if any(publisher.MAY_HAVE_GONE in r["error"] for r in results):
            raise MayHaveGone(detail)
        raise Upstream(detail)
    return {
        "post": first["post"],
        "message": first["message"],
        "results": results,
        "warnings": report["warnings"] + warnings,
    }


def dry_run_report(chats: list, post: dict, warnings: list[str]) -> dict:
    """Everything a send would do, without Telegram and without a record in the history."""
    media_dir = settings.get().media_dir
    requests = []
    for chat in chats:
        method, params = publisher.request(chat, post)
        try:
            collect_uploads(params, {}, media_dir)
        except MissingUpload as e:
            raise StudioError(str(e)) from None
        requests.append({"method": method, "params": params, "chat_title": channels.title_of(chat)})
    steps = [
        s
        for s, on in (
            ("pinChatMessage", post.get("pin")),
            ("first comment", post.get("comment")),
            ("sendPoll (poll_after)", post.get("poll_after") and "poll" not in post),
        )
        if on
    ]
    return {"dry_run": True, "requests": requests, "after_send": steps, "warnings": warnings}


async def preview(post: dict, chat_id=None) -> dict:
    publisher.validate(post)
    target = chat_id or settings.get().preview_chat
    if not target:  # never fall back to the default channel: a preview must not go public
        raise StudioError("не задан TG_PREVIEW_CHAT (ваш user id) — некуда отправить предпросмотр")
    chat = publisher.resolve_chat(target)
    res = await publisher.send(chat, post)
    out: dict = {"message_id": res["message_id"], "chat_id": chat}
    # how Telegram itself parsed the post: the editor compares it with its own preview and can
    # take these blocks into the visual mode
    blocks = (res.get("rich_message") or {}).get("blocks")
    if isinstance(blocks, list):
        out["parsed"] = {"blocks": blocks}
        # the message is already out: a block shape the converter did not expect must not fail the answer
        try:
            out["parsed"]["input_blocks"] = spec.to_input_blocks(spec.current, blocks)
        except Exception:
            log.exception("parsed blocks could not be converted to input blocks")
    if post.get("callbacks"):  # so the buttons of the preview answer like in the channel
        remember_preview(chat, res["message_id"], post["callbacks"])
    if post.get("comment"):  # show the first comment right under the preview
        await tg.call(
            "sendRichMessage",
            {"chat_id": chat, "rich_message": post["comment"], "reply_parameters": {"message_id": res["message_id"]}},
        )
    return out


NOT_MODIFIED = "message is not modified"


def after_edit(old: dict, post: dict) -> dict:
    """The stored post after editMessageText: new text and buttons, everything else as it was.

    Pin and first comment were done at publish time, and answers to buttons stay unless the new
    post brings its own (an agent usually sends only rich_message). A comment still waiting for the
    forward can be replaced.
    """
    new = {**old, "rich_message": post["rich_message"]}
    if post.get("reply_markup"):
        new["reply_markup"] = post["reply_markup"]
    else:  # no reply_markup removes the buttons
        new.pop("reply_markup", None)
    if post.get("callbacks"):
        new["callbacks"] = post["callbacks"]
    if post.get("comment"):
        new["comment"] = post["comment"]
    return new


async def edit_message(
    post: dict, chat_id, message_id: int, actor: str = "api", rec: dict | None = None, claimed: bool = False
) -> dict:
    """Replace the content of any channel message (editMessageText + rich_message).

    If the studio published it (`rec`, or found by chat and message id), the record follows the
    channel and the content before the edit is kept as a version. `claimed`: the caller already took
    the post's next version (`edit`)."""
    publisher.validate(post)
    params = {
        "chat_id": publisher.resolve_chat(chat_id),
        "message_id": message_id,
        "rich_message": post["rich_message"],
    }
    if post.get("reply_markup"):
        params["reply_markup"] = post["reply_markup"]
    try:
        result = await tg.call("editMessageText", params)
    except tg.TelegramError as e:
        # the same text and buttons again (e.g. only the answers to buttons changed): nothing to do in Telegram
        if NOT_MODIFIED not in e.description:
            raise
        result = None
    # if the studio published this message, keep its history in step with the channel
    rec = rec or store.find_sent(params["chat_id"], message_id)
    if rec and rec.get("kind") != "poll":
        new = after_edit(rec["payload"], post)
        if new != rec["payload"]:  # a repeated edit is not a new version
            if not claimed:
                store.claim_version(rec["id"], None)
            store.replace_sent(rec["id"], new, rec["payload"], actor)
    return {"result": result, "unchanged": result is None}


def schedule(
    post: dict,
    at,
    chat_id=None,
    chat_ids=None,
    draft_id: str | None = None,
    actor: str = "api",
    dry_run: bool = False,
    as_new: bool = False,
) -> dict:
    _not_an_edit_copy(draft_id, as_new)
    report = publisher.validate(post)
    when = publisher.parse_at(at)
    chats = publisher.targets(chat_id, chat_ids, draft_id)
    publisher.check_thread(chats, post)
    if dry_run:
        return {**dry_run_report(chats, post, report["warnings"]), "at": when}
    recs = [
        {**r, "chat_title": channels.title_of(r["chat_id"])}
        for r in store.add_scheduled(chats, post, when, draft_id, actor=actor)
    ]
    return recs[0] if len(recs) == 1 else {**recs[0], "all": recs}


async def edit(pid: str, post: dict, actor: str = "api", post_version: int | None = None) -> dict | None:
    """Replace a post published or scheduled through the studio.

    `post_version`: the version the caller read (get_post); someone else's edit since then gives 409
    instead of being overwritten silently."""
    p = _get(pid)
    if p["status"] not in ("scheduled", "sent"):
        raise StudioError(f"пост в статусе {p['status']}, заменить нельзя")
    if p.get("kind") == "poll":
        raise StudioError("опрос заменить нельзя; можно закрыть или удалить")
    if p.get("kind") in publisher.MEDIA_METHODS:
        raise StudioError(f"{publisher.MEDIA_LABELS[p['kind']]} заменить нельзя; можно поменять кнопки или удалить")
    if p["status"] == "sent" and not p["message_id"]:
        raise StudioError(f"пост в статусе {p['status']}, редактировать нечего")
    publisher.validate(post)
    taken = store.claim_version(pid, post_version)
    if taken is None:
        now = (store.get_post(pid) or {}).get("version")
        raise Conflict(
            f"пост {pid} уже заменили (версия {now}, а у вас {post_version}): прочитайте его заново (get_post) "
            "и повторите правку поверх новой версии"
        )
    if p["status"] == "scheduled":
        if not store.replace_scheduled(pid, post, p["payload"], actor):
            store.release_version(pid, taken)
            raise StudioError("пост уже отправляется: обновите список и замените опубликованный")
        return store.get_post(pid)
    try:
        await edit_message(post, p["chat_id"], p["message_id"], actor, rec=p, claimed=True)
    except (StudioError, tg.TelegramError):  # refused, nothing changed in the channel: the version is not used
        store.release_version(pid, taken)
        raise
    except Exception as e:  # network error mid-edit: the edit may have gone through, the version stays taken
        raise MayHaveGone(
            f"{type(e).__name__}: {e}. Правка могла пройти: проверьте пост в канале (get_post покажет записанное)"
        ) from e
    return store.get_post(pid)


def versions(pid: str) -> list[dict]:
    """What the post looked like before each replacement, newest first."""
    _get(pid)
    return store.list_versions(pid)


def reschedule(pid: str, at) -> dict | None:
    """Move a waiting post, or bring back a missed one, to a new time."""
    when = publisher.parse_at(at)
    if not store.reschedule(pid, when):
        raise StudioError(f"пост в статусе {_get(pid)['status']}: перенести можно отложенный или пропущенный")
    return store.get_post(pid)


async def delete(pid: str) -> dict | None:
    """Cancel a scheduled post, delete a published one from the channel, or drop a failed send from the list."""
    p = _get(pid)
    if p["status"] == "failed":  # nothing is in the channel: only the row goes
        store.delete_post(pid)
        return {**p, "status": "removed"}
    if p["status"] == "sent" and p["message_id"]:
        await tg.call("deleteMessage", {"chat_id": publisher.resolve_chat(p["chat_id"]), "message_id": p["message_id"]})
        return store.update_post(pid, status="deleted")
    if p["status"] in ("scheduled", "missed") and store.cancel_scheduled(pid):
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
    bad = spec.keyboard_errors(reply_markup)
    if bad:  # the same rule as a full send: Telegram would refuse the whole request
        raise Invalid({"message": "кнопки не прошли проверку", "ok": False, "errors": bad, "warnings": []})
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
        try:
            await tg.call("editMessageReplyMarkup", params)
        except tg.TelegramError as e:
            # the same buttons again (e.g. only their answers changed): nothing to do in Telegram
            if NOT_MODIFIED not in e.description:
                raise
    store.claim_version(pid, None)
    return store.update_post(pid, payload=payload)


# ---------- polls ----------
async def send_poll(poll: dict, chat_id=None, chat_ids=None, at=None, pin: bool = False, actor: str = "api") -> dict:
    poll = dict(poll)
    if "chat_id" in poll:
        raise StudioError("chat_id задаётся не в poll")
    if not poll.get("question") or not isinstance(poll.get("options"), list) or len(poll["options"]) < 2:
        raise StudioError("нужны question и минимум два варианта в options")
    poll["options"] = [o if isinstance(o, dict) else {"text": str(o)} for o in poll["options"]]
    # the same checks as a poll after a post: a scheduled poll would otherwise fail only at its time
    if bad := publisher.poll_error(poll, "опрос"):
        raise Invalid({"message": "опрос не прошёл проверку", "ok": False, "errors": [bad], "warnings": []})
    post = {"poll": poll, "pin": pin}
    if at is not None:
        when = publisher.parse_at(at)
        recs = store.add_scheduled(publisher.targets(chat_id, chat_ids), post, when, kind="poll", actor=actor)
        return {"scheduled": recs}
    results, warnings = await publisher.publish(publisher.targets(chat_id, chat_ids), post, kind="poll", actor=actor)
    return {"results": results, "warnings": warnings}


# ---------- Live Photo and paid media ----------
async def send_media(
    kind: str, params: dict, chat_id=None, chat_ids=None, at=None, pin: bool = False, actor: str = "api"
) -> dict:
    """A Live Photo (sendLivePhoto) or photos and videos for stars (sendPaidMedia): a message of its own, not a
    rich post. Parameters go to Telegram as they are, files as upload:<name>."""
    if kind not in publisher.MEDIA_METHODS:
        raise StudioError(f"kind: {' или '.join(publisher.MEDIA_METHODS)}")
    if bad := publisher.media_error(kind, params):
        raise Invalid({"message": "сообщение не прошло проверку", "ok": False, "errors": [bad], "warnings": []})
    if params.get("reply_markup") and (errs := spec.keyboard_errors(params["reply_markup"])):
        raise Invalid({"message": "кнопки не прошли проверку", "ok": False, "errors": errs, "warnings": []})
    post = {kind: dict(params), "pin": pin}
    if at is not None:
        when = publisher.parse_at(at)
        recs = store.add_scheduled(publisher.targets(chat_id, chat_ids), post, when, kind=kind, actor=actor)
        return {"scheduled": recs}
    results, warnings = await publisher.publish(publisher.targets(chat_id, chat_ids), post, kind=kind, actor=actor)
    return {"results": results, "warnings": warnings}


async def stop_poll(pid: str) -> dict | None:
    p = _sent(pid)
    if p.get("kind") != "poll":
        raise StudioError("это не опрос")
    res = await tg.call("stopPoll", {"chat_id": publisher.resolve_chat(p["chat_id"]), "message_id": p["message_id"]})
    return store.update_post(pid, poll_results=poll_results(res))
