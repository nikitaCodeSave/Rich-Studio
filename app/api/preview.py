"""Validation and the Telegram-like preview page."""

import html
import json
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

import store
from errors import NotFound
from models import Post
from services import publisher

from .deps import ui as from_ui

router = APIRouter()
TEMPLATE = Path(__file__).resolve().parent.parent / "static" / "render.html"
# the body of /api/validate is the post itself: chat_ids put there is the post's field, not where it goes
CHATS_IN_QUERY = "у /api/validate — query-параметр: /api/validate?chat_ids=@канал, можно несколько раз"


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
def validate(
    post: Post,
    ui: bool = Depends(from_ui),
    chat_ids: Annotated[
        list[str] | None,
        Query(
            description="Куда пойдёт пост (можно несколько): предупреждения о том, что Telegram не примет в этих "
            "чатах (web_app в канале…). Без параметра — канал по умолчанию",
        ),
    ] = None,
):
    """Тело — сам пост, каналы — query-параметр `chat_ids`; `chat_id`/`chat_ids` в посте — ошибка. Для проверенных
    каналов (`POST /api/channels`) ещё предупреждения о том, что Telegram в чате такого типа не примет (кнопка
    `web_app` в канале…). Поле верхнего уровня, которого у поста нет (`disable_notification` вне `options`), —
    предупреждение: в Telegram оно не уходит."""
    return publisher.check(post.model_dump(), publisher.checked_targets(chat_ids), CHATS_IN_QUERY, ui)


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
