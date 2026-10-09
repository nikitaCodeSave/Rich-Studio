"""Channels as the person knows them: a title instead of a bare id, forum topics, the bot's rights.

Telegram is asked once, when a channel is added or checked again; the editor and agents read the stored answer.
"""

import time

import settings
import store
import telegram as tg
from errors import NotFound, StudioError

from . import publisher

# rights a channel author cares about, in the order the editor lists them
RIGHTS = {
    "can_post_messages": "публикация",
    "can_edit_messages": "правка",
    "can_delete_messages": "удаление",
    "can_pin_messages": "закреп",
}


def _key(chat_id) -> str:
    c = str(chat_id or "").strip()
    if not c:
        raise StudioError("укажите @канал или числовой id")
    return c


async def check(chat_id) -> dict:
    """Ask Telegram about the chat and remember the answer (title, forum, rights). Adds the channel if it is new."""
    key = _key(chat_id)
    info = await tg.check_chat(publisher.resolve_chat(key))
    chat = info["chat"]
    known = store.get_channel(key)
    saved = store.save_channel(
        known["chat_id"] if known else key,
        chat_num=chat["id"],
        title=chat.get("title") or chat.get("username") or str(chat["id"]),
        username=chat.get("username"),
        type=chat.get("type"),
        is_forum=int(bool(chat.get("is_forum"))),
        can_post=int(bool(info["can_post"])),
        rights={"status": info["status"], **info["rights"]},
        checked_at=int(time.time()),
    )
    return {**info, "channel": saved}


def listing() -> list[dict]:
    """Known channels; TG_DEFAULT_CHAT is always in the list, even before anyone checked it."""
    out = store.list_channels()
    default = settings.get().default_chat
    if default and not any(_same(c, default) for c in out):
        out.insert(0, {"chat_id": str(default), "title": None, "topics": None, "is_forum": 0, "checked_at": None})
    for c in out:
        c["is_default"] = bool(default) and _same(c, default)
        c["is_forum"] = bool(c.get("is_forum"))
        c["topics"] = c.get("topics") or []
    return out


def _same(channel: dict, chat_id) -> bool:
    c = str(chat_id)
    return c in (channel["chat_id"], str(channel.get("chat_num")), "@" + str(channel.get("username")))


def set_topics(chat_id, topics: list[dict]) -> dict:
    """Forum topics by hand: the Bot API cannot list them. [{id: message_thread_id, name}]."""
    ch = store.get_channel(_key(chat_id))
    if not ch:
        raise NotFound(f"канала {chat_id} нет в списке: сначала проверьте его")
    clean = []
    for t in topics:
        tid, name = t.get("id"), str(t.get("name") or "").strip()
        if not isinstance(tid, int) or isinstance(tid, bool) or tid < 1:
            raise StudioError(f"id темы — номер темы (message_thread_id), а не {tid!r}")
        clean.append({"id": tid, "name": name or f"Тема {tid}"})
    return store.save_channel(ch["chat_id"], topics=clean)


def remember_topic(chat_num, thread_id, name: str) -> None:
    """A topic seen in an update (created or renamed in a known forum) joins the channel's list by itself."""
    ch = store.get_channel(chat_num)
    if not ch or not thread_id or not name:
        return
    topics = [t for t in ch.get("topics") or [] if t.get("id") != thread_id]
    store.save_channel(ch["chat_id"], topics=[*topics, {"id": thread_id, "name": name}])


def remove(chat_id) -> None:
    ch = store.get_channel(_key(chat_id))
    if ch:
        store.delete_channel(ch["chat_id"])


def title_of(chat_id) -> str | None:
    ch = store.get_channel(chat_id) if chat_id not in (None, "") else None
    return ch.get("title") if ch else None
