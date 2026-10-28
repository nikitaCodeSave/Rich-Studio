"""The Bot API spec the studio works by: version, what changed, exact fields of a type."""

from typing import Annotated

from pydantic import Field

import spec
from errors import StudioError
from mcp_tools.common import FETCH, READ, ToolDecorator


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def bot_api_status() -> dict:
        """Версия Bot API, на которой работает студия, и последние изменения rich-типов и методов."""
        s = spec.current
        return {
            "version": s.version,
            "release_date": s.data.get("release_date"),
            "source": s.source,
            "changes": s.changes[:3],
            "gaps": s.summary()["gaps"],
            "last_check": s.last_check,
        }

    @tool(FETCH)
    async def refresh_bot_api() -> dict:
        """Скачать свежую спецификацию Bot API прямо сейчас (обычно студия делает это сама при запуске и раз в
        12 часов). Возвращает версию, изменения rich-типов и чего не хватает подписям и предпросмотру (gaps).
        При ошибке скачивания студия продолжает работать на прежней версии, ошибка в поле error. На сервере
        спецификация закреплена (`pinned`): новая версия приходит только с обновлением образа."""
        return await spec.current.refresh(force=True)

    @tool(READ)
    async def describe_type(
        name: Annotated[str, Field(description="Тип или метод, например InputRichBlockTable")],
    ) -> dict:
        """Точные поля типа или метода Bot API из текущей спецификации."""
        t = spec.current.types.get(name) or spec.current.methods.get(name)
        if not t:
            raise StudioError(f"{name} нет в спецификации {spec.current.version}")
        return t
