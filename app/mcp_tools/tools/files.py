"""Pictures, videos and files for posts (upload:<name>)."""

from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from mcp_tools.common import LOCAL, READ, ToolDecorator
from services import media


def register(tool: ToolDecorator) -> None:
    @tool(LOCAL)
    async def upload_media(
        url: Annotated[str | None, Field(description="Ссылка http(s) на файл: студия скачает его сама")] = None,
        data_base64: Annotated[str | None, Field(description="Или содержимое файла в base64 (с filename)")] = None,
        filename: Annotated[str | None, Field(description="Имя файла с расширением: по нему выбирается тип")] = None,
    ) -> dict:
        """Загрузить картинку, видео или файл для поста. Вернёт ref (upload:<имя>): его ставят в media блока
        (например {"type": "photo", "photo": {"type": "photo", "media": ref}}) или в rich_message.media."""
        if (url is None) == (data_base64 is None):
            raise ToolError("передайте url или data_base64, одно из двух")
        if data_base64 is not None:
            if not filename:
                raise ToolError("с data_base64 нужен filename с расширением, например cover.jpg")
            return media.save_base64(filename, data_base64)
        return await media.fetch(url or "", filename)

    @tool(READ)
    async def list_media() -> list:
        """Загруженные файлы: в посте указываются как ref (upload:<имя>); used — нужен ли черновику или посту."""
        return media.list_files()
