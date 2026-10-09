"""Idempotency-Key for actions that post to a channel.

Agents retry requests after timeouts. With the same key a retry gets the first result
back instead of a second post in the channel:
- done: the stored result is returned again;
- still running: 409, try later;
- the first attempt hit a network error mid-send ("may have gone"): the same error again,
  never a blind resend; check the channel and use a new key;
- the first attempt failed before anything was sent (the studio or Telegram refused it):
  the key is freed, a retry runs normally;
- any other error, or a claim left "pending" by a crash: treated like "may have gone".
The same key with a different request body is refused.
"""

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

import store
import telegram as tg
from errors import Conflict, MayHaveGone, StudioError

from .publisher import MAY_HAVE_GONE

MAX_KEY = 200
# longer than any send can take (Telegram timeout, 429 waits, uploads): a claim this old was cut off by a crash
STALE_PENDING = 15 * 60


def publisher_may_have_gone() -> str:
    return f"запрос прерван во время отправки. {MAY_HAVE_GONE}"


def fingerprint(scope: str, body: Any) -> str:
    raw = json.dumps([scope, body], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def run(key: str | None, scope: str, body: Any, action: Callable[[], Awaitable[Any]]) -> Any:
    if not key:
        return await action()
    if len(key) > MAX_KEY:
        raise StudioError(f"Idempotency-Key длиннее {MAX_KEY} символов")
    fp = fingerprint(scope, body)
    existing = store.idempotency.claim(key, scope, fp)
    if existing is not None:
        if existing["fingerprint"] != fp:
            raise StudioError("этот Idempotency-Key уже использован для другого запроса", status=422)
        if existing["status"] == "done":
            return existing["response"]
        if existing["status"] == "uncertain":
            raise MayHaveGone(existing["response"])
        if time.time() - existing["created_at"] > STALE_PENDING:
            raise MayHaveGone(publisher_may_have_gone())
        raise Conflict("запрос с этим Idempotency-Key ещё выполняется")
    try:
        result = await action()
    except MayHaveGone as e:
        store.idempotency.finish(key, "uncertain", e.detail)
        raise
    except (StudioError, tg.TelegramError):  # refused before anything was sent
        store.idempotency.release(key)
        raise
    except BaseException:  # anything else (a bug after the send, shutdown mid-send): the post may be out
        store.idempotency.finish(key, "uncertain", publisher_may_have_gone())
        raise
    store.idempotency.finish(key, "done", result)
    return result
