"""Posts subscribers suggest in the channel's direct messages."""

from typing import Annotated

from pydantic import Field

from mcp_tools.common import PUBLIC, READ, KeyArg, ToolDecorator, actor, request_key
from services import idempotency, suggested_posts

SuggestionArg = Annotated[str, Field(description="id предложения из list_suggested_posts")]


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def list_suggested_posts(
        state: Annotated[
            str | None,
            Field(
                description="open (ждёт решения, решается или одобрение не прошло), pending, approving, declining, "
                "approved, declined, paid, refunded, failed"
            ),
        ] = "open",
    ) -> list:
        """Посты, которые подписчики предложили в личных сообщениях канала: text, media, author, price_amount и
        price_currency (XTR — звёзды), send_date (время, которое выбрал автор), state. Покажите их пользователю
        и спросите, что принять."""
        return suggested_posts.listing(state)

    @tool(PUBLIC)
    async def approve_suggested_post(
        suggestion_id: SuggestionArg,
        send_date: Annotated[
            str | None, Field(description="Когда опубликовать (ISO 8601), если автор не выбрал; пусто — сразу")
        ] = None,
        idempotency_key: KeyArg = None,
    ) -> dict:
        """Принять предложенный пост: Telegram опубликует его в канале, платный спишет звёзды с автора. Только после
        явного подтверждения пользователя для этого предложения."""
        body = {"sid": suggestion_id, "send_date": send_date}
        # after «Решить снова» the same request is a new decision, not a retry of the one without an answer
        auto = {**body, "round": suggested_posts.decision_round(suggestion_id)}
        return await idempotency.run(
            request_key(idempotency_key, "suggested-approve", auto),
            "suggested-approve",
            body,
            lambda: suggested_posts.approve(suggestion_id, send_date, actor=actor()),
        )

    @tool(PUBLIC)
    async def reopen_suggested_post(suggestion_id: SuggestionArg) -> dict:
        """Решить снова: предложение, решение по которому осталось без ответа Telegram (approving/declining),
        возвращается в ожидание. Только после того, как пользователь проверил в личных сообщениях канала, что
        решение не дошло, и сам попросил решить снова. Затем approve_suggested_post или decline_suggested_post."""
        return suggested_posts.reopen(suggestion_id, actor=actor())

    @tool(PUBLIC)
    async def decline_suggested_post(
        suggestion_id: SuggestionArg,
        comment: Annotated[str | None, Field(description="Комментарий автору, до 128 символов")] = None,
    ) -> dict:
        """Отклонить предложенный пост, можно с комментарием автору. Только после подтверждения пользователя."""
        return await suggested_posts.decline(suggestion_id, comment, actor=actor())
