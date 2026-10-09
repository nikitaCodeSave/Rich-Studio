"""HTTP transport to the Bot API."""

import asyncio
import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

import metrics
import settings
from errors import StudioError

from .uploads import MissingUpload, collect_uploads

log = logging.getLogger("rich-studio.telegram")
# 429 means Telegram did not execute the call, so waiting and repeating is safe even for send*.
# Longer waits are reported instead: the caller (a person or the scheduler) decides.
MAX_RETRY_AFTER = 30
MAX_RETRIES = 3
_sleep = asyncio.sleep  # tests replace this, not asyncio.sleep itself
_TOKEN_RE = re.compile(r"/bot\d+:[\w-]+/")
QUIET = {"getUpdates"}  # long polling every 25 s: only its failures are worth a log line


class _HideToken(logging.Filter):
    """The token is part of every Bot API URL; keep it out of logs (httpx logs URLs at INFO)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _TOKEN_RE.sub("/bot<token>/", str(record.msg))
        if record.args:
            record.args = tuple(
                _TOKEN_RE.sub("/bot<token>/", str(a)) if _TOKEN_RE.search(str(a)) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_HideToken())


class TelegramError(Exception):
    """Telegram refused the call, or it never reached Telegram: nothing was executed."""

    def __init__(self, description: str, code: int | None = None, raw: Any = None):
        super().__init__(description)
        self.description, self.code, self.raw = description, code, raw

    @property
    def retry_after(self) -> int | None:
        return ((self.raw or {}).get("parameters") or {}).get("retry_after") if isinstance(self.raw, dict) else None


class Unreachable(TelegramError):
    """No connection to Telegram was made (DNS, refused, connect timeout, proxy): the request was not sent."""


class NoAnswer(Exception):
    """Telegram's gateway answered with an error page or a 5xx: the call may still have been executed.

    Deliberately not a TelegramError: for a send this means "the post may have gone", never "safe to retry".
    """


def _token() -> str:
    t = settings.get().bot_token
    if not t:
        raise TelegramError("TG_BOT_TOKEN не задан (см. .env)")
    return t


def _loggable_chat(chat: Any) -> int | str | None:
    """chat_id for the log: an id or @name, never whatever else a raw call put there (it could be post text)."""
    if isinstance(chat, int) or (isinstance(chat, str) and len(chat) <= 64):
        return chat
    return None if chat is None else "?"


async def call(method: str, params: dict | None = None, timeout: float = 60) -> Any:  # noqa: ASYNC109
    """One Bot API call; every outcome is counted for the health report and logged without the URL."""
    started = time.monotonic()
    chat = _loggable_chat((params or {}).get("chat_id"))
    try:
        result = await _call(method, params, timeout)
    except (TelegramError, NoAnswer) as e:
        ms = int((time.monotonic() - started) * 1000)
        text = e.description if isinstance(e, TelegramError) else str(e)
        code = e.code if isinstance(e, TelegramError) else None
        metrics.telegram_call(method, False, text, None if method in QUIET else ms, code)
        log.warning(
            "%s failed: %s",
            method,
            text,
            extra={"event": "telegram.error", "method": method, "ms": ms, "code": code, "chat_id": chat},
        )
        raise
    ms = int((time.monotonic() - started) * 1000)
    if method in QUIET:  # a long poll lasts as long as Telegram has nothing to say: not a latency
        metrics.telegram_call(method, True)
    else:
        log.info("%s ok", method, extra={"event": "telegram.call", "method": method, "ms": ms, "chat_id": chat})
        metrics.telegram_call(method, True, ms=ms)
    return result


async def _call(method: str, params: dict | None, timeout: float) -> Any:  # noqa: ASYNC109
    cfg = settings.get()
    files: dict[str, Path] = {}
    try:
        params = collect_uploads(params or {}, files, cfg.media_dir)
    except MissingUpload as e:  # nothing was sent: the request itself is wrong
        raise StudioError(str(e)) from e
    url = f"{cfg.api_base}/bot{_token()}/{method}"
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await _post(url, params, files, timeout)
        except TelegramError as e:
            wait = e.retry_after
            if e.code != 429 or not wait or wait > MAX_RETRY_AFTER or attempt == MAX_RETRIES:
                raise
            log.warning("%s: Telegram asks to wait %ss (429), retrying", method, wait)
            await _sleep(wait)
    raise AssertionError("unreachable")


# errors raised before a single byte of the request reached Telegram
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError, httpx.UnsupportedProtocol)


async def _post(url: str, params: dict, files: dict[str, Path], timeout: float) -> Any:  # noqa: ASYNC109
    try:
        r = await _request(url, params, files, timeout)
    except _NOT_SENT as e:
        raise Unreachable(f"нет связи с Telegram ({type(e).__name__}): запрос не отправлен") from e
    try:
        body = r.json()
    except ValueError:
        body = None
    # a proxy's or a wrong TG_API_BASE's error page may echo the URL: hide the token before cutting the text
    detail = body.get("description") if isinstance(body, dict) else r.text.replace(_token(), "<token>")[:300]
    if r.status_code >= 500 or (isinstance(body, dict) and (body.get("error_code") or 0) >= 500):
        raise NoAnswer(f"Telegram ответил HTTP {r.status_code}: {detail}")
    if not isinstance(body, dict):  # a 4xx page from a proxy or a wrong TG_API_BASE: refused, not executed
        raise TelegramError(f"HTTP {r.status_code}: {detail}", r.status_code)
    if not body.get("ok"):
        raise TelegramError(body.get("description", "unknown error"), body.get("error_code"), body)
    return body["result"]


async def _request(url: str, params: dict, files: dict[str, Path], timeout: float) -> httpx.Response:  # noqa: ASYNC109
    async with httpx.AsyncClient(timeout=timeout) as client:
        if files:
            data = {
                k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                for k, v in params.items()
                if v is not None
            }
            handles = {}
            try:
                for k, p in files.items():
                    handles[k] = (p.name, p.open("rb"), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
                r = await client.post(url, data=data, files=handles)
            finally:
                for h in handles.values():
                    h[1].close()
        else:
            r = await client.post(url, json={k: v for k, v in params.items() if v is not None})
    return r
