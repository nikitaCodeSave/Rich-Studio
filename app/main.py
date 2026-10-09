"""Rich Studio: local editor, preview renderer and API for Telegram rich posts.

This module only assembles the app. Routes live in api/, logic in services/,
Telegram transport in telegram/, storage in store/, the Bot API spec in spec/.
"""

import asyncio
import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

import logs
import mcp_tools
import metrics
import settings
import spec
import store
import telegram as tg
from api import backup as backup_api
from api import channels, drafts, health, media, posts, preview, raw, service, specs
from errors import StudioError
from security import guard
from services import backup, housekeeping, scheduler, updates

STATIC = Path(__file__).parent / "static"
SPEC_CHECK_INTERVAL = 1800  # seconds; Spec.refresh itself skips while the copy is fresh

log = logging.getLogger("rich-studio")
access = logging.getLogger("rich-studio.http")
# polled by Docker and by agents; a line per call would bury everything else
PATH_LOGGED = 200
NOT_LOGGED = ("/healthz", "/readyz", "/api/health", "/api/logs", "/static/", "/favicon.ico")


async def _spec_loop():
    while True:
        error = None
        with logs.bind(logs.new_id("spec-")):
            try:
                await spec.current.refresh()
            except Exception as e:
                log.exception("spec refresh failed")
                error = f"{type(e).__name__}: {e}"
        metrics.beat("spec", SPEC_CHECK_INTERVAL, error)
        await asyncio.sleep(SPEC_CHECK_INTERVAL)


def _printable(path: str) -> str:
    """The path as it goes into the log: anyone can send one, even without the key. Short, one line, no escape codes."""
    path = path[:PATH_LOGGED]
    return path if path.isprintable() else "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in path)


def _route(request: Request) -> str:
    """The route template (/api/posts/{pid}/edit), so latencies group by endpoint, not by id."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "other"


async def _observe(request: Request, call_next):
    """Request id on every line of the request and in X-Request-ID; one access line: method, path, status, time,
    who called (X-Studio-Client). No query, no body."""
    rid = logs.incoming_id(request.headers.get("x-request-id"), request.headers.get("traceparent"))
    started = time.monotonic()
    status = 500
    with logs.bind(rid):
        try:
            try:
                response = await call_next(request)
            except Exception:  # logged here, inside the request id; uvicorn would log it after the id is gone
                log.exception("unhandled error in %s %s", request.method, _printable(request.url.path))
                response = JSONResponse({"detail": "внутренняя ошибка студии", "request_id": rid}, status_code=500)
            status = response.status_code
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            path = _printable(request.url.path)
            ms = int((time.monotonic() - started) * 1000)
            if not path.startswith(("/static/", "/media/")):
                metrics.http_observe(request.method, _route(request), status, ms)
            if not path.startswith(NOT_LOGGED):
                access.log(
                    logging.WARNING if status >= 500 else logging.INFO,
                    "%s %s %s",
                    request.method,
                    path,
                    status,
                    extra={
                        "event": "http",
                        "method": request.method,
                        "path": path,
                        "route": _route(request),
                        "status": status,
                        "ms": ms,
                        "client": request.headers.get("x-studio-client") or None,
                    },
                )


@contextlib.asynccontextmanager
async def lifespan(app):
    cfg = settings.get()
    logs.setup()
    store.init()
    stale = store.recover_stale_sending()
    if stale:
        log.warning("%d posts were being sent when the service stopped; marked failed", stale)
    cfg.media_dir.mkdir(parents=True, exist_ok=True)
    spec.current.load_initial()
    updates.reset_status()
    tasks = []
    if cfg.background:
        tasks = [asyncio.create_task(_spec_loop()), asyncio.create_task(scheduler.loop())]
        tasks.append(asyncio.create_task(housekeeping.loop()))
        if cfg.backup_keep > 0:
            tasks.append(asyncio.create_task(backup.loop()))
        if cfg.updates_on:
            tasks.append(asyncio.create_task(updates.loop()))
    log.info("studio started", extra={"event": "start", "spec": spec.current.version, "background": cfg.background})
    async with app.state.mcp.session_manager.run():
        yield
    for t in tasks:
        t.cancel()
    log.info("studio stopped", extra={"event": "stop"})
    logs.flush()


async def _studio_error(request: Request, e: Exception):
    assert isinstance(e, StudioError)  # noqa: S101 - registered for StudioError only
    return JSONResponse({"detail": e.detail}, status_code=e.status)


async def _telegram_error(request: Request, e: Exception):
    assert isinstance(e, tg.TelegramError)  # noqa: S101
    body = {"detail": f"Telegram: {e.description}", "error_code": e.code}
    if e.retry_after:  # flood wait longer than the studio waits itself: the caller decides when to try again
        body["retry_after"] = e.retry_after
    # 400: Telegram refused this request (a bad button link, no rights in the topic...), nothing went out and
    # the same request will fail again; anything else is Telegram or the way to it failing
    return JSONResponse(body, status_code=422 if e.code == 400 else 502)


async def _no_answer(request: Request, e: Exception):
    return JSONResponse({"detail": f"{e}. Действие могло выполниться, проверьте в Telegram"}, status_code=502)


def create_app(cfg: settings.Settings | None = None) -> FastAPI:
    if cfg is not None:
        settings.use(cfg)
    app = FastAPI(
        title="Rich Studio API",
        version=settings.VERSION,
        lifespan=lifespan,
        description=(
            "Локальный сервис для rich-постов Telegram (sendRichMessage). Поле `post` везде имеет вид "
            "`{rich_message: InputRichMessage, reply_markup?: InlineKeyboardMarkup, options?: {...}}` и уходит в "
            "Bot API без изменений. Медиа из загрузок указывайте как `upload:<имя файла>`. "
            "Спецификация Bot API: GET /api/spec."
        ),
    )
    app.middleware("http")(guard)
    app.middleware("http")(_observe)  # added last = runs first: also sees what the guard refused
    app.add_exception_handler(StudioError, _studio_error)
    app.add_exception_handler(tg.TelegramError, _telegram_error)
    app.add_exception_handler(tg.NoAnswer, _no_answer)
    for module in (service, health, specs, preview, drafts, channels, posts, raw, media, backup_api):
        app.include_router(module.router)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():  # browsers ask on every page; a 404 shows up as a console error in the preview
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # MCP for agents at /mcp, behind the same guard (Host, CSRF, X-API-Key) as the REST API
    @app.get("/mcp", include_in_schema=False)
    def mcp_no_stream():  # stateless server: nothing to push, and an open GET stream would hang until shutdown
        return Response(status_code=405, headers={"Allow": "POST"})

    app.state.mcp = mcp_tools.build()
    mcp_app = app.state.mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),  # guard checks Host
    )
    app.router.routes.extend(mcp_app.routes)
    return app


app = create_app()
