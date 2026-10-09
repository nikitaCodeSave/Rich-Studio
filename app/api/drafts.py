"""Drafts CRUD."""

from fastapi import APIRouter

import store
from errors import Conflict, NotFound
from models import DraftReq
from services import publisher

router = APIRouter(tags=["drafts"])


@router.get("/api/drafts")
def drafts():
    return store.list_drafts()


@router.get("/api/drafts/{did}")
def draft(did: str):
    d = store.get_draft(did)
    if not d:
        raise NotFound("Not Found")
    return d


@router.post("/api/drafts")
def create_draft(req: DraftReq):
    return store.save_draft(
        None, req.title, req.post, post_id=req.post_id, chat_ids=publisher.normalize_chats(req.chat_ids) or None
    )


@router.put("/api/drafts/{did}")
def put_draft(did: str, req: DraftReq):
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


@router.delete("/api/drafts/{did}")
def del_draft(did: str):
    store.delete_draft(did)
    return {"ok": True}
