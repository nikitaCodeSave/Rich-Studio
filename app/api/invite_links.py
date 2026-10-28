"""Named invite links to a channel and how many came through each."""

from fastapi import APIRouter, Depends

from models import InviteLinkEditReq, InviteLinkRef, InviteLinkReq
from services import invite_links

from .deps import actor, need

router = APIRouter(tags=["invite links"])
# links change who can get into the channel
PUBLISH = [Depends(need("publish"))]


@router.get("/api/invite-links", summary="Пригласительные ссылки, созданные студией, со счётчиком вступлений")
def list_links(chat_id: str | None = None):
    """`joined` и `requests` студия считает сама по событиям `chat_member` и `chat_join_request`: Telegram боту
    таких счётчиков не даёт. Вступления, пока студия была выключена больше суток, не посчитаются."""
    return invite_links.listing(chat_id)


@router.post(
    "/api/invite-links",
    dependencies=PUBLISH,
    summary="Создать именную пригласительную ссылку (createChatInviteLink) или платную подписку",
)
async def create_link(req: InviteLinkReq, who: str = Depends(actor)):
    """Бот должен быть админом канала с правом «Пригласительные ссылки» (`can_invite_users`)."""
    return await invite_links.create(**req.model_dump(), actor=who)


@router.patch("/api/invite-links", dependencies=PUBLISH, summary="Изменить название, срок или предел ссылки")
async def edit_link(req: InviteLinkEditReq):
    """Непереданные срок и предел снимаются, как в Telegram. У платной подписки меняется только название."""
    return await invite_links.edit(**req.model_dump())


@router.post("/api/invite-links/revoke", dependencies=PUBLISH, summary="Отозвать ссылку: по ней больше не вступить")
async def revoke_link(req: InviteLinkRef):
    return await invite_links.revoke(req.invite_link)
