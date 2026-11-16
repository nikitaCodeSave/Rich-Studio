"""Post templates (a rubric: a digest, an announcement): a post with {{key}} fields that fills into a new draft."""

from typing import Annotated

from pydantic import Field

from errors import Conflict
from mcp_tools.common import LOCAL, READ, PostArg, ToolDecorator, draft_links, parse_post
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
        """Шаблоны постов (рубрики) пользователя, последние сверху: id, title, version и fields — поля формы
        {key, label, type, required, default}. Сам пост с {{key}} отдаёт get_post_template."""
        return templates.list_templates()

    @tool(READ)
    async def get_post_template(template_id: TemplateIdArg) -> dict:
        """Шаблон целиком: payload — пост с {{key}} в строковых значениях, fields, version для save_post_template
        и draft_from_template."""
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
        """Сохранить пост шаблоном с полями. {{key}} ставьте только в строковые значения: в блоках — в текст,
        в HTML — в текст или в атрибут в кавычках, в href/src — только поле type url целиком (href="{{link}}").
        Значение при заполнении всегда текст: разметки оно не создаёт. В Markdown поля пока не подставляются:
        возьмите блоки или HTML. Файл подставляет поле url целиком со значением upload:имя в медиа поста (блок,
        rich_message.media), не в атрибут HTML. HTML с полями должен разбираться без ошибок HTML5 (без «=» без
        имени, лишних кавычек в тегах, < вне тегов). Ничего не публикует."""
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
        """Заполнить шаблон: новый черновик без каналов с подставленными значениями и его проверка (validation).
        Дальше как с любым черновиком: preview_post, publish_post с draft_id и version; канал — chat_id в
        publish_post (goes_to показывает, куда черновик уйдёт без него).
        Обязательное пустое поле или ссылка не той схемы — ошибка, черновик не создаётся."""
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
            "validation": publisher.check(d["payload"], publisher.checked_targets(d["chat_ids"])),
        }
