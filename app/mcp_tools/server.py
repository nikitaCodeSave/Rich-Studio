"""Curated MCP tools over the studio services.

Same layer as api/: tools parse arguments, call services and return plain JSON.
Publishing goes through the same Idempotency-Key logic and records actor "mcp".
Deleting posts and arbitrary Bot API calls are left to the editor and /api/raw on purpose.
The tools live in tools/, one module per area, each with register(tool); build() picks up every module there,
so a new tool or area never edits this file. Shared argument types and helpers: common.py.
"""

import importlib
import pkgutil

from mcp.server.apps import Apps
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

import settings
from mcp_tools import preview_app, tools
from mcp_tools.common import (
    PUBLIC,
    READ,
    DraftArg,
    OptPostArg,
    draft_links,
    errors_as_tool_errors,
    level_of,
    needs_level,
    post_source,
)
from services import publisher

INSTRUCTIONS = """Rich Studio: write, check, preview and publish rich posts for the user's Telegram channel.
Order: bot_api_status (what is new; refresh_bot_api if the user asks for the newest) → save_draft (returns validation:
fix every error and save again) → preview_post with draft_id (the user sees the post as in Telegram right in the chat)
→ publish_post or schedule_post with draft_id and the version preview_post returned, only after the user explicitly confirmed that exact post
and channel. Channels: list_channels gives titles and forum topics; name the channel by its title when asking, pass
chat_ids to save_draft so the draft (and the editor) knows where it goes. Pass the post JSON once, to save_draft; later calls take draft_id. validate_post checks without saving.
To continue the user's work: list_drafts → get_draft → save_draft with draft_id and version (the editor shows it).
To change a published post: list_posts → get_post (read it first) → edit_post (post or draft_id + version; a draft
with post_id is an edit of that post and can only go out through edit_post); post_history shows earlier versions.
studio_status says whether scheduled posts, button answers and first comments will actually work.
studio_health lists what is wrong and what to do (status ok|warn|fail per check); studio_logs reads the log.
A post is {"rich_message": InputRichMessage with exactly one of html|markdown|blocks, "reply_markup"?,
"options"?, "pin"?, "comment"?, "callbacks"?, "poll_after"? (sendPoll fields: a poll sent right after the post)};
it reaches the Bot API unchanged. describe_type gives exact
fields of any Bot API type. Uploaded files are referenced as upload:<name>."""


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
    @errors_as_tool_errors
    async def preview_post(post: OptPostArg = None, draft_id: DraftArg = None) -> dict:
        """Показать пост пользователю прямо в чате так, как его отрисует Telegram, вместе с ошибками проверки.
        Ничего не отправляет. Файлы upload:<имя> в чате не загружаются."""
        p, version = post_source(post, draft_id, None, publish=False)
        # version: what to pass to publish_post/edit_post, so exactly the version shown here goes out
        out = {"post": p, "validation": publisher.check(p)}
        return out if version is None else {**out, "version": version, **draft_links(draft_id or "")}

    server = MCPServer(name="rich-studio", title="Rich Studio", instructions=INSTRUCTIONS, extensions=[apps])

    def tool(annotations: ToolAnnotations):  # a ToolDecorator
        def register(fn):
            if annotations is PUBLIC and not settings.get().mcp_publish:
                return fn  # MCP_PUBLISH=off: the tool does not exist for agents
            server.tool(annotations=annotations)(errors_as_tool_errors(needs_level(level_of(annotations), fn)))
            return fn

        return register

    for m in pkgutil.iter_modules(tools.__path__):
        importlib.import_module(f"{tools.__name__}.{m.name}").register(tool)
    return server
