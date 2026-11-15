"""Idempotency-Key for actions that post to a channel.

Agents retry requests after timeouts. With the same key a retry gets the first result
back instead of a second post in the channel:
- done: the stored result is returned again;
- still running: 409, try later;
- the first attempt hit a network error mid-send ("may have gone"): the same error again, with what to do
  instead, never a blind resend, however late the retry; check the channel and use a new key;
- the first attempt failed before anything was sent (the studio or Telegram refused it):
  the key is freed, a retry runs normally;
- the post went out and the studio could not write it down (Unrecorded): that same answer again;
- any other error, or a claim left "pending" by a crash: treated like "may have gone".
The same key with a different request body is refused. An outcome the database refuses to take (a full or read-only
disk) never replaces the answer: it is kept in memory (`unwritten`), answers retries from there and is written later.
"""

import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import store
import telegram as tg
from errors import Conflict, MayHaveGone, StudioError, Unrecorded

from . import publisher
from .publisher import MAY_HAVE_GONE

log = logging.getLogger("rich-studio.idempotency")
MAX_KEY = 200
# longer than any send can take (Telegram timeout, 429 waits, uploads): a claim this old was cut off by a crash
STALE_PENDING = 15 * 60
# outcomes the database refused: key → (fingerprint, status, response); a retry with the key is answered from here
# while the process runs, and every keyed request first tries to write them down. Lost with the process: the key
# stays "pending" in the table, and a retry then gets 409 and, after STALE_PENDING, "may have gone"
unwritten: dict[str, tuple[str, str, Any]] = {}
KEY_NOT_SAVED = (
    "ответ не сохранён для повтора с этим Idempotency-Key ({}): отправка прошла, повторять запрос не нужно; "
    "повтор с тем же ключом получит этот ответ, пока студию не перезапустят"
)


# a retry with the key of a send that may have gone never goes out (the key does not expire): say what to do instead.
# The agent cannot see the channel, so the person checks it.
NEW_KEY = "с новым ключом (заголовок Idempotency-Key, в MCP параметр idempotency_key)"
BLOCKED = {
    "send": f"проверьте канал вместе с пользователем; если сообщение туда не дошло, отправьте заново {NEW_KEY}",
    "schedule": "проверьте вместе с пользователем отложенные посты (вкладка «Посты», list_posts); если этого поста "
    f"там нет, отложите заново {NEW_KEY}",
    # a new key does not help either: the suggestion stays "approving" until Telegram's outcome or a person's reopen
    "suggested-approve": "новый ключ тоже не поможет, пока решение ждёт ответа Telegram: пусть пользователь проверит "
    "в личных сообщениях канала, дошло ли оно; если нет, он сам нажимает «Решить снова» в редакторе (агенту это "
    f"недоступно), и после этого решение можно отправить {NEW_KEY}",
}


def publisher_may_have_gone() -> str:
    return f"запрос прерван во время отправки. {MAY_HAVE_GONE}"


def _blocked(scope: str, detail: Any) -> MayHaveGone:
    if isinstance(detail, dict) and detail.get("unrecorded"):
        # known to be in the channel: the first answer again, its headline as things are now (written, lost)
        return Unrecorded({**detail, "message": publisher.unrecorded_message(detail["unrecorded"])})
    hint = f"Повтор с этим ключом больше ничего не отправит: {BLOCKED.get(scope, BLOCKED['send'])}"
    if isinstance(detail, dict):
        return MayHaveGone({**detail, "message": f"{detail.get('message', '')}. {hint}"})
    return MayHaveGone(f"{detail}. {hint}")


def fingerprint(scope: str, body: Any) -> str:
    raw = json.dumps([scope, body], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


async def run(key: str | None, scope: str, body: Any, action: Callable[[], Awaitable[Any]]) -> Any:
    write_unwritten()  # any request that posts is a chance to write down what the database refused before
    if not key:
        return await action()
    if len(key) > MAX_KEY:
        raise StudioError(f"Idempotency-Key длиннее {MAX_KEY} символов")
    fp = fingerprint(scope, body)
    existing = _remembered(key) or store.idempotency.claim(key, scope, fp)
    if existing is not None:
        if existing["fingerprint"] != fp:
            raise StudioError("этот Idempotency-Key уже использован для другого запроса", status=422)
        if existing["status"] == "done":
            return existing["response"]
        if existing["status"] == "uncertain":
            raise _blocked(scope, existing["response"])
        if time.time() - existing["created_at"] > STALE_PENDING:
            raise _blocked(scope, publisher_may_have_gone())
        raise Conflict("запрос с этим Idempotency-Key ещё выполняется")
    try:
        result = await action()
    except MayHaveGone as e:
        _finish(key, fp, "uncertain", e.detail)
        raise
    except (StudioError, tg.TelegramError):  # refused before anything was sent
        try:
            store.idempotency.release(key)
        except Exception:  # left "pending": a retry gets 409, later "may have gone"; never another error instead
            log.exception("could not free the idempotency key")
        raise
    except BaseException:  # anything else (a bug after the send, shutdown mid-send): the post may be out
        _finish(key, fp, "uncertain", publisher_may_have_gone())
        raise
    failed = _finish(key, fp, "done", result)
    if failed and isinstance(result, dict) and isinstance(result.get("warnings"), list):
        return {**result, "warnings": [*result["warnings"], KEY_NOT_SAVED.format(failed)]}
    return result


def _finish(key: str, fp: str, status: str, response: Any) -> str | None:
    """Store the outcome; if the database refuses, keep it in memory. Returns why it was not written, or None."""
    try:
        store.idempotency.finish(key, status, response)
    except Exception as e:  # the answer the caller gets must stay the action's own, not a database error
        log.exception("could not store the %s outcome of an idempotency key", status)
        unwritten[key] = (fp, status, response)
        return f"{type(e).__name__}: {e}"
    return None


def _remembered(key: str) -> dict | None:
    """The outcome of the key the database refused, shaped like its row."""
    if key not in unwritten:
        return None
    fp, status, response = unwritten[key]
    return {"fingerprint": fp, "status": status, "response": response, "created_at": time.time()}


def write_unwritten() -> int:
    """Write down the outcomes the database refused earlier (`unwritten`). Returns how many still wait."""
    for key, (_, status, response) in list(unwritten.items()):
        try:
            store.idempotency.finish(key, status, response)
        except Exception:  # noqa: BLE001 - the database still refuses: the next keyed request tries again
            break
        del unwritten[key]
    return len(unwritten)
