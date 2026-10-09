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

import logs
import settings
import spec
import store
import telegram as tg
from errors import Conflict, NotFound, StudioError
from mcp_tools import preview_app
from models import Post
from services import health, idempotency, media, posts, publisher, updates

ACTOR = "mcp"

INSTRUCTIONS = """Rich Studio: write, check, preview and publish rich posts for the user's Telegram channel.
Order: bot_api_status (what is new; refresh_bot_api if the user asks for the newest) → validate_post (fix every error) → preview_post (the user sees the post
as in Telegram right in the chat) → save_draft and/or send_preview_to_me
→ publish_post or schedule_post only after the user explicitly confirmed that exact post and channel.
To continue the user's work: list_drafts → get_draft → save_draft with draft_id and version (the editor shows it).
To change a published post: list_posts → get_post (read it first) → edit_post; post_history shows earlier versions.
studio_status says whether scheduled posts, button answers and first comments will actually work.
studio_health lists what is wrong and what to do (status ok|warn|fail per check); studio_logs reads the log.
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
    Field(
        description="Один ключ на пост. При повторе после ошибки передайте тот же ключ: второго поста не будет. "
        "Без ключа ключом считается сам запрос: тот же пост в тот же канал в течение суток не уйдёт второй раз"
    ),
]
PostIdArg = Annotated[str, Field(description="id поста из list_posts")]

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOCAL = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
# reaches Telegram but changes nothing in the channel
TELEGRAM_READ = ToolAnnotations(read_only_hint=True, open_world_hint=True)
TO_ME = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
# downloads the Bot API spec from GitHub and updates the studio's cached copy
FETCH = TO_ME
PUBLIC = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)


def _post(raw: dict) -> dict:
    try:
        return Post.model_validate(raw).model_dump()
    except ValueError as e:
        raise ToolError(f"неверный пост: {e}") from None


def _key(given: str | None, scope: str, body: dict) -> str:
    """Agents retry after a timeout and often without a key: the request itself becomes the key then."""
    return given or "auto:" + idempotency.fingerprint(scope, body)


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
            wait = f" (повторить не раньше чем через {e.retry_after} с)" if e.retry_after else ""
            raise ToolError(f"Telegram: {e.description}{wait}") from None

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
            if annotations is PUBLIC and not settings.get().mcp_publish:
                return fn  # MCP_PUBLISH=off: the tool does not exist for agents
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
            "gaps": s.summary()["gaps"],
            "last_check": s.last_check,
        }

    @tool(FETCH)
    async def refresh_bot_api() -> dict:
        """Скачать свежую спецификацию Bot API прямо сейчас (обычно студия делает это сама при запуске и раз в
        12 часов). Возвращает версию, изменения rich-типов и чего не хватает подписям и предпросмотру (gaps).
        При ошибке скачивания студия продолжает работать на прежней версии, ошибка в поле error."""
        return await spec.current.refresh(force=True)

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
    async def save_draft(
        post: PostArg,
        title: Annotated[str, Field(description="Название черновика; пусто при обновлении — прежнее")] = "",
        draft_id: Annotated[
            str | None, Field(description="id черновика, чтобы обновить его, а не создать новый")
        ] = None,
        version: Annotated[
            int | None,
            Field(
                description="version из get_draft: если пользователь успел изменить черновик, будет ошибка, а не перезапись"
            ),
        ] = None,
    ) -> dict:
        """Сохранить черновик: он появится в редакторе, предпросмотр по адресу preview_path.
        С draft_id обновляет существующий черновик (связь с правкой опубликованного поста сохраняется)."""
        old = store.get_draft(draft_id) if draft_id else None
        if draft_id and not old:
            raise NotFound(f"черновика {draft_id} нет")
        d = store.save_draft(
            draft_id, title or (old or {}).get("title") or "", _post(post), version, (old or {}).get("post_id")
        )
        if d is None:
            raise Conflict("черновик изменили после get_draft: прочитайте его заново и перенесите правки")
        return {
            "id": d["id"],
            "title": d["title"],
            "version": d["version"],
            "preview_path": f"/render/draft/{d['id']}",
        }

    @tool(READ)
    async def list_drafts() -> list:
        """Черновики студии (последние сверху); post_id — опубликованный пост, который правится в черновике."""
        return store.list_drafts()

    @tool(READ)
    async def get_draft(draft_id: Annotated[str, Field(description="id из list_drafts")]) -> dict:
        """Черновик целиком: пост (payload), название, version для save_draft."""
        d = store.get_draft(draft_id)
        if not d:
            raise NotFound(f"черновика {draft_id} нет")
        return d

    @tool(READ)
    async def list_media() -> list:
        """Загруженные файлы: в посте указываются как ref (upload:<имя>); used — нужен ли черновику или посту."""
        return media.list_files()

    @tool(READ)
    async def studio_status() -> dict:
        """Что сейчас работает: токен, канал по умолчанию, приём событий (без него не будет ответов на кнопки,
        первых комментариев и реакций), предел опоздания отложенных постов, можно ли агентам публиковать."""
        cfg = settings.get()
        return {
            "token_set": bool(cfg.bot_token),
            "default_chat": cfg.default_chat or None,
            "preview_chat_set": bool(cfg.preview_chat),
            "updates": updates.STATUS.as_dict(),
            "schedule_late_min": cfg.schedule_late_min,
            "mcp_publish": cfg.mcp_publish,
            "note": "отложенные посты отправляет сама студия: в это время она должна работать",
        }

    @tool(READ)
    async def studio_health() -> dict:
        """Состояние студии целиком: проверки (база, диск, Telegram, фоновые циклы, очередь, сбойные и неясные
        отправки, спецификация, бэкап, место, ошибки в журнале) со статусом ok/warn/fail и подсказкой action,
        счётчики вызовов и последние предупреждения. Ничего не меняет."""
        return health.report()

    @tool(READ)
    async def studio_logs(
        level: Annotated[str, Field(description="DEBUG | INFO | WARNING | ERROR | CRITICAL: этот и выше")] = "WARNING",
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
        since: Annotated[
            float | None, Field(ge=0, le=4102444800, description="unix-время: только строки не старше")
        ] = None,
        logger: Annotated[str, Field(description="начало имени: rich-studio.telegram, rich-studio.http…")] = "",
        text: Annotated[str, Field(description="подстрока в строке")] = "",
    ) -> list:
        """Последние строки журнала студии (JSON: ts, level, logger, msg, event и поля события), новые в конце.
        Токена и текста постов в журнале нет."""
        return logs.read(level, limit, since, logger, text)

    @tool(TO_ME)
    async def send_preview_to_me(post: PostArg) -> dict:
        """Отправить пост пользователю в личку (TG_PREVIEW_CHAT), чтобы он увидел его в Telegram. В канал не уходит."""
        return await posts.preview(_post(post))

    @tool(TELEGRAM_READ)
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
            _key(idempotency_key, "send", body), "send", body, lambda: posts.send(p, chat_id, chat_ids, actor=ACTOR)
        )

    @tool(PUBLIC)
    async def schedule_post(
        post: PostArg,
        at: Annotated[str, Field(description="Когда: ISO 8601 с часовым поясом, например 2026-10-10T09:00:00+03:00")],
        chat_id: ChatArg = None,
        chat_ids: ChatsArg = None,
        idempotency_key: KeyArg = None,
        dry_run: Annotated[bool, Field(description="Только показать запросы и время, ничего не откладывать")] = False,
    ) -> dict:
        """Отложить публикацию. Только после явного подтверждения пользователя. Студия должна работать в это время;
        если она была выключена и пост опоздал больше schedule_late_min, он станет missed и не уйдёт сам."""
        p = _post(post)
        if dry_run:
            return posts.schedule(p, at, chat_id, chat_ids, dry_run=True)
        body = {"post": p, "chat_id": chat_id, "chat_ids": chat_ids, "draft_id": None, "dry_run": False, "at": at}

        async def action():
            return posts.schedule(p, at, chat_id, chat_ids, actor=ACTOR)

        return await idempotency.run(_key(idempotency_key, "schedule", body), "schedule", body, action)

    @tool(PUBLIC)
    async def edit_post(post_id: PostIdArg, post: PostArg) -> dict | None:
        """Заменить содержимое опубликованного или отложенного поста. Только после подтверждения пользователя.
        Сначала прочитайте пост через get_post. Ответы на кнопки и первый комментарий остаются, если их не передать;
        прежняя версия попадёт в post_history."""
        return await posts.edit(post_id, _post(post), actor=ACTOR)

    @tool(PUBLIC)
    async def reschedule_post(
        post_id: PostIdArg,
        at: Annotated[str, Field(description="Новое время: ISO 8601 с часовым поясом")],
    ) -> dict | None:
        """Перенести отложенный пост или вернуть пропущенный (missed) в очередь. Только после подтверждения."""
        return posts.reschedule(post_id, at)

    @tool(PUBLIC)
    async def cancel_scheduled_post(post_id: PostIdArg) -> dict | None:
        """Отменить отложенный или пропущенный пост (из канала ничего не удаляется). Только после подтверждения."""
        p = store.get_post(post_id)
        if not p:
            raise NotFound(f"поста {post_id} нет")
        if p["status"] not in ("scheduled", "missed"):
            raise StudioError(f"пост в статусе {p['status']}: отменить можно только отложенный или пропущенный")
        return await posts.delete(post_id)

    @tool(READ)
    async def get_post(post_id: PostIdArg) -> dict:
        """Пост целиком: содержимое (payload), статус, канал, message_id, реакции, нажатия, итоги опроса."""
        p = store.get_post(post_id)
        if not p:
            raise NotFound(f"поста {post_id} нет")
        return p

    @tool(READ)
    async def post_history(post_id: PostIdArg) -> list:
        """Как пост выглядел до каждой замены (новые сверху), кто заменял."""
        return posts.versions(post_id)

    @tool(READ)
    async def list_posts(limit: Annotated[int, Field(ge=1, le=100)] = 20) -> list:
        """Опубликованные и отложенные посты: статус, реакции, нажатия кнопок, итоги опросов, кто отправил."""
        return store.list_posts(limit)

    return server
