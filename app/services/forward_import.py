"""A post the owner forwards to the bot becomes a draft with the same formatting.

Only the owner counts: the private chat of the person previews go to (TG_PREVIEW_CHAT, a user id), and only a
forward (forward_origin) or a copy of a rich post. Anything else is ignored without an answer, as before. Telegram
gives the bot the message itself: a rich post as rich_message (turned into input blocks by spec.to_input_blocks),
any other as text or caption with entities (spec.text_blocks) and its photo, video or file. Those file_ids work
for this bot, so nothing is downloaded or uploaded. Buttons only the original's bot can answer (callback) are
dropped, links and the rest stay.

The answer is a reaction on the forwarded message, or one short reply saying why no draft was made: the owner does
not want reports in the chat. Nothing is published: the draft has no channels. Where it came from is the studio's
own note (`_studio.source`), not part of the post. An album comes as one message per item; they go into one draft.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import settings
import spec
import store
import telegram as tg

from . import listing

log = logging.getLogger("rich-studio.forward_import")
REACTION = "✍"  # one of the emoji ReactionTypeEmoji allows: «written down»
# button actions (InlineKeyboardButton, RichMessageButton) bound to the bot that sent the original: its answers to
# clicks, its web app, a login signed with its token, its inline mode, its game and payment. Links and copy_text stay
BOT_ACTIONS = {
    "callback_data",
    "callback_game",
    "pay",
    "web_app",
    "login_url",
    "switch_inline_query",
    "switch_inline_query_current_chat",
    "switch_inline_query_chosen_chat",
}
MESSAGE_KEYS = {"voice_note": "voice"}  # media field of an InputRichBlock -> the Message field, where they differ
GRID = ("photo", "video")  # an album of these is a grid in Telegram: a collage in the draft
# a Message field holding content of its own, by the spec's wording («Message is a sticker…», «contains paid media»)
CONTENT_FIELD = re.compile(r"Optional\. (The )?[Mm]essage (is|contains)\b")
ALBUMS_KEY = "forward_albums"
ALBUMS_KEPT = 5  # albums still being received; their items come within seconds of each other


def owner_id() -> int | None:
    """TG_PREVIEW_CHAT when it is a person's id (a positive number): the only sender whose forwards are taken."""
    v = settings.get().preview_chat
    return int(v) if v.isdigit() else None


def wanted(m: dict) -> bool:
    owner = owner_id()
    chat, sender = m.get("chat") or {}, m.get("from") or {}
    return bool(
        owner
        and chat.get("type") == "private"
        and chat.get("id") == sender.get("id") == owner
        and not sender.get("is_bot")
        and (m.get("forward_origin") or m.get("rich_message"))
    )


@dataclass
class _Post:
    """What a received message gives the draft."""

    media: list  # its photo, video or file as a block
    text: list  # the other blocks
    rich: dict  # the rest of rich_message (is_rtl)
    markup: dict | None  # the keyboard under it, without buttons only the original's bot works with
    dropped: int  # how many such buttons were removed
    lost: list[str] = field(default_factory=list)  # content no rich block holds (Live Photo, paid media, sticker…)


async def on_message(m: dict) -> str:
    """Save the draft and react to the message, or say in one line why there is no draft."""
    if m.get("has_protected_content"):
        return await _refuse(m, "у поста защищённое содержимое, его запрещено копировать")
    try:
        warnings: list[str] = []
        post = _content(m, warnings)
        if not post.media and not post.text:  # checked after the buttons are gone: a post of them alone is empty
            why = f"{', '.join(post.lost)} в rich-пост не переносится" if post.lost else ""
            why = why or ("в посте только кнопки, которые работают у его бота" if post.dropped else "")
            why = why or "в сообщении нет текста, фото, видео, файла или rich-блоков"
            return await _refuse(m, f"нечего перенести: {why}")
        d = _save(m, post, warnings)
    except Exception:
        log.exception("forwarded message %s was not imported", m.get("message_id"))
        return await _refuse(m, "ошибка студии, подробности в журнале")
    log.info(
        "forward saved as draft %s",
        d["id"],
        extra={"event": "forward.import", "draft_id": d["id"], "warnings": len(warnings)},
    )
    reaction = [{"type": "emoji", "emoji": REACTION}]
    await _answer(
        "setMessageReaction", {"chat_id": m["chat"]["id"], "message_id": m["message_id"], "reaction": reaction}
    )
    return "forward_import"


def _content(m: dict, warnings: list[str]) -> _Post:
    sp = spec.current
    rich = m.get("rich_message")
    if isinstance(rich, dict):
        fields = {f["name"] for f in sp.types.get("InputRichMessage", {}).get("fields", [])}
        extra = {k: v for k, v in rich.items() if k != "blocks" and k in fields}
        media, text, lost = [], spec.to_input_blocks(sp, rich.get("blocks") or []), []
    else:
        extra = {}
        entities = m.get("entities") or m.get("caption_entities") or []
        text = spec.text_blocks(sp, m.get("text") or m.get("caption") or "", entities, warnings)
        media, lost = _media(sp, m), _lost(sp, m)
        if lost and (media or text):
            warnings.append(f"не перенесено, в rich-посте для этого нет блока: {', '.join(lost)}")
    dropped: list = []
    media, text = _without_bot_buttons(media, dropped), _without_bot_buttons(text, dropped)
    markup = _keyboard(m.get("reply_markup"), dropped)
    if dropped:
        warnings.append(
            f"убраны кнопки ({len(dropped)}), которые работают только у бота исходного поста (ответ на нажатие, "
            "веб-приложение, вход, inline-запрос, игра, оплата); ссылки и копирование текста остались"
        )
    return _Post(media, text, extra, markup, len(dropped), lost)


def _media_fields(sp: spec.Spec) -> list[tuple[str, str]]:
    """(block type, its media field) of every InputRichBlock that holds an InputMedia, in the spec's order."""
    out = []
    for name in sp.types.get("InputRichBlock", {}).get("subtypes", []):
        const = sp.type_const(name)
        for f in sp.types[name].get("fields", []):
            if const and (f["types"] or [""])[0].startswith("InputMedia"):
                out.append((const[1], f["name"]))
    return out


def _media(sp: spec.Spec, m: dict) -> list:
    """The message's photo, video, file… as an input block that reuses its file_id (at most one: an animation also
    comes as a document, so the first InputRichBlock with media wins)."""
    for kind, key in _media_fields(sp):
        if sent := m.get(MESSAGE_KEYS.get(key, key)):
            block = {"type": kind, key: sent, "has_spoiler": bool(m.get("has_media_spoiler"))}
            return spec.to_input_blocks(sp, [block])
    return []


def _lost(sp: spec.Spec, m: dict) -> list[str]:
    """Message fields with content of their own that no block takes: Live Photo (its still photo does go in), paid
    media, a sticker, a poll…"""
    taken = {"rich_message", *(MESSAGE_KEYS.get(key, key) for _, key in _media_fields(sp))}
    fields = sp.types.get("Message", {}).get("fields", [])
    return [
        f["name"]
        for f in fields
        if f["name"] not in taken and m.get(f["name"]) and CONTENT_FIELD.match(f.get("description", ""))
    ]


def _blocks(media: list, text: list, above: bool) -> list:
    if len(media) > 1 and all(b.get("type") in GRID for b in media):
        media = [{"type": "collage", "blocks": media}]
    return [*text, *media] if above else [*media, *text]


def _save(m: dict, post: _Post, warnings: list[str]) -> dict:
    media, text = post.media, post.text
    above = bool(m.get("show_caption_above_media"))
    group = m.get("media_group_id")
    albums = json.loads(store.get_meta(ALBUMS_KEY) or "{}")
    if group and (album := albums.get(group)) and (d := _add_to_album(album, media, text, warnings)):
        store.set_meta(ALBUMS_KEY, json.dumps(albums, ensure_ascii=False))
        return d
    payload: dict = {"rich_message": {"blocks": _blocks(media, text, above), **post.rich}}
    if post.markup:
        payload["reply_markup"] = post.markup
    payload["_studio"] = {"source": _source(m), "warnings": warnings}
    d = store.save_draft(None, _title(m, payload), payload)
    if d is None:  # only a save with a version can miss; this is a new draft
        raise RuntimeError("черновик не записан")
    if group:
        albums[group] = {"draft": d["id"], "version": d["version"], "media": media, "text": text, "above": above}
        store.set_meta(ALBUMS_KEY, json.dumps(dict(list(albums.items())[-ALBUMS_KEPT:]), ensure_ascii=False))
    return d


def _add_to_album(album: dict, media: list, text: list, warnings: list[str]) -> dict | None:
    """The next item of an album into the draft of its first one; None if the person changed or deleted that draft
    since: their edit is kept, the rest of the album goes to a new draft."""
    d = store.get_draft(album["draft"])
    if not d or d["version"] != album.get("version"):
        return None
    album["media"] += media
    album["text"] += text
    payload = d["payload"]
    payload["rich_message"]["blocks"] = _blocks(album["media"], album["text"], album["above"])
    studio = payload.setdefault("_studio", {})
    studio["warnings"] = list(dict.fromkeys([*studio.get("warnings", []), *warnings]))
    saved = store.save_draft(d["id"], d["title"], payload, d["version"], d.get("post_id"), d.get("chat_ids"))
    if saved:
        album["version"] = saved["version"]
    return saved


def _bot_only(button) -> bool:
    return isinstance(button, dict) and bool(BOT_ACTIONS & button.keys())


def _without_bot_buttons(value, dropped: list):
    """Content without the buttons only the original's bot answers: a button in text leaves its text, a row of
    buttons left empty goes."""
    if isinstance(value, list):
        out: list = []
        glue = False  # the last item is the text a removed button left: the text after it joins it
        for x in value:
            if _bot_only(x):
                dropped.append(x)
                continue
            y = _without_bot_buttons(x, dropped)
            if y is None:
                continue
            left = isinstance(y, str) and not isinstance(x, str)
            if isinstance(y, str) and out and isinstance(out[-1], str) and (left or glue):
                out[-1] += y
            else:
                out.append(y)
            glue = left
        return out
    if isinstance(value, dict):
        if value.get("type") == "button" and _bot_only(value.get("button")):
            dropped.append(value["button"])
            return value["button"].get("text", "")
        out_d = {k: _without_bot_buttons(v, dropped) for k, v in value.items()}
        if value.get("type") == "buttons" and not out_d.get("buttons"):
            return None
        return out_d
    return value


def _keyboard(markup, dropped: list) -> dict | None:
    rows = markup.get("inline_keyboard") if isinstance(markup, dict) else None
    kept = [r for r in (_without_bot_buttons(row, dropped) for row in rows or [] if isinstance(row, list)) if r]
    return {"inline_keyboard": kept} if kept else None


def _source(m: dict) -> dict:
    origin = m.get("forward_origin")
    src: dict = {"message_id": m.get("message_id"), "date": m.get("date")}
    if origin:
        src["forward_origin"] = origin
        chat = origin.get("chat") or {}
        if origin.get("type") == "channel" and origin.get("message_id"):
            if chat.get("username"):
                src["link"] = f"https://t.me/{chat['username']}/{origin['message_id']}"
            elif str(chat.get("id", "")).startswith("-100"):
                src["link"] = f"https://t.me/c/{str(chat['id'])[4:]}/{origin['message_id']}"
    return src


def _title(m: dict, payload: dict) -> str:
    origin = m.get("forward_origin") or {}
    chat = origin.get("chat") or origin.get("sender_chat") or {}
    user = origin.get("sender_user") or {}
    who = (
        chat.get("title")
        or ("@" + chat["username"] if chat.get("username") else "")
        or " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
        or origin.get("sender_user_name")
        or ""
    )
    line = listing.title(payload)
    return f"{who}: {line}" if who and line else who or line or "Пересылка"


async def _refuse(m: dict, why: str) -> str:
    log.info("forward not imported: %s", why, extra={"event": "forward.import", "refused": why})
    reply = {"message_id": m["message_id"], "allow_sending_without_reply": True}
    await _answer(
        "sendMessage", {"chat_id": m["chat"]["id"], "text": f"Черновик не создан: {why}.", "reply_parameters": reply}
    )
    return "forward_refused"


async def _answer(method: str, params: dict) -> None:
    """The reaction or the reply; the draft does not depend on it, so a failure is only a line in the log."""
    try:
        await tg.call(method, params)
    except Exception as e:  # noqa: BLE001 - the update is handled either way
        log.warning("forward import: %s failed: %s", method, e)
