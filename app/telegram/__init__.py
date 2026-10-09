"""Thin pass-through client for the Bot API.

Payloads go to Telegram exactly as written, so any new field or tag works
the day Telegram ships it. The only rewrite is for local uploads: a media
value "upload:<file>" becomes a multipart attachment.

Call through the package (`telegram.call`), so tests can replace it in one place.
"""

from .chat import check_chat
from .client import TelegramError, call

__all__ = ["TelegramError", "call", "check_chat"]
