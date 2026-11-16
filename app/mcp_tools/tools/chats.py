"""The channels the studio posts to."""

from mcp_tools.common import READ, TELEGRAM_CHECK, ChatArg, ToolDecorator
from services import channels, publisher


def register(tool: ToolDecorator) -> None:
    @tool(TELEGRAM_CHECK)
    async def check_channel(chat_id: ChatArg = None) -> dict:
        """Бот админ канала и может публиковать? Канал запоминается с названием и появляется в list_channels."""
        return await channels.check(publisher.resolve_chat(chat_id))

    @tool(READ)
    async def list_channels() -> list:
        """Каналы студии: chat_id, title, is_forum, topics, права бота при последней проверке, is_default.
        Пользователю называйте канал по title; тема форума — post.options.message_thread_id (id из topics)."""
        return channels.listing()
