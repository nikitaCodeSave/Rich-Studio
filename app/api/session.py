"""Editor login: the key or a token is exchanged for an HttpOnly session cookie, so no script ever holds it."""

import secrets

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

import security
import settings
import store
from errors import StudioError

router = APIRouter(tags=["service"])


class SessionReq(BaseModel):
    key: str = Field(min_length=1, max_length=200, description="STUDIO_API_KEY или токен")


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@router.post("/api/session", summary="Войти в редактор: ключ или токен меняется на cookie сессии")
def login(req: SessionReq, request: Request, response: Response):
    """Cookie `rs_session`: HttpOnly, SameSite=Strict, Secure по HTTPS, живёт `SESSION_DAYS` дней. Её же берут
    страницы предпросмотра и картинки из загрузок."""
    who = security.by_secret(req.key)
    if who is None:
        raise StudioError("ключ или токен не подошёл", status=401)
    secret = secrets.token_urlsafe(32)
    ttl = settings.get().session_days * 86400
    store.add_session(secret, who.name, ttl)
    response.set_cookie(
        security.SESSION_COOKIE,
        secret,
        max_age=int(ttl),
        httponly=True,
        secure=_https(request),
        samesite="strict",
        path="/",
    )
    return {"name": who.name, "level": who.level}


@router.get("/api/session", summary="Кто я: имя токена и его уровень")
def whoami():
    who = security.current()
    return {"name": who.name, "level": who.level, "auth": security.auth_on()}


@router.delete("/api/session", summary="Выйти из редактора: сессия и её cookie удаляются")
def logout(request: Request, response: Response):
    secret = request.cookies.get(security.SESSION_COOKIE)
    if secret:
        store.drop_session(secret)
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}
