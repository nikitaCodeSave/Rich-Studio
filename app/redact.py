"""Secrets out of every text the studio shows or keeps: the log, API answers, saved errors, MCP errors.

What is masked: the bot token (the value of TG_BOT_TOKEN, also URL-encoded, and anything shaped like a bot token,
even cut short after /bot), STUDIO_API_KEY (also URL-encoded) and named tokens rs_… (cli.py). The text around them
stays as it was: Telegram's "Bad Request: …" is what the person needs to fix the post.

A module without layers, like settings: telegram/ masks an answer before it becomes an error, store/ masks what it
saves, api/ and mcp_tools/ mask what they answer, logs masks every line.
"""

import functools
import re
from typing import Any

import settings

# a configured secret shorter than this is not looked for: a local STUDIO_API_KEY like "1" or "test" would turn
# every text into masks, and a key that short protects nothing anyway (on a server it is at least 32 characters)
MIN_LENGTH = 8
_COLON = r"(?::|%3[Aa])"  # a token in a URL may come back URL-encoded
# a Bot API token anywhere in the text, not only in URLs; the lookbehind and the bound on digits keep a long run
# of digits linear (an unauthenticated path of 15000 digits used to block the event loop for seconds)
_TOKEN = re.compile(rf"(?<!\d)\d{{5,12}}{_COLON}[\w-]{{30,}}")
_TOKEN_IN_URL = re.compile(rf"(?<=/bot)\d{{5,12}}{_COLON}[\w-]+")  # also a token cut short by a truncated text
# rs_ + token_urlsafe(32), alone or right after an escape like %20 or %3D; not rs_session, not users_…
_NAMED = re.compile(r"(?:(?<![\w-])|(?<=%[0-9A-Fa-f]{2}))rs_[\w-]{20,}")
_UNRESERVED = re.compile(r"[A-Za-z0-9._~-]")  # what URL encoding leaves as it is


def _pattern(secret: str) -> str:
    """The secret as a regex that also finds it URL-encoded, wholly or in part (%3A or %3a for a colon)."""
    parts = []
    for ch in secret:
        if _UNRESERVED.fullmatch(ch):
            parts.append(re.escape(ch))
        else:
            encoded = "".join(f"%{b:02X}" for b in ch.encode())
            parts.append(f"(?:{re.escape(ch)}|(?i:{re.escape(encoded)}))")
    return "".join(parts)


@functools.lru_cache(maxsize=16)
def _configured(token: str, key: str) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Patterns for the secrets in the settings; cached by their values, so a changed setting is seen at once."""
    out = []
    for value, mask in ((token, "<token>"), (key, "<api-key>")):
        if len(value) >= MIN_LENGTH:
            out.append((re.compile(_pattern(value)), mask))
    return tuple(out)


def scrub(text: str) -> str:
    """The text with every secret replaced by <token>, <api-key> or <rs-token>; nothing else changes."""
    if not text:
        return text
    cfg = settings.get()
    for pattern, mask in _configured(cfg.bot_token, cfg.api_key):
        text = pattern.sub(mask, text)
    text = _TOKEN_IN_URL.sub("<token>", _TOKEN.sub("<token>", text))
    return _NAMED.sub("<rs-token>", text)


def scrub_all(value: Any) -> Any:
    """scrub() every string inside a JSON-like value (a Telegram answer, an error detail, the health report)."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: scrub_all(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub_all(v) for v in value]
    return value
