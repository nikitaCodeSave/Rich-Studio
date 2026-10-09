"""The list of posts as a person reads it: the first line of the text, a thumbnail, a link to the message."""

import html
import re

import store

from . import channels

TITLE_LEN = 120
# a closing block tag or a line break ends a line of the post
_LINE_END = re.compile(
    r"<(?:br|hr|/p|/h[1-6]|/li|/blockquote|/figcaption|/summary|/caption|/aside|/pre|/footer)\b[^>]*>", re.I
)
_TAG = re.compile(r"<[^>]+>")
_MD_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|\|)")
_MD_MARKS = re.compile(r"\*\*|__|~~|\|\||==|`|\$\$?|!\[[^\]]*\]\([^)]*\)|\[([^\]]*)\]\([^)]*\)")
_TEXT_KEYS = ("text", "summary", "caption", "expression")


def _plain(rt) -> str:
    """RichText (a string, an array or {type, text}) as plain text."""
    if isinstance(rt, str):
        return rt
    if isinstance(rt, list):
        return "".join(_plain(x) for x in rt)
    if isinstance(rt, dict):
        return _plain(rt.get("text"))
    return ""


def _first_block_text(blocks) -> str:
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        for key in _TEXT_KEYS:
            v = b.get(key)
            text = _plain(v.get("text") if isinstance(v, dict) and "type" not in v else v).strip()
            if text:
                return text
        for nested in [b.get("blocks"), *[i.get("blocks") for i in b.get("items") or [] if isinstance(i, dict)]]:
            if text := _first_block_text(nested):
                return text
        for r in b.get("cells") or []:
            for c in r if isinstance(r, list) else []:
                if isinstance(c, dict) and (text := _plain(c.get("text")).strip()):
                    return text
    return ""


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def title(payload: dict) -> str:
    """The first line a reader sees, for the list of posts; empty when the post has no text."""
    if not isinstance(payload, dict):
        return ""
    if isinstance(payload.get("poll"), dict):
        return str(payload["poll"].get("question") or "")
    rm = payload.get("rich_message") or {}
    if not isinstance(rm, dict):
        return ""
    if rm.get("blocks"):
        text = _first_line(_first_block_text(rm["blocks"]))
    elif isinstance(rm.get("html"), str):
        text = _first_line(html.unescape(_TAG.sub("", _LINE_END.sub("\n", rm["html"]))))
    elif isinstance(rm.get("markdown"), str):
        lines = (_MD_MARKS.sub(r"\1", _MD_PREFIX.sub("", line)).strip(" |") for line in rm["markdown"].splitlines())
        text = next((x for x in lines if x and not re.fullmatch(r"[-=*_:| ]+", x)), "")
    else:
        text = ""
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= TITLE_LEN else text[: TITLE_LEN - 1].rstrip() + "…"


def thumb(payload: dict) -> str | None:
    """The first photo uploaded through the studio, as a URL the editor can show; None otherwise."""
    stack = [payload.get("rich_message") if isinstance(payload, dict) else None]
    while stack:
        x = stack.pop(0)
        if isinstance(x, dict):
            media = x.get("media")
            if x.get("type") == "photo" and isinstance(media, str) and media.startswith("upload:"):
                return "/media/" + media[len("upload:") :]
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return None


def link(chat_id, message_id) -> str | None:
    """t.me link to the message: by @username, or t.me/c/... for a numeric -100 id (members only)."""
    if not message_id:
        return None
    chat = str(chat_id or "")
    if chat.startswith("@"):
        return f"https://t.me/{chat[1:]}/{message_id}"
    if chat.startswith("-100") and chat[4:].isdigit():
        return f"https://t.me/c/{chat[4:]}/{message_id}"
    return None


def list_posts(limit: int = 100, status: str | None = None, chat: str | None = None) -> list[dict]:
    out = []
    titles: dict[str, str | None] = {}
    for p in store.list_posts(limit, status=status, chat=chat):
        c = str(p["chat_id"])
        if c not in titles:
            titles[c] = channels.title_of(c)
        p["chat_title"] = titles[c]
        payload = p.pop("payload", None) or {}
        p["title"] = title(payload)
        p["thumb"] = thumb(payload)
        p["link"] = link(p["chat_id"], p["message_id"])
        p["pinned"] = bool(p.get("pinned"))
        out.append(p)
    return out
