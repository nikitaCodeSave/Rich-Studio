"""Validation and the Telegram-like preview page."""

import html
import json
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

import store
from errors import NotFound
from models import Post
from services import publisher

router = APIRouter()
TEMPLATE = Path(__file__).resolve().parent.parent / "static" / "render.html"


def render_page(post: dict, theme: str = "auto", title: str = "Предпросмотр") -> str:
    if theme not in ("auto", "light", "dark"):
        theme = "auto"
    payload = (
        json.dumps(post, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )
    values = {"__TITLE__": html.escape(title), "__THEME__": theme, "__PAYLOAD__": payload}
    # single pass, so a value can never be re-substituted
    return re.sub(r"__(TITLE|THEME|PAYLOAD)__", lambda m: values[m.group(0)], TEMPLATE.read_text("utf-8"))


@router.post("/api/validate", tags=["posts"], summary="Проверить пост по спецификации: ошибки, предупреждения, символы")
def validate(post: Post):
    return publisher.check(post.model_dump())


@router.post(
    "/api/render",
    tags=["preview"],
    response_class=HTMLResponse,
    summary="Отрисовать пост как в Telegram: отдельная HTML-страница",
)
def render(post: Post, theme: str = "auto"):
    return render_page(post.model_dump(), theme)


@router.get(
    "/render/draft/{did}",
    response_class=HTMLResponse,
    tags=["preview"],
    summary="Страница предпросмотра черновика (preview_path из save_draft)",
)
def render_draft(did: str, theme: str = "auto"):
    d = store.get_draft(did)
    if not d:
        raise NotFound("Not Found")
    return render_page(d["payload"], theme, d["title"] or "Черновик")
