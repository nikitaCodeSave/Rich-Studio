"""Media messages that are not rich posts: a Live Photo, photos and videos for stars."""

from typing import Annotated, Any, Literal

from pydantic import Field

from mcp_tools.common import PUBLIC, ChatArg, ChatsArg, KeyArg, ToolDecorator, actor, request_key
from services import idempotency, posts


def register(tool: ToolDecorator) -> None:
    @tool(PUBLIC)
    async def send_media_post(
        kind: Annotated[
            Literal["live_photo", "paid_media"],
            Field(description="live_photo — sendLivePhoto; paid_media — sendPaidMedia (фото и видео за звёзды)"),
        ],
        params: Annotated[
            dict[str, Any],
            Field(
                description="Параметры метода без chat_id. live_photo: {live_photo: 'upload:clip.mp4', photo: "
                "'upload:still.jpg', caption?}; paid_media: {star_count, media: [{type: photo|video|live_photo, "
                "media: 'upload:…', photo? (кадр live_photo)}], caption?}"
            ),
        ],
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        at: Annotated[str | None, Field(description="Отложить: ISO 8601 с часовым поясом")] = None,
        pin: Annotated[bool, Field(description="Закрепить после отправки")] = False,
        idempotency_key: KeyArg = None,
    ) -> dict:
        """Live Photo или платный контент отдельным сообщением: в rich-пост такие медиа не вкладываются. Файлы
        сначала загрузите (upload_media). Только после явного подтверждения пользователя для этого сообщения,
        цены и канала. Платный контент — только там, где разрешены платные медиа (каналы)."""
        body = {"kind": kind, "params": params, "chat_id": chat_id, "chat_ids": chat_ids, "at": at, "pin": pin}
        return await idempotency.run(
            request_key(idempotency_key, "media-post", body),
            "media-post",
            body,
            lambda: posts.send_media(kind, params, chat_id, chat_ids, at, pin, actor=actor()),
        )
