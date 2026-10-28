"""Whether the studio works: settings in effect, health checks, the log."""

from typing import Annotated

from pydantic import Field

import logs
import settings
from mcp_tools.common import READ, ToolDecorator
from services import health, updates


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def studio_status() -> dict:
        """Что сейчас работает: токен, канал по умолчанию, приём событий (без него не будет ответов на кнопки,
        первых комментариев и реакций), предел опоздания отложенных постов, можно ли агентам публиковать."""
        cfg = settings.get()
        return {
            "token_set": bool(cfg.bot_token),
            "default_chat": cfg.default_chat or None,
            "preview_chat_set": bool(cfg.preview_chat),
            "updates": updates.STATUS.as_dict(),
            "schedule_late_min": cfg.schedule_late_min,
            "mcp_publish": cfg.mcp_publish,
            "note": "отложенные посты отправляет сама студия: в это время она должна работать",
        }

    @tool(READ)
    async def studio_health() -> dict:
        """Состояние студии целиком: проверки (база, диск, Telegram, фоновые циклы, очередь, сбойные и неясные
        отправки, спецификация, бэкап, место, ошибки в журнале) со статусом ok/warn/fail и подсказкой action,
        счётчики вызовов и последние предупреждения. Ничего не меняет."""
        return health.report()

    @tool(READ)
    async def studio_logs(
        level: Annotated[str, Field(description="DEBUG | INFO | WARNING | ERROR | CRITICAL: этот и выше")] = "WARNING",
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        since: Annotated[
            float | None, Field(ge=0, le=4102444800, description="unix-время: только строки не старше")
        ] = None,
        logger: Annotated[str, Field(description="начало имени: rich-studio.telegram, rich-studio.http…")] = "",
        text: Annotated[str, Field(description="подстрока в строке")] = "",
    ) -> list:
        """Последние строки журнала студии (JSON: ts, level, logger, msg, event и поля события), новые в конце.
        Токена и текста постов в журнале нет."""
        return logs.read(level, limit, since, logger, text)
