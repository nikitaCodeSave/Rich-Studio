"""Channels: titles, forum topics, the bot's rights."""

from fastapi import APIRouter

from models import ChannelReq, TopicsReq
from services import channels

router = APIRouter(tags=["channels"])


@router.get("/api/channels", summary="Каналы студии с названиями, темами форума и правами бота")
def list_channels():
    return channels.listing()


@router.post("/api/channels", summary="Добавить канал или проверить его заново: название, форум, права бота")
async def check_channel(req: ChannelReq):
    return await channels.check(req.chat_id)


@router.put("/api/channels/{chat_id}/topics", summary="Темы форума вручную: Bot API не умеет их перечислять")
def set_topics(chat_id: str, req: TopicsReq):
    """Темы приходят и сами, из событий создания темы."""
    return channels.set_topics(chat_id, [t.model_dump() for t in req.topics])


@router.delete("/api/channels/{chat_id}", summary="Убрать канал из списка (в Telegram ничего не меняется)")
def remove_channel(chat_id: str):
    channels.remove(chat_id)
    return {"ok": True}
