"""Questions about a chat that need several Bot API calls."""

import telegram


async def check_chat(chat_id) -> dict:
    """Is the bot an admin of the chat, and with which posting rights."""
    me = await telegram.call("getMe")
    chat = await telegram.call("getChat", {"chat_id": chat_id})
    member = await telegram.call("getChatMember", {"chat_id": chat_id, "user_id": me["id"]})
    rights = {k: v for k, v in member.items() if k.startswith("can_")}
    return {
        "bot": {"id": me["id"], "username": me.get("username")},
        "chat": {
            "id": chat["id"],
            "title": chat.get("title"),
            "type": chat.get("type"),
            "username": chat.get("username"),
            "is_forum": bool(chat.get("is_forum")),
        },
        "status": member.get("status"),
        "rights": rights,
        "can_post": member.get("status") == "creator"
        or bool(member.get("can_post_messages"))
        or chat.get("type") in ("group", "supergroup", "private"),
    }
