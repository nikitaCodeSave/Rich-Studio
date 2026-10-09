"""Publishing and managing posts in channels."""

from fastapi import APIRouter, Body

import store
import telegram as tg
from errors import NotFound
from models import ButtonsReq, EditReq, PinReq, PollReq, Post, ScheduleReq, SendReq
from services import posts, publisher

router = APIRouter(tags=["telegram"])


@router.get("/api/check", summary="Бот админ канала? Есть права на публикацию?")
async def check(chat_id: str | None = None):
    return await tg.check_chat(publisher.resolve_chat(chat_id))


@router.post("/api/send", summary="Опубликовать пост (sendRichMessage)")
async def send(req: SendReq):
    return await posts.send(req.post.model_dump(), req.chat_id, req.chat_ids, req.draft_id)


@router.post("/api/preview-send", summary="Отправить пост себе в личку (TG_PREVIEW_CHAT)")
async def preview_send(req: SendReq):
    return await posts.preview(req.post.model_dump(), req.chat_id)


@router.post("/api/edit", summary="Заменить опубликованный пост (editMessageText + rich_message)")
async def edit(req: EditReq):
    return await posts.edit_message(req.post.model_dump(), req.chat_id, req.message_id)


@router.post("/api/schedule", summary="Отложить публикацию")
def schedule(req: ScheduleReq):
    return posts.schedule(req.post.model_dump(), req.at, req.chat_id, req.chat_ids, req.draft_id)


@router.post("/api/poll", summary="Опрос или викторина в канал (sendPoll), сразу или отложенно")
async def send_poll(req: PollReq):
    return await posts.send_poll(req.poll, req.chat_id, req.chat_ids, req.at, req.pin)


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


@router.get("/api/posts", summary="Опубликованные и отложенные посты")
def list_posts():
    return store.list_posts()


@router.get("/api/posts/{pid}")
def post(pid: str):
    p = store.get_post(pid)
    if not p:
        raise NotFound("Not Found")
    return p


@router.post("/api/posts/{pid}/edit", summary="Заменить содержимое поста, опубликованного через студию")
async def edit_post(pid: str, new: Post):
    return await posts.edit(pid, new.model_dump())


@router.delete("/api/posts/{pid}", summary="Отменить отложенный пост или удалить пост из канала")
async def delete_post(pid: str):
    return await posts.delete(pid)
