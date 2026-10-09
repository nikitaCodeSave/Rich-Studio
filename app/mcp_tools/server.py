"""Curated MCP tools over the studio services.

Same layer as api/: tools parse arguments, call services and return plain JSON.
Publishing goes through the same Idempotency-Key logic and records actor "mcp".
Deleting posts and arbitrary Bot API calls are left to the editor and /api/raw on purpose.
"""

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

import spec
import store
import telegram as tg
from errors import StudioError
from mcp_tools import preview_app
from models import Post
from services import idempotency, posts, publisher

ACTOR = "mcp"

INSTRUCTIONS = """Rich Studio: write, check, preview and publish rich posts for the user's Telegram channel.
Order: bot_api_status (what is new) → validate_post (fix every error) → preview_post (the user sees the post
as in Telegram right in the chat) → save_draft and/or send_preview_to_me
→ publish_post or schedule_post only after the user explicitly confirmed that exact post and channel.
A post is {"rich_message": InputRichMessage with exactly one of html|markdown|blocks, "reply_markup"?,
"options"?, "pin"?, "comment"?, "callbacks"?}; it reaches the Bot API unchanged. describe_type gives exact
fields of any Bot API type. Uploaded files are referenced as upload:<name>."""

PostArg = Annotated[
    dict[str, Any],
    Field(description="Пост: {rich_message: {html|markdown|blocks, media?}, reply_markup?, options?, pin?, comment?}"),
]
ChatArg = Annotated[str | None, Field(description="@канал или числовой id; пусто — канал по умолчанию")]
ChatsArg = Annotated[list[str] | None, Field(description="Несколько каналов сразу вместо chat_id")]
KeyArg = Annotated[
    str | None,
    Field(description="Один ключ на пост. При повторе после ошибки передайте тот же ключ: второго поста не будет"),
]

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOCAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
PUBLIC = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def _post(raw: dict) -> dict:
    try:
        return Post.model_validate(raw).model_dump()
    except ValueError as e:
        raise ToolError(f"неверный пост: {e}") from None


def _errors_as_tool_errors(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Studio and Telegram errors become tool errors the agent can read and fix."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except StudioError as e:
            detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail, ensure_ascii=False)
            raise ToolError(detail) from None
        except tg.TelegramError as e:
            raise ToolError(f"Telegram: {e.description}") from None

    return wrapper


def build() -> MCPServer:
    apps = Apps()
    apps.add_html_resource(
        preview_app.URI,
        preview_app.html(),
        title="Предпросмотр поста",
        description="Пост так, как его покажет Telegram",
        prefers_border=False,
    )

    @apps.tool(resource_uri=preview_app.URI, annotations=READ)
    @_errors_as_tool_errors
    async def preview_post(post: PostArg) -> dict:
        """Показать пост пользователю прямо в чате так, как его отрисует Telegram, вместе с ошибками проверки.
        Ничего не отправляет. Файлы upload:<имя> в чате не загружаются."""
        p = _post(post)
        return {"post": p, "validation": spec.validate_rich_message(spec.current, p["rich_message"])}

    server = MCPServer(name="rich-studio", title="Rich Studio", instructions=INSTRUCTIONS, extensions=[apps])

    def tool(annotations: ToolAnnotations):
        def register(fn):
            server.tool(annotations=annotations)(_errors_as_tool_errors(fn))
            return fn

        return register

    @tool(READ)
    async def bot_api_status() -> dict:
        """Версия Bot API, на которой работает студия, и последние изменения rich-типов и методов."""
        s = spec.current
        return {
            "version": s.version,
            "release_date": s.data.get("release_date"),
            "source": s.source,
            "changes": s.changes[:3],
        }

    @tool(READ)
    async def describe_type(
        name: Annotated[str, Field(description="Тип или метод, например InputRichBlockTable")],
    ) -> dict:
        """Точные поля типа или метода Bot API из текущей спецификации."""
        t = spec.current.types.get(name) or spec.current.methods.get(name)
        if not t:
            raise StudioError(f"{name} нет в спецификации {spec.current.version}")
        return t

    @tool(READ)
    async def validate_post(post: PostArg) -> dict:
        """Проверить пост по спецификации: ошибки (исправить обязательно), предупреждения, длина."""
        return spec.validate_rich_message(spec.current, _post(post)["rich_message"])

    @tool(LOCAL)
    async def save_draft(post: PostArg, title: Annotated[str, Field(description="Название черновика")] = "") -> dict:
        """Сохранить черновик: он появится в редакторе, предпросмотр по адресу preview_path."""
        d = store.save_draft(None, title, _post(post))
        assert d is not None  # noqa: S101 - just written
        return {"id": d["id"], "title": d["title"], "preview_path": f"/render/draft/{d['id']}"}

    @tool(READ)
    async def list_drafts() -> list:
        """Черновики студии (последние сверху)."""
        return store.list_drafts()

    @tool(LOCAL)
    async def send_preview_to_me(post: PostArg) -> dict:
        """Отправить пост пользователю в личку (TG_PREVIEW_CHAT), чтобы он увидел его в Telegram. В канал не уходит."""
        return await posts.preview(_post(post))

    @tool(READ)
    async def check_channel(chat_id: ChatArg = None) -> dict:
        """Бот админ канала и может публиковать?"""
        return await tg.check_chat(publisher.resolve_chat(chat_id))

    @tool(PUBLIC)
    async def publish_post(
        post: PostArg,
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        idempotency_key: KeyArg = None,
        dry_run: Annotated[bool, Field(description="Только показать запросы к Telegram, ничего не отправлять")] = False,
    ) -> dict:
        """Опубликовать пост в канал. Только после явного подтверждения пользователя для этого поста и канала."""
        p = _post(post)
        if dry_run:
            return await posts.send(p, chat_id, chat_ids, dry_run=True)
        body = {"post": p, "chat_id": chat_id, "chat_ids": chat_ids, "draft_id": None, "dry_run": False}
        return await idempotency.run(
            idempotency_key, "send", body, lambda: posts.send(p, chat_id, chat_ids, actor=ACTOR)
        )

    @tool(PUBLIC)
    async def schedule_post(
        post: PostArg,
        at: Annotated[str, Field(description="Когда: ISO 8601 с часовым поясом, например 2026-10-10T09:00:00+03:00")],
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        idempotency_key: KeyArg = None,
    ) -> dict:
        """Отложить публикацию. Только после явного подтверждения пользователя. Студия должна работать в это время."""
        p = _post(post)
        body = {"post": p, "chat_id": chat_id, "chat_ids": chat_ids, "draft_id": None, "dry_run": False, "at": at}

        async def action():
            return posts.schedule(p, at, chat_id, chat_ids, actor=ACTOR)

        return await idempotency.run(idempotency_key, "schedule", body, action)

    @tool(PUBLIC)
    async def edit_post(
        post_id: Annotated[str, Field(description="id поста из list_posts")], post: PostArg
    ) -> dict | None:
        """Заменить содержимое опубликованного или отложенного поста. Только после подтверждения пользователя."""
        return await posts.edit(post_id, _post(post))

    @tool(READ)
    async def list_posts(limit: Annotated[int, Field(ge=1, le=100)] = 20) -> list:
        """Опубликованные и отложенные посты: статус, реакции, нажатия кнопок, итоги опросов, кто отправил."""
        return store.list_posts(limit)

    return server
