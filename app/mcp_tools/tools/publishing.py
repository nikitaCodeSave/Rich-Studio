"""Putting something new into a channel: now or later. Every tool here is PUBLIC."""

from typing import Annotated, Any

from pydantic import Field

from mcp_tools.common import (
    PUBLIC,
    ChatArg,
    ChatsArg,
    DraftArg,
    KeyArg,
    OptPostArg,
    ToolDecorator,
    VersionArg,
    actor,
    post_source,
    request_key,
)
from services import idempotency, posts


def register(tool: ToolDecorator) -> None:
    @tool(PUBLIC)
    async def publish_post(
        post: OptPostArg = None,
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        idempotency_key: KeyArg = None,
        dry_run: Annotated[bool, Field(description="Только показать запросы к Telegram, ничего не отправлять")] = False,
        draft_id: DraftArg = None,
        version: VersionArg = None,
    ) -> dict:
        """Опубликовать пост в канал. Только после явного подтверждения пользователя для этого поста и канала
        (назовите канал по названию из list_channels). Пост или draft_id с version; без chat_id пост уходит в каналы
        черновика, а если их нет, в канал по умолчанию. Опубликованный из черновика пост связан с ним."""
        p, _ = post_source(post, draft_id, version, publish=not dry_run)
        if dry_run:
            return await posts.send(p, chat_id, chat_ids, draft_id, dry_run=True)
        body = {"post": p, "chat_id": chat_id, "chat_ids": chat_ids, "draft_id": draft_id, "dry_run": False}
        return await idempotency.run(
            request_key(idempotency_key, "send", body),
            "send",
            body,
            lambda: posts.send(p, chat_id, chat_ids, draft_id, actor=actor()),
        )

    @tool(PUBLIC)
    async def send_poll(
        poll: Annotated[
            dict[str, Any],
            Field(
                description="Параметры sendPoll без chat_id: question, options [{text}], type quiz + correct_option_id, "
                "is_anonymous, allows_multiple_answers, members_only, country_codes…"
            ),
        ],
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        at: Annotated[str | None, Field(description="Отложить: ISO 8601 с часовым поясом")] = None,
        pin: Annotated[bool, Field(description="Закрепить после отправки")] = False,
        idempotency_key: KeyArg = None,
    ) -> dict:
        """Опрос или викторина отдельным сообщением (в rich-пост опрос не вкладывается), сразу или отложенно.
        Только после явного подтверждения пользователя для этого опроса и канала."""
        body = {"poll": poll, "chat_id": chat_id, "chat_ids": chat_ids, "at": at, "pin": pin}
        return await idempotency.run(
            request_key(idempotency_key, "poll", body),
            "poll",
            body,
            lambda: posts.send_poll(poll, chat_id, chat_ids, at, pin, actor=actor()),
        )

    @tool(PUBLIC)
    async def schedule_post(
        at: Annotated[str, Field(description="Когда: ISO 8601 с часовым поясом, например 2026-10-10T09:00:00+03:00")],
        post: OptPostArg = None,
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        idempotency_key: KeyArg = None,
        dry_run: Annotated[bool, Field(description="Только показать запросы и время, ничего не откладывать")] = False,
        draft_id: DraftArg = None,
        version: VersionArg = None,
    ) -> dict:
        """Отложить публикацию. Только после явного подтверждения пользователя. Студия должна работать в это время;
        если она была выключена и пост опоздал больше schedule_late_min, он станет missed и не уйдёт сам."""
        p, _ = post_source(post, draft_id, version, publish=not dry_run)
        if dry_run:
            return posts.schedule(p, at, chat_id, chat_ids, draft_id, dry_run=True)
        body = {"post": p, "chat_id": chat_id, "chat_ids": chat_ids, "draft_id": draft_id, "dry_run": False, "at": at}

        async def action():
            return posts.schedule(p, at, chat_id, chat_ids, draft_id, actor=actor())

        return await idempotency.run(request_key(idempotency_key, "schedule", body), "schedule", body, action)
