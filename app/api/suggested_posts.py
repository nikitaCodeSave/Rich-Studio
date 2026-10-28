"""Posts subscribers suggest in the channel's direct messages: list, approve, decline."""

from fastapi import APIRouter, Depends

from models import ApproveSuggestedReq, DeclineSuggestedReq
from services import idempotency, suggested_posts

from .deps import actor, idempotency_key, need

router = APIRouter(tags=["suggested posts"])
PUBLISH = [Depends(need("publish"))]


@router.get("/api/suggested-posts", summary="Предложенные посты из личных сообщений канала")
def list_suggested(state: str | None = None):
    """`state`: pending (ждёт решения), approved, declined, paid, refunded, failed. Студия видит предложения,
    пришедшие, пока она запущена: бот — админ канала с правом «Личные сообщения канала»."""
    return suggested_posts.listing(state)


@router.post(
    "/api/suggested-posts/{sid}/approve",
    dependencies=PUBLISH,
    summary="Принять предложенный пост: Telegram опубликует его в канале (approveSuggestedPost)",
)
async def approve(
    sid: str,
    req: ApproveSuggestedReq | None = None,
    who: str = Depends(actor),
    key: str | None = Depends(idempotency_key),
):
    """Платное предложение списывает звёзды с автора. Боту нужно право публиковать в канале."""
    req = req or ApproveSuggestedReq()
    return await idempotency.run(
        key,
        "suggested-approve",
        {"sid": sid, **req.model_dump()},
        lambda: suggested_posts.approve(sid, req.send_date, actor=who),
    )


@router.post(
    "/api/suggested-posts/{sid}/reopen",
    dependencies=PUBLISH,
    summary="Решить снова: решение без ответа Telegram (approving/declining) возвращается в ожидание",
)
def reopen(sid: str, who: str = Depends(actor)):
    """Только после проверки в личных сообщениях канала, что решение не дошло. Telegram решает предложение один раз:
    дошедшее решение, принятое снова, он отклонит, а его итог всё равно придёт и обновит состояние."""
    return suggested_posts.reopen(sid, actor=who)


@router.post(
    "/api/suggested-posts/{sid}/decline",
    dependencies=PUBLISH,
    summary="Отклонить предложенный пост с комментарием автору (declineSuggestedPost)",
)
async def decline(sid: str, req: DeclineSuggestedReq | None = None, who: str = Depends(actor)):
    return await suggested_posts.decline(sid, req.comment if req else None, actor=who)
