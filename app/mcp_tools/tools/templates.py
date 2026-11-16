"""Post templates (a rubric: a digest, an announcement): a post with {{key}} fields that fills into a new draft."""

from typing import Annotated

from pydantic import Field

from errors import Conflict
from mcp_tools.common import DRAFT_CHATS, LOCAL, READ, PostArg, ToolDecorator, draft_links, parse_post
from models import TemplateField
from services import channels, publisher, templates

TemplateIdArg = Annotated[str, Field(description="id шаблона из list_post_templates")]


def _changed(e: Conflict) -> Conflict:
    """The agent needs what to do, not the whole current template."""
    current = e.detail.get("current") if isinstance(e.detail, dict) else None
    now = f" (сейчас version {current['version']})" if current else ""
    return Conflict(f"шаблон изменили{now}: прочитайте его заново через get_post_template и повторите")


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def list_post_templates() -> list:
        """Шаблоны постов (рубрики) пользователя, последние сверху: id, title, version, fields. Сам пост с {{key}}
        отдаёт get_post_template."""
        return templates.list_templates()

    @tool(READ)
    async def get_post_template(template_id: TemplateIdArg) -> dict:
        """Шаблон целиком: payload (пост с {{key}}), fields, version для save_post_template и draft_from_template."""
        return templates.get(template_id)

    @tool(LOCAL)
    async def save_post_template(
        title: Annotated[str, Field(description="Название шаблона (рубрики)")],
        post: PostArg,
        fields: Annotated[
            list[TemplateField],
            Field(description="Поля: каждое стоит в посте как {{key}}; type url — ссылка или файл upload:имя"),
        ],
        template_id: Annotated[
            str | None, Field(description="id шаблона, чтобы изменить его, а не создать новый")
        ] = None,
        version: Annotated[
            int | None, Field(description="С template_id: version из get_post_template, иначе ошибка, а не перезапись")
        ] = None,
    ) -> dict:
        """Сохранить пост шаблоном с полями; ничего не публикует. {{key}} — только в строковых значениях: в тексте
        блоков, в HTML в тексте или в атрибуте в кавычках; в Markdown полей нет, возьмите блоки или HTML. Значение
        подставляется как текст, разметки не создаёт. Ссылку (href/src, ключ url в JSON) задаёт только поле type url
        целиком ("url": "{{link}}", не https://site/{{slug}}); файл — тоже поле url со значением upload:имя, и
        только в медиа поста (блок, rich_message.media). Поля не ставятся рядом с parse_mode или entities (подпись
        медиа) и в callback_data кнопки с ответом в callbacks."""
        try:
            return templates.save(title, parse_post(post), [f.model_dump() for f in fields], template_id, version)
        except Conflict as e:
            raise _changed(e) from None

    @tool(LOCAL)
    async def draft_from_template(
        template_id: TemplateIdArg,
        version: Annotated[int, Field(description="version из list_post_templates или get_post_template")],
        values: Annotated[
            dict[str, str], Field(description="Значения по key из fields; поле без значения берёт default")
        ],
        title: Annotated[str, Field(description="Название черновика; пусто — название шаблона")] = "",
    ) -> dict:
        """Заполнить шаблон: новый черновик без каналов и его validation. Дальше как с любым черновиком:
        preview_post, затем publish_post с draft_id, version и chat_id (без него — канал по умолчанию, см. goes_to).
        Пустое обязательное поле или негодная ссылка — ошибка, черновик не создаётся."""
        try:
            d = templates.fill(template_id, version, values, title)
        except Conflict as e:
            raise _changed(e) from None
        return {
            "id": d["id"],
            "title": d["title"],
            "version": d["version"],
            "chat_ids": d["chat_ids"],
            "goes_to": channels.goes_to(d["chat_ids"]),
            "preview_path": f"/render/draft/{d['id']}",
            **draft_links(d["id"]),
            "validation": publisher.check(d["payload"], publisher.checked_targets(d["chat_ids"]), DRAFT_CHATS),
        }
