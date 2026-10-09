"""Drafts CRUD."""

from fastapi import APIRouter

import store
from errors import NotFound
from models import DraftReq

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
    return store.save_draft(None, req.title, req.post)


@router.put("/api/drafts/{did}")
def put_draft(did: str, req: DraftReq):
    return store.save_draft(did, req.title, req.post)


@router.delete("/api/drafts/{did}")
def del_draft(did: str):
    store.delete_draft(did)
    return {"ok": True}
