"""Named invite links to a channel: where the subscribers come from."""

from typing import Annotated

from pydantic import Field

from mcp_tools.common import PUBLIC, READ, ChatArg, ToolDecorator, actor
from services import invite_links

LinkArg = Annotated[str, Field(description="Ссылка из list_invite_links: https://t.me/+…")]
NameArg = Annotated[str | None, Field(description="Название до 32 символов: площадка, где ссылку разместят")]
ExpireArg = Annotated[str | None, Field(description="Когда ссылка перестанет работать: ISO 8601 с часовым поясом")]
LimitArg = Annotated[int | None, Field(description="Сколько человек смогут вступить (1–99999)")]
RequestArg = Annotated[bool, Field(description="Вступление по заявке, которую принимает админ (без member_limit)")]


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def list_invite_links(chat_id: ChatArg = None) -> list:
        """Пригласительные ссылки, созданные студией, без chat_id — всех каналов: joined (вступили), requests
        (заявки), kind (invite или subscription). Счётчики студия ведёт по событиям Telegram, пока запущена; это
        вступления, а не люди: кто вышел и вступил снова, посчитан дважды."""
        return invite_links.listing(chat_id)

    @tool(PUBLIC)
    async def create_invite_link(
        chat_id: ChatArg = None,
        name: NameArg = None,
        expire_date: ExpireArg = None,
        member_limit: LimitArg = None,
        creates_join_request: RequestArg = False,
        subscription_price: Annotated[
            int | None, Field(description="Платная подписка: звёзд в месяц (1–10000); тогда без срока и предела")
        ] = None,
    ) -> dict:
        """Создать именную пригласительную ссылку в канал (своя на каждую площадку: видно, откуда пришли) или
        платную подписку за звёзды. Только после подтверждения пользователя. Боту нужно право can_invite_users."""
        return await invite_links.create(
            chat_id, name, expire_date, member_limit, creates_join_request, subscription_price, actor=actor()
        )

    @tool(PUBLIC)
    async def edit_invite_link(
        invite_link: LinkArg,
        name: NameArg = None,
        expire_date: ExpireArg = None,
        member_limit: LimitArg = None,
        creates_join_request: Annotated[
            bool | None, Field(description="Заявки на вступление: true включить, false снять, не передано — как было")
        ] = None,
    ) -> dict:
        """Изменить ссылку, созданную студией. Только после подтверждения пользователя. Непереданное остаётся как
        было; снять: name "", expire_date "", member_limit 0, creates_join_request false. У подписки меняется только
        название."""
        return await invite_links.edit(invite_link, name, expire_date, member_limit, creates_join_request)

    @tool(PUBLIC)
    async def revoke_invite_link(invite_link: LinkArg) -> dict:
        """Отозвать ссылку: по ней больше нельзя вступить, счётчики остаются. Только после подтверждения пользователя."""
        return await invite_links.revoke(invite_link)
