"""The channels the studio posts to."""

from mcp_tools.common import READ, TELEGRAM_READ, ChatArg, ToolDecorator
from services import channels, publisher


def register(tool: ToolDecorator) -> None:
    @tool(TELEGRAM_READ)
    async def check_channel(chat_id: ChatArg = None) -> dict:
        """Бот админ канала и может публиковать? Канал запоминается с названием и появляется в list_channels."""
        return await channels.check(publisher.resolve_chat(chat_id))

    @tool(READ)
    async def list_channels() -> list:
        """Каналы студии: chat_id, название (title), форум ли (is_forum) и его темы (topics: id = message_thread_id),
        права бота при последней проверке, is_default. Называйте пользователю канал по названию; тема форума
        задаётся в post.options.message_thread_id."""
        return channels.listing()
