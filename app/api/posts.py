"""Publishing and managing posts in channels."""

from fastapi import APIRouter, Body, Depends, Query

import store
import telegram as tg
from errors import NotFound
from models import ButtonsReq, EditReq, PinReq, PollReq, Post, PublishReq, RescheduleReq, ScheduleReq, SendReq
from services import idempotency, listing, posts, publisher

from .deps import actor, idempotency_key

router = APIRouter(tags=["telegram"])


@router.get("/api/check", summary="Бот админ канала? Есть права на публикацию?")
async def check(chat_id: str | None = None):
    return await tg.check_chat(publisher.resolve_chat(chat_id))


@router.post("/api/send", summary="Опубликовать пост (sendRichMessage)")
async def send(req: PublishReq, who: str = Depends(actor), key: str | None = Depends(idempotency_key)):
    post = req.post.model_dump()
    if req.dry_run:  # nothing is sent, so there is nothing to deduplicate
        return await posts.send(post, req.chat_id, req.chat_ids, req.draft_id, dry_run=True, as_new=req.as_new)
    return await idempotency.run(
        key,
        "send",
        req.model_dump(),
        lambda: posts.send(post, req.chat_id, req.chat_ids, req.draft_id, actor=who, as_new=req.as_new),
    )


@router.post("/api/preview-send", summary="Отправить пост себе в личку (TG_PREVIEW_CHAT)")
async def preview_send(req: SendReq):
    return await posts.preview(req.post.model_dump(), req.chat_id)


@router.post("/api/edit", summary="Заменить опубликованный пост (editMessageText + rich_message)")
async def edit(req: EditReq, who: str = Depends(actor)):
    return await posts.edit_message(req.post.model_dump(), req.chat_id, req.message_id, who)


@router.post("/api/schedule", summary="Отложить публикацию")
async def schedule(req: ScheduleReq, who: str = Depends(actor), key: str | None = Depends(idempotency_key)):
    if req.dry_run:
        return posts.schedule(
            req.post.model_dump(), req.at, req.chat_id, req.chat_ids, req.draft_id, dry_run=True, as_new=req.as_new
        )

    async def action():
        return posts.schedule(
            req.post.model_dump(), req.at, req.chat_id, req.chat_ids, req.draft_id, actor=who, as_new=req.as_new
        )

    return await idempotency.run(key, "schedule", req.model_dump(), action)


@router.post("/api/poll", summary="Опрос или викторина в канал (sendPoll), сразу или отложенно")
async def send_poll(req: PollReq, who: str = Depends(actor), key: str | None = Depends(idempotency_key)):
    return await idempotency.run(
        key,
        "poll",
        req.model_dump(),
        lambda: posts.send_poll(req.poll, req.chat_id, req.chat_ids, req.at, req.pin, actor=who),
    )


@router.post("/api/posts/{pid}/stop-poll", summary="Закрыть опрос и сохранить итоги")
async def stop_poll(pid: str):
    return await posts.stop_poll(pid)


@router.post("/api/posts/{pid}/pin", summary="Закрепить пост в канале")
async def pin_post(pid: str, req: PinReq = Body(default_factory=PinReq)):
    return await posts.pin(pid, req.disable_notification)


@router.delete("/api/posts/{pid}/pin", summary="Открепить пост")
async def unpin_post(pid: str):
    return await posts.unpin(pid)


@router.post(
    "/api/posts/{pid}/buttons", summary="Заменить только кнопки под постом (editMessageReplyMarkup), текст не трогается"
)
async def set_buttons(pid: str, req: ButtonsReq):
    return await posts.set_buttons(pid, req.reply_markup, req.callbacks)


@router.get("/api/posts", summary="Опубликованные, отложенные и неудачные посты: первая строка, миниатюра, ссылка")
def list_posts(status: str | None = None, chat: str | None = None, limit: int = Query(100, ge=1, le=500)):
    return listing.list_posts(limit, status=status, chat=chat)


@router.get("/api/posts/{pid}")
def post(pid: str):
    p = store.get_post(pid)
    if not p:
        raise NotFound("Not Found")
    return p


@router.post("/api/posts/{pid}/edit", summary="Заменить содержимое поста, опубликованного через студию")
async def edit_post(pid: str, new: Post, who: str = Depends(actor)):
    return await posts.edit(pid, new.model_dump(), who)


@router.get("/api/posts/{pid}/versions", summary="Как выглядел пост до каждой замены, новые сверху")
def post_versions(pid: str):
    return posts.versions(pid)


@router.post(
    "/api/posts/{pid}/reschedule", summary="Перенести отложенный пост или вернуть пропущенный в очередь на новое время"
)
def reschedule(pid: str, req: RescheduleReq):
    return posts.reschedule(pid, req.at)


@router.delete("/api/posts/{pid}", summary="Отменить отложенный или пропущенный пост, или удалить пост из канала")
async def delete_post(pid: str):
    return await posts.delete(pid)
