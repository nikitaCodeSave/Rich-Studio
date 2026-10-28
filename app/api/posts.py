"""Publishing and managing posts in channels."""

from fastapi import APIRouter, Body, Depends, Query

import settings
import store
from errors import NotFound
from models import (
    ButtonsReq,
    EditReq,
    MediaPostReq,
    PinReq,
    PollReq,
    Post,
    PublishReq,
    RescheduleReq,
    ScheduleReq,
    SendReq,
)
from services import channels, idempotency, listing, posts, publisher

from .deps import actor, idempotency_key, need

router = APIRouter(tags=["telegram"])
# what goes into a channel or changes it; preview-send to the user's own chat needs only draft
PUBLISH = [Depends(need("publish"))]


@router.get("/api/check", summary="Бот админ канала? Есть права на публикацию? Канал запоминается с названием")
async def check(chat_id: str | None = None):
    return await channels.check(publisher.resolve_chat(chat_id))


@router.post("/api/send", dependencies=PUBLISH, summary="Опубликовать пост (sendRichMessage)")
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
    """В ответе `parsed`: блоки, на которые Telegram разобрал пост, и они же для повторной отправки (`input_blocks`).
    `chat_id`, отличный от `TG_PREVIEW_CHAT`, требует права `publish`: это уже отправка в чужой чат."""
    preview_chat = settings.get().preview_chat
    if req.chat_id and (
        not preview_chat or publisher.resolve_chat(req.chat_id) != publisher.resolve_chat(preview_chat)
    ):
        need("publish")()
    return await posts.preview(req.post.model_dump(), req.chat_id)


@router.post("/api/edit", dependencies=PUBLISH, summary="Заменить опубликованный пост (editMessageText + rich_message)")
async def edit(req: EditReq, who: str = Depends(actor)):
    return await posts.edit_message(req.post.model_dump(), req.chat_id, req.message_id, who)


@router.post("/api/schedule", dependencies=PUBLISH, summary="Отложить публикацию")
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


@router.post("/api/poll", dependencies=PUBLISH, summary="Опрос или викторина в канал (sendPoll), сразу или отложенно")
async def send_poll(req: PollReq, who: str = Depends(actor), key: str | None = Depends(idempotency_key)):
    """Опрос сразу после поста — поле `poll_after` в самом посте: оно проверяется по `sendPoll` до отправки поста."""
    return await idempotency.run(
        key,
        "poll",
        req.model_dump(),
        lambda: posts.send_poll(req.poll, req.chat_id, req.chat_ids, req.at, req.pin, actor=who),
    )


@router.post(
    "/api/media-post",
    dependencies=PUBLISH,
    summary="Live Photo (sendLivePhoto) или платный контент за звёзды (sendPaidMedia), сразу или отложенно",
)
async def send_media(req: MediaPostReq, who: str = Depends(actor), key: str | None = Depends(idempotency_key)):
    """Отдельное сообщение, не rich-пост: в блоках `InputRichMessage` таких медиа нет. Платный контент требует,
    чтобы в канале была разрешена публикация платных медиа (`can_send_paid_media`)."""
    return await idempotency.run(
        key,
        "media-post",
        req.model_dump(),
        lambda: posts.send_media(req.kind, req.params, req.chat_id, req.chat_ids, req.at, req.pin, actor=who),
    )


@router.post("/api/posts/{pid}/stop-poll", dependencies=PUBLISH, summary="Закрыть опрос и сохранить итоги")
async def stop_poll(pid: str):
    return await posts.stop_poll(pid)


@router.post("/api/posts/{pid}/pin", dependencies=PUBLISH, summary="Закрепить пост в канале")
async def pin_post(pid: str, req: PinReq = Body(default_factory=PinReq)):
    return await posts.pin(pid, req.disable_notification)


@router.delete("/api/posts/{pid}/pin", dependencies=PUBLISH, summary="Открепить пост")
async def unpin_post(pid: str):
    return await posts.unpin(pid)


@router.post(
    "/api/posts/{pid}/buttons",
    dependencies=PUBLISH,
    summary="Заменить только кнопки под постом (editMessageReplyMarkup), текст не трогается",
)
async def set_buttons(pid: str, req: ButtonsReq, who: str = Depends(actor)):
    """Прежние кнопки попадают в историю поста (`/versions`), `version` поста растёт."""
    return await posts.set_buttons(pid, req.reply_markup, req.callbacks, who)


@router.get("/api/posts", summary="Опубликованные, отложенные и неудачные посты: первая строка, миниатюра, ссылка")
def list_posts(status: str | None = None, chat: str | None = None, limit: int = Query(100, ge=1, le=500)):
    """Для каждого поста: `title`, `thumb`, `link`, нажатия (`clicks`) и текст кнопки каждого `callback_data`
    (`buttons`). Отправка, которой Telegram отказал, — статус `failed` с ошибкой."""
    return listing.list_posts(limit, status=status, chat=chat)


@router.get("/api/posts/{pid}", summary="Пост целиком: payload, статус, канал, version, реакции, нажатия")
def post(pid: str):
    p = store.get_post(pid)
    if not p:
        raise NotFound("Not Found")
    return p


@router.post(
    "/api/posts/{pid}/edit", dependencies=PUBLISH, summary="Заменить содержимое поста, опубликованного через студию"
)
async def edit_post(
    pid: str,
    new: Post,
    who: str = Depends(actor),
    post_version: int | None = Query(
        None,
        description="Поле version поста, который вы прочитали. Если пост с тех пор заменили, ответ 409, а не "
        "молчаливая перезапись чужой правки",
    ),
):
    """`?post_version=` — поле `version` поста из `GET /api/posts/{id}`: если пост с тех пор заменили, 409."""
    return await posts.edit(pid, new.model_dump(), who, post_version)


@router.get("/api/posts/{pid}/versions", summary="Как выглядел пост до каждой замены, новые сверху")
def post_versions(pid: str):
    return posts.versions(pid)


@router.post(
    "/api/posts/{pid}/reschedule",
    dependencies=PUBLISH,
    summary="Перенести отложенный пост или вернуть пропущенный в очередь на новое время",
)
def reschedule(pid: str, req: RescheduleReq):
    return posts.reschedule(pid, req.at)


@router.delete(
    "/api/posts/{pid}",
    dependencies=PUBLISH,
    summary="Отменить отложенный или пропущенный пост, или удалить пост из канала",
)
async def delete_post(pid: str):
    """`DELETE` неудачной отправки убирает её из списка."""
    return await posts.delete(pid)
