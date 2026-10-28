"""Service status: health, configuration, incoming updates."""

from fastapi import APIRouter

import settings
import spec
from services import updates

router = APIRouter(tags=["service"])


@router.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


@router.get(
    "/api/config",
    summary="Что нужно редактору: канал по умолчанию, чат для «Мне в личку», задан ли токен, версия спецификации",
)
def config():
    cfg = settings.get()
    return {
        "default_chat": cfg.default_chat,
        "preview_chat": cfg.preview_chat,
        "token_set": bool(cfg.bot_token),
        "api_base": cfg.api_base,
        "spec_version": spec.current.version,
        "auth": bool(cfg.api_key),
        "updates": updates.STATUS.as_dict(),
    }


@router.get("/api/updates", summary="Приём событий от Telegram: нажатия, комментарии, реакции, опросы")
def updates_status():
    return updates.STATUS.as_dict()
