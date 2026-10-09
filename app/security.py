"""Request guard for a service that is meant to be reached only from this computer.

- Host check against DNS rebinding;
- CSRF: browsers mark cross-site requests, curl and scripts send neither header;
- strict Content-Type for writes;
- optional X-API-Key (or the rs_key cookie set by the editor);
- CSP and other headers on the editor and preview pages.
"""

import secrets

from fastapi import Request
from fastapi.responses import JSONResponse

import settings

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
    "media-src 'self' blob: https:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)
# reachable without the API key
PUBLIC_PATHS = {"/", "/favicon.ico", "/healthz"}
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def host_ok(host: str) -> bool:
    allowed = settings.get().allowed_hosts
    host = (host or "").lower()
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    return name in allowed or host in allowed


def _cross_site(request: Request) -> bool:
    origin = request.headers.get("origin")
    site = request.headers.get("sec-fetch-site")
    if site in ("cross-site", "same-site") or origin == "null":
        return True
    return origin is not None and origin != "" and not host_ok(origin.split("://", 1)[-1])


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
    api_key = settings.get().api_key
    if api_key and not (path in PUBLIC_PATHS or path.startswith("/static/")):
        key = request.headers.get("x-api-key") or request.cookies.get("rs_key")
        if not key or not secrets.compare_digest(key, api_key):
            return JSONResponse({"detail": "нужен заголовок X-API-Key"}, status_code=401)
    response = await call_next(request)
    if path == "/" or path.startswith("/render/") or path == "/api/render":
        response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
