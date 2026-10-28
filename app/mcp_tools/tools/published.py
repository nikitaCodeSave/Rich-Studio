"""Posts already in the studio: read them, replace, move or cancel."""

from typing import Annotated

from pydantic import Field

import settings
import store
from errors import NotFound, StudioError
from mcp_tools.common import (
    PUBLIC,
    READ,
    DraftArg,
    OptPostArg,
    PostIdArg,
    ToolDecorator,
    VersionArg,
    actor,
    post_source,
)
from services import channels, listing, posts


def register(tool: ToolDecorator) -> None:
    @tool(PUBLIC)
    async def edit_post(
        post_id: PostIdArg,
        post: OptPostArg = None,
        draft_id: DraftArg = None,
        version: VersionArg = None,
        post_version: Annotated[
            int | None,
            Field(description="version поста из get_post: если его с тех пор заменил кто-то другой, правка не пройдёт"),
        ] = None,
    ) -> dict | None:
        """Заменить содержимое опубликованного или отложенного поста. Только после подтверждения пользователя.
        Сначала прочитайте пост через get_post и передайте его version как post_version. Новое содержимое: post
        или draft_id с version (черновик-правка этого поста из get_draft). Ответы на кнопки и первый комментарий
        остаются, если их не передать; прежняя версия попадёт в post_history."""
        p, _ = post_source(post, draft_id, version, publish=True, edits=post_id)
        return await posts.edit(post_id, p, actor=actor(), post_version=post_version)

    @tool(PUBLIC)
    async def reschedule_post(
        post_id: PostIdArg,
        at: Annotated[str, Field(description="Новое время: ISO 8601 с часовым поясом")],
    ) -> dict | None:
        """Перенести отложенный пост или вернуть пропущенный (missed) в очередь. Только после подтверждения."""
        return posts.reschedule(post_id, at)

    @tool(PUBLIC)
    async def cancel_scheduled_post(post_id: PostIdArg) -> dict | None:
        """Отменить отложенный или пропущенный пост (из канала ничего не удаляется). Только после подтверждения."""
        p = store.get_post(post_id)
        if not p:
            raise NotFound(f"поста {post_id} нет")
        if p["status"] not in ("scheduled", "missed"):
            raise StudioError(f"пост в статусе {p['status']}: отменить можно только отложенный или пропущенный")
        return await posts.delete(post_id)

    @tool(READ)
    async def get_post(post_id: PostIdArg) -> dict:
        """Пост целиком: содержимое (payload), статус, канал, message_id, реакции, нажатия, итоги опроса."""
        p = store.get_post(post_id)
        if not p:
            raise NotFound(f"поста {post_id} нет")
        # the editor opens the post in edit mode: «Обновить пост» replaces it in the channel
        return {
            **p,
            "chat_title": channels.title_of(p["chat_id"]),
            "editor_url": f"{settings.get().public_url}/?post={post_id}",
        }

    @tool(READ)
    async def post_history(post_id: PostIdArg) -> list:
        """Как пост выглядел до каждой замены (новые сверху), кто заменял."""
        return posts.versions(post_id)

    @tool(READ)
    async def list_posts(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        status: Annotated[
            str | None, Field(description="sent | scheduled | failed | missed | cancelled | deleted")
        ] = None,
        chat_id: Annotated[str | None, Field(description="только посты этого канала")] = None,
    ) -> list:
        """Посты студии, новые сверху: первая строка текста (title), ссылка t.me (link), статус и ошибка
        (failed — Telegram отказал, пост не вышел), реакции, нажатия кнопок, итоги опросов, кто отправил."""
        return listing.list_posts(limit, status=status, chat=chat_id)
