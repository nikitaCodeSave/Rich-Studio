"""Rich Studio: local editor, preview renderer and API for Telegram rich posts.

This module only assembles the app. Routes live in api/, logic in services/,
Telegram transport in telegram/, storage in store/, the Bot API spec in spec/.
"""

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings

import mcp_tools
import settings
import spec
import store
import telegram as tg
from api import backup as backup_api
from api import drafts, media, posts, preview, raw, service, specs
from errors import StudioError
from security import guard
from services import backup, scheduler, updates

STATIC = Path(__file__).parent / "static"
SPEC_CHECK_INTERVAL = 1800  # seconds; Spec.refresh itself skips while the copy is fresh

log = logging.getLogger("rich-studio")


async def _spec_loop():
    while True:
        try:
            await spec.current.refresh()
        except Exception:
            log.exception("spec refresh failed")
        await asyncio.sleep(SPEC_CHECK_INTERVAL)


@contextlib.asynccontextmanager
async def lifespan(app):
    cfg = settings.get()
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
        if cfg.backup_keep > 0:
            tasks.append(asyncio.create_task(backup.loop()))
        if cfg.updates_on:
            tasks.append(asyncio.create_task(updates.loop()))
    async with app.state.mcp.session_manager.run():
        yield
    for t in tasks:
        t.cancel()


async def _studio_error(request: Request, e: Exception):
    assert isinstance(e, StudioError)  # noqa: S101 - registered for StudioError only
    return JSONResponse({"detail": e.detail}, status_code=e.status)


async def _telegram_error(request: Request, e: Exception):
    assert isinstance(e, tg.TelegramError)  # noqa: S101
    body = {"detail": f"Telegram: {e.description}", "error_code": e.code}
    if e.retry_after:  # flood wait longer than the studio waits itself: the caller decides when to try again
        body["retry_after"] = e.retry_after
    return JSONResponse(body, status_code=502)


async def _no_answer(request: Request, e: Exception):
    return JSONResponse({"detail": f"{e}. Действие могло выполниться, проверьте в Telegram"}, status_code=502)


def create_app(cfg: settings.Settings | None = None) -> FastAPI:
    if cfg is not None:
        settings.use(cfg)
    app = FastAPI(
        title="Rich Studio API",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "Локальный сервис для rich-постов Telegram (sendRichMessage). Поле `post` везде имеет вид "
            "`{rich_message: InputRichMessage, reply_markup?: InlineKeyboardMarkup, options?: {...}}` и уходит в "
            "Bot API без изменений. Медиа из загрузок указывайте как `upload:<имя файла>`. "
            "Спецификация Bot API: GET /api/spec."
        ),
    )
    app.middleware("http")(guard)
    app.add_exception_handler(StudioError, _studio_error)
    app.add_exception_handler(tg.TelegramError, _telegram_error)
    app.add_exception_handler(tg.NoAnswer, _no_answer)
    for module in (service, specs, preview, drafts, posts, raw, media, backup_api):
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
