"""Request headers shared by the routes that post to a channel."""

import re

from fastapi import Header

_CLIENT_RE = re.compile(r"[A-Za-z0-9._:-]{1,40}")


def actor(
    x_studio_client: str | None = Header(
        None, description="Кто отправляет: ui (редактор), mcp:<клиент>, имя скрипта. Пишется в историю постов."
    ),
) -> str:
    v = (x_studio_client or "").strip()
    return v if _CLIENT_RE.fullmatch(v) else "api"


def idempotency_key(
    idempotency_key: str | None = Header(
        None,
        description="Повтор запроса с тем же ключом вернёт первый результат, а не опубликует пост второй раз. "
        "Ключ живёт сутки.",
    ),
) -> str | None:
    return idempotency_key or None
