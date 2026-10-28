"""Any Bot API method as is, for features the studio does not have yet."""

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, Request

import settings
import telegram as tg
from errors import StudioError

from .deps import need

router = APIRouter(tags=["telegram"])

# Methods that only read, send or edit messages run as is. Everything else (delete*, set*, ban*,
# logOut, and any method Telegram adds later) needs X-Confirm: yes: an allowlist cannot miss a new
# dangerous method the way a denylist can.
SAFE_PREFIXES = ("get", "send", "edit", "answer", "copy", "forward")
SAFE_METHODS = {"stopPoll", "pinChatMessage", "unpinChatMessage", "setMessageReaction"}
# reads, but would take updates away from the studio's own getUpdates loop
CONFIRM_ANYWAY = {"getUpdates"}


def needs_confirmation(method: str) -> bool:
    if method in CONFIRM_ANYWAY:
        return True
    return not (method in SAFE_METHODS or method.startswith(SAFE_PREFIXES))


@router.post(
    "/api/raw/{method}",
    summary="Любой метод Bot API как есть (для функций, которых ещё нет в студии)",
    dependencies=[Depends(need("admin"))],
)
async def raw(method: str, request: Request, params: dict[str, Any] = Body(default_factory=dict)):
    """Только для уровня admin; `RAW_API=off` выключает маршрут совсем."""
    if not settings.get().raw_api:
        raise StudioError("/api/raw выключен (RAW_API=off)", status=403)
    if not re.fullmatch(r"[A-Za-z]+", method):
        raise StudioError("bad method")
    if needs_confirmation(method) and request.headers.get("x-confirm") != "yes":
        raise StudioError(
            f"{method} не только читает и отправляет сообщения: он может что-то удалить или поменять настройки "
            "бота или канала; повторите с заголовком X-Confirm: yes"
        )
    return {"result": await tg.call(method, params)}
