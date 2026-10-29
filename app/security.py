"""Request guard for a service that is meant to be reached only from this computer.

- Host check against DNS rebinding;
- CSRF: browsers mark cross-site requests, curl and scripts send neither header; Origin must match Host with the port;
- strict Content-Type for writes;
- who is asking: a named token (Authorization: Bearer or X-API-Key), STUDIO_API_KEY, or the editor's session
  cookie; with neither a key nor tokens set up the studio is open, as a local tool on 127.0.0.1;
- the level a request needs: GET reads, other methods write drafts; routes that post to a channel and /api/raw
  ask for more themselves (api.deps.need), MCP tools check it per tool;
- CSP and other headers on the editor and preview pages.
"""

import contextvars
import secrets
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

import settings
import store

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
    "media-src 'self' blob: https:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)
# reachable without the API key
PUBLIC_PATHS = {"/", "/favicon.ico", "/healthz", "/readyz"}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
SESSION_COOKIE = "rs_session"
LEVELS = ("read", "draft", "publish", "admin")


@dataclass(frozen=True)
class Principal:
    """Who makes the request. name is the token's name; None for STUDIO_API_KEY and for an open studio."""

    name: str | None
    level: str

    def can(self, level: str) -> bool:
        return LEVELS.index(self.level) >= LEVELS.index(level)


OPEN = Principal(None, "admin")
# set by guard for the request; MCP tools run in the request's context and read it too
_current: contextvars.ContextVar[Principal] = contextvars.ContextVar("principal", default=OPEN)


def current() -> Principal:
    return _current.get()


def auth_on() -> bool:
    return bool(settings.get().api_key) or store.any_tokens()


def by_secret(secret: str) -> Principal | None:
    """The principal a key or token stands for, or None."""
    key = settings.get().api_key
    if key and secrets.compare_digest(secret.encode(), key.encode()):  # str needs ASCII, bytes take any key
        return Principal(None, "admin")
    t = store.token_by_secret(secret)
    return Principal(t["name"], t["level"]) if t else None


def _credentials(request: Request) -> Principal | None:
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    # an empty Bearer falls back to X-API-Key: .mcp.json sends both, filled from STUDIO_TOKEN and STUDIO_API_KEY
    secret = bearer or request.headers.get("x-api-key", "")
    if secret:
        return by_secret(secret)
    session = request.cookies.get(SESSION_COOKIE)
    if session:
        s = store.session_by_secret(session)
        if s and s["token_name"]:
            return Principal(s["token_name"], s["level"])
        # a STUDIO_API_KEY session lives only while that same key does
        key = settings.get().api_key
        if s and key and s["key_hash"] and secrets.compare_digest(s["key_hash"], store.hash_secret(key)):
            return OPEN
    return None


def host_ok(host: str) -> bool:
    allowed = settings.get().allowed_hosts
    host = (host or "").lower()
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    return name in allowed or host in allowed


def _same_origin(origin: str, host: str) -> bool:
    """Origin names the Host the request came to, port included: a page on another port of localhost is another site."""
    scheme, _, authority = origin.lower().partition("://")
    default = {"http": ":80", "https": ":443"}.get(scheme, "")
    return authority.removesuffix(default) == host.lower().removesuffix(default)


def _cross_site(request: Request) -> bool:
    origin = request.headers.get("origin")
    site = request.headers.get("sec-fetch-site")
    if site in ("cross-site", "same-site") or origin == "null":
        return True
    return origin is not None and origin != "" and not _same_origin(origin, request.headers.get("host", ""))


async def guard(request: Request, call_next):
    path = request.url.path
    if not host_ok(request.headers.get("host", "")):
        return JSONResponse({"detail": "неизвестный Host; добавьте его в ALLOWED_HOSTS"}, status_code=400)
    if request.method not in SAFE_METHODS:
        if _cross_site(request):
            return JSONResponse({"detail": "запрос с другого сайта отклонён"}, status_code=403)
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        allowed = "multipart/form-data" if path == "/api/media" else "application/json"
        if path.startswith("/api/") and ctype and ctype != allowed:
            return JSONResponse({"detail": f"ожидался {allowed}"}, status_code=415)
    who = OPEN
    # logging in and out of the editor (POST, DELETE /api/session) needs no token yet
    public = path in PUBLIC_PATHS or path.startswith("/static/") or (path == "/api/session" and request.method != "GET")
    if not public and auth_on():
        found = _credentials(request)
        if found is None:
            return JSONResponse(
                {"detail": "нужен токен: заголовок Authorization: Bearer <токен> или X-API-Key"}, status_code=401
            )
        who = found
        # /mcp checks the level per tool: a read token may call the read-only tools over POST
        need = "read" if request.method in SAFE_METHODS or path == "/mcp" else "draft"
        if not who.can(need):
            return JSONResponse({"detail": f"у токена {who.name} нет права {need}"}, status_code=403)
    request.state.principal = who
    reset = _current.set(who)
    try:
        response = await call_next(request)
    finally:
        _current.reset(reset)
    if path == "/" or path.startswith("/render/") or path == "/api/render":
        response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
