"""What every tool module shares: argument types, annotations, error mapping, the post a tool works on."""

import functools
import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

import security
import settings
import store
import telegram as tg
from errors import Conflict, NotFound, StudioError
from models import Post
from services import idempotency

ACTOR = "mcp"
# what server.build() hands each tools/ module: @tool(annotations) registers a function as a tool
ToolDecorator = Callable[[ToolAnnotations], Callable[[Callable[..., Any]], Callable[..., Any]]]


PostArg = Annotated[
    dict[str, Any],
    Field(
        description="Пост: {rich_message: {html|markdown|blocks, media?}, reply_markup?, options?, pin?, comment?, "
        "poll_after? (опрос сразу после поста)}"
    ),
]
OptPostArg = Annotated[
    dict[str, Any] | None,
    Field(
        description="Пост: {rich_message: {html|markdown|blocks, media?}, reply_markup?, options?, pin?, comment?, "
        "poll_after?}; или вместо него draft_id"
    ),
]
DraftArg = Annotated[
    str | None, Field(description="id черновика из save_draft вместо post: пост берётся из черновика, JSON не нужен")
]
VersionArg = Annotated[
    int | None,
    Field(description="version из save_draft/get_draft: если черновик успели изменить, будет ошибка, а не другой пост"),
]
ChatArg = Annotated[
    str | None,
    Field(description="@канал или числовой id (list_channels); пусто — каналы черновика, иначе канал по умолчанию"),
]
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


def level_of(annotations: ToolAnnotations) -> str:
    """The token level a tool needs: reading, drafting (studio data, the user's own chat) or publishing."""
    if annotations is PUBLIC:
        return "publish"
    return "read" if annotations.read_only_hint else "draft"


def actor() -> str:
    """Who to record for a post: mcp, or mcp@<token name> when the agent came with a named token."""
    name = security.current().name
    return f"{ACTOR}@{name}" if name else ACTOR


def needs_level(level: str, fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """A tool the caller's token is not allowed to use answers with an error the agent can show the user."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        who = security.current()
        if not who.can(level):
            raise ToolError(f"у токена {who.name} нет права {level}: попросите пользователя сделать это в редакторе")
        return await fn(*args, **kwargs)

    return wrapper


def parse_post(raw: dict) -> dict:
    try:
        return Post.model_validate(raw).model_dump()
    except ValueError as e:
        raise ToolError(f"неверный пост: {e}") from None


def post_source(
    post: dict | None, draft_id: str | None, version: int | None, *, publish: bool, edits: str | None = None
) -> tuple[dict, int | None]:
    """The post itself or the one saved in a draft, so an agent sends the JSON once (to save_draft); with the draft's
    version. Publishing from a draft needs that version: the user may have changed it in the editor after the preview.
    A draft that is a working copy of a published post (post_id) only goes out as an edit of that post (`edits`):
    publishing it would put a second post in the channel."""
    if (post is None) == (draft_id is None):
        raise ToolError("передайте post или draft_id, одно из двух")
    if post is not None:
        return parse_post(post), None
    d = store.get_draft(draft_id or "")
    if not d:
        raise NotFound(f"черновика {draft_id} нет")
    if publish and version is None:
        raise ToolError(
            "с draft_id передайте version из save_draft или get_draft: публикуется то, что видел пользователь"
        )
    if version is not None and d["version"] != version:
        raise Conflict(
            f"черновик изменили (version {d['version']}, а не {version}): покажите его пользователю заново через "
            "get_draft и preview_post"
        )
    if publish and d.get("post_id") and d["post_id"] != edits:
        raise ToolError(
            f"черновик {draft_id} — правка поста {d['post_id']}: замените его через edit_post с post_id "
            f"{d['post_id']}, draft_id и version, а не публикуйте новый пост"
        )
    return parse_post(d["payload"]), d["version"]


def draft_links(draft_id: str) -> dict:
    """Full links the agent can give the person: the draft in the editor and its preview page."""
    base = settings.get().public_url
    return {"editor_url": f"{base}/?draft={draft_id}", "preview_url": f"{base}/render/draft/{draft_id}"}


def request_key(given: str | None, scope: str, body: dict) -> str:
    """Agents retry after a timeout and often without a key: the request itself becomes the key then."""
    return given or "auto:" + idempotency.fingerprint(scope, body)


def errors_as_tool_errors(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
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
