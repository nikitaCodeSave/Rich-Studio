"""Request headers shared by the routes that post to a channel, and the level a route needs."""

import re

from fastapi import Header

import security
from errors import StudioError

_CLIENT_RE = re.compile(r"[A-Za-z0-9._:-]{1,40}")


def actor(
    x_studio_client: str | None = Header(
        None, description="Кто отправляет: ui (редактор), mcp:<клиент>, имя скрипта. Пишется в историю постов."
    ),
) -> str:
    """The token's name is the record of who did it; the header only says which client (ui@laptop, mcp:claude@ci)."""
    v = (x_studio_client or "").strip()
    client = v if _CLIENT_RE.fullmatch(v) else ""
    name = security.current().name
    if name:
        return f"{client}@{name}" if client else name
    return client or "api"


def need(level: str):
    """Dependency for routes that need more than guard checks by method: publish, admin."""

    def check() -> None:
        who = security.current()
        if not who.can(level):
            raise StudioError(f"у токена {who.name} нет права {level}", status=403)

    return check


def idempotency_key(
    idempotency_key: str | None = Header(
        None,
        description="Повтор запроса с тем же ключом вернёт первый результат, а не опубликует пост второй раз. "
        "Ключ живёт сутки.",
    ),
) -> str | None:
    return idempotency_key or None
