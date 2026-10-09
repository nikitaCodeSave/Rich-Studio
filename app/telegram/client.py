"""HTTP transport to the Bot API."""

import asyncio
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

import httpx

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
    def __init__(self, description: str, code: int | None = None, raw: Any = None):
        super().__init__(description)
        self.description, self.code, self.raw = description, code, raw


def _token() -> str:
    t = settings.get().bot_token
    if not t:
        raise TelegramError("TG_BOT_TOKEN не задан (см. .env)")
    return t


async def call(method: str, params: dict | None = None, timeout: float = 60) -> Any:  # noqa: ASYNC109
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
            wait = ((e.raw or {}).get("parameters") or {}).get("retry_after")
            if e.code != 429 or not wait or wait > MAX_RETRY_AFTER or attempt == MAX_RETRIES:
                raise
            log.warning("%s: Telegram asks to wait %ss (429), retrying", method, wait)
            await _sleep(wait)
    raise AssertionError("unreachable")


async def _post(url: str, params: dict, files: dict[str, Path], timeout: float) -> Any:  # noqa: ASYNC109
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
    try:
        body = r.json()
    except ValueError as e:
        raise TelegramError(f"HTTP {r.status_code}: {r.text[:300]}", r.status_code) from e
    if not body.get("ok"):
        raise TelegramError(body.get("description", "unknown error"), body.get("error_code"), body)
    return body["result"]
