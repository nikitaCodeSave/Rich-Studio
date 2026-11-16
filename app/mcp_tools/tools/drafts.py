"""Drafts: check, save, read, and a look in the user's own chat before anything goes out."""

from typing import Annotated

from pydantic import Field

import store
from errors import Conflict, NotFound
from mcp_tools.common import LOCAL, READ, TO_ME, PostArg, ToolDecorator, draft_links, parse_post
from services import channels, posts, publisher
from services.drafts import keep_notes


def register(tool: ToolDecorator) -> None:
    @tool(READ)
    async def validate_post(
        post: PostArg,
        chat_ids: Annotated[
            list[str] | None,
            Field(description="Куда пойдёт пост (list_channels); без поля — канал по умолчанию"),
        ] = None,
    ) -> dict:
        """Проверить пост по спецификации: ошибки (исправить обязательно), предупреждения, длина. Для проверенных
        каналов предупреждает о том, что Telegram в чате такого типа не примет (кнопка web_app вне лички,
        switch_inline_query в канале, message_effect_id…): правила взяты из описаний полей Bot API."""
        return publisher.check(parse_post(post), publisher.checked_targets(chat_ids))

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
        """Сохранить черновик и сразу проверить его (validation: ошибки исправить и сохранить снова; первый
        комментарий и rich-ответы на кнопки, callbacks[…].reply, проверяются как пост, их ошибки начинаются
        с «комментарий:» и «ответ на кнопку «…»:»; предупреждения называют и то, что Telegram не примет в каналах
        черновика, например кнопку web_app в канале). Он появится
        в редакторе, предпросмотр по адресу preview_path; дальше preview_post и publish_post принимают draft_id.
        С draft_id обновляет существующий черновик (связь с правкой опубликованного поста сохраняется).
        Заметки студии в _studio (source, warnings пересланного поста) остаются; буферы редактора сбрасываются:
        они описывали прежний пост."""
        old = store.get_draft(draft_id) if draft_id else None
        if draft_id and not old:
            raise NotFound(f"черновика {draft_id} нет")
        chats = (publisher.normalize_chats(chat_ids) or None) if chat_ids is not None else (old or {}).get("chat_ids")
        d = store.save_draft(
            draft_id,
            title or (old or {}).get("title") or "",
            keep_notes(parse_post(post), (old or {}).get("payload")),
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
            "validation": publisher.check(d["payload"], publisher.checked_targets(d["chat_ids"])),
        }

    @tool(READ)
    async def list_drafts() -> list:
        """Черновики студии (последние сверху); post_id — опубликованный пост, который правится в черновике;
        goes_to — куда уйдёт публикация без chat_id: каналы черновика по названию или канал по умолчанию."""
        return [{**d, "goes_to": channels.goes_to(d.get("chat_ids"))} for d in store.list_drafts()]

    @tool(READ)
    async def get_draft(draft_id: Annotated[str, Field(description="id из list_drafts")]) -> dict:
        """Черновик целиком: пост (payload), название, version для save_draft. В payload._studio только заметки
        студии (source, warnings у пересланного поста; в Telegram не уходят); копии поста для редактора
        (buffers, неприменённый json) не отдаются: пост это rich_message. save_draft кладёт пост как есть,
        и редактор откроет его без прежних буферов."""
        d = store.get_draft(draft_id)
        if not d:
            raise NotFound(f"черновика {draft_id} нет")
        studio = d["payload"].get("_studio") if isinstance(d.get("payload"), dict) else None
        if isinstance(studio, dict):
            notes = {k: v for k, v in studio.items() if k not in ("json", "buffers")}
            payload = {k: v for k, v in d["payload"].items() if k != "_studio"}
            d = {**d, "payload": {**payload, **({"_studio": notes} if notes else {})}}
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
