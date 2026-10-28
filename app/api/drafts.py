"""Drafts CRUD."""

from fastapi import APIRouter

import store
from errors import Conflict, NotFound
from models import DraftReq
from services import publisher

router = APIRouter(tags=["drafts"])


@router.get("/api/drafts", summary="Черновики студии, последние сверху")
def drafts():
    return store.list_drafts()


@router.get("/api/drafts/{did}", summary="Черновик целиком: пост, название, version, каналы")
def draft(did: str):
    d = store.get_draft(did)
    if not d:
        raise NotFound("Not Found")
    return d


@router.post("/api/drafts", summary="Новый черновик; он сразу открывается в редакторе")
def create_draft(req: DraftReq):
    return store.save_draft(
        None, req.title, req.post, post_id=req.post_id, chat_ids=publisher.normalize_chats(req.chat_ids) or None
    )


@router.put("/api/drafts/{did}", summary="Сохранить черновик; с version — 409, если его сохранили в другом месте")
def put_draft(did: str, req: DraftReq):
    """`chat_ids` — каналы черновика: публикация из него без `chat_id` уходит туда. Без поля остаются прежние."""
    old = (store.get_draft(did) or {}).get("chat_ids")
    chat_ids = (publisher.normalize_chats(req.chat_ids) or None) if req.chat_ids is not None else old
    d = store.save_draft(did, req.title, req.post, req.version, req.post_id, chat_ids)
    if d is None:
        current = store.get_draft(did)
        raise Conflict(
            {
                "message": "черновик изменили в другой вкладке или агентом"
                if current
                else "черновик удалили в другой вкладке",
                "current": current,
            }
        )
    return d


@router.delete("/api/drafts/{did}", summary="Удалить черновик")
def del_draft(did: str):
    store.delete_draft(did)
    return {"ok": True}
