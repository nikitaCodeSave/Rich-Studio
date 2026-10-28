"""Drafts: check, save, read, and a look in the user's own chat before anything goes out."""

from typing import Annotated

from pydantic import Field

import store
from errors import Conflict, NotFound
from mcp_tools.common import LOCAL, READ, TO_ME, PostArg, ToolDecorator, draft_links, parse_post
from services import channels, posts, publisher


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def validate_post(post: PostArg) -> dict:
        """Проверить пост по спецификации: ошибки (исправить обязательно), предупреждения, длина."""
        return publisher.check(parse_post(post))

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
        chat_ids: Annotated[
            list[str] | None,
            Field(
                description="Каналы черновика (list_channels): редактор откроет его с ними, publish_post без chat_id "
                "уйдёт туда. Без поля у существующего черновика остаются прежние"
            ),
        ] = None,
    ) -> dict:
        """Сохранить черновик и сразу проверить его (validation: ошибки исправить и сохранить снова). Он появится
        в редакторе, предпросмотр по адресу preview_path; дальше preview_post и publish_post принимают draft_id.
        С draft_id обновляет существующий черновик (связь с правкой опубликованного поста сохраняется)."""
        old = store.get_draft(draft_id) if draft_id else None
        if draft_id and not old:
            raise NotFound(f"черновика {draft_id} нет")
        chats = (publisher.normalize_chats(chat_ids) or None) if chat_ids is not None else (old or {}).get("chat_ids")
        d = store.save_draft(
            draft_id,
            title or (old or {}).get("title") or "",
            parse_post(post),
            version,
            (old or {}).get("post_id"),
            chats,
        )
        if d is None:
            raise Conflict("черновик изменили после get_draft: прочитайте его заново и перенесите правки")
        return {
            "id": d["id"],
            "title": d["title"],
            "version": d["version"],
            "chat_ids": d["chat_ids"],
            "goes_to": channels.goes_to(d["chat_ids"]),
            "preview_path": f"/render/draft/{d['id']}",
            **draft_links(d["id"]),
            "validation": publisher.check(d["payload"]),
        }

    @tool(READ)
    async def list_drafts() -> list:
        """Черновики студии (последние сверху); post_id — опубликованный пост, который правится в черновике;
        goes_to — куда уйдёт публикация без chat_id: каналы черновика по названию или канал по умолчанию."""
        return [{**d, "goes_to": channels.goes_to(d.get("chat_ids"))} for d in store.list_drafts()]

    @tool(READ)
    async def get_draft(draft_id: Annotated[str, Field(description="id из list_drafts")]) -> dict:
        """Черновик целиком: пост (payload), название, version для save_draft."""
        d = store.get_draft(draft_id)
        if not d:
            raise NotFound(f"черновика {draft_id} нет")
        return {**d, **draft_links(d["id"]), "goes_to": channels.goes_to(d.get("chat_ids"))}

    @tool(TO_ME)
    async def send_preview_to_me(post: PostArg) -> dict:
        """Отправить пост пользователю в личку (TG_PREVIEW_CHAT), чтобы он увидел его в Telegram. В канал не уходит.
        В ответе parsed.input_blocks: пост так, как его разобрал Telegram (можно отправить как rich_message.blocks)."""
        res = await posts.preview(parse_post(post))
        # the agent needs the post once: Telegram's output blocks are for the editor's preview only
        if isinstance(res.get("parsed"), dict):
            res["parsed"].pop("blocks", None)
        return res
