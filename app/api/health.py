"""What the studio knows about itself: the health report and the log, for the person and for agents."""

from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

import logs
from services import health

router = APIRouter(tags=["service"])


@router.get(
    "/api/health",
    summary="Состояние студии: проверки ok/warn/fail с подсказкой, что делать, счётчики и последние ошибки",
)
def health_report():
    return health.report()


@router.get("/readyz", include_in_schema=False)
def ready():
    """Readiness, unlike /healthz (liveness): 503 while any check fails. Public, so it says only which checks."""
    failing = [c["name"] for c in health.report()["checks"] if c["status"] == "fail"]
    return JSONResponse({"ready": not failing, "failing": failing}, status_code=503 if failing else 200)


@router.get("/api/logs", summary="Журнал студии (строки JSON), новые в конце; без токена и текста постов")
def read_logs(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO",
    limit: int = Query(100, ge=1, le=1000),
    since: float | None = Query(None, description="unix-время: только строки не старше"),
    logger: str = Query("", description="начало имени, например rich-studio.telegram"),
    q: str = Query("", description="подстрока в строке журнала"),
):
    return {"file": str(logs.current_file() or ""), "lines": logs.read(level, limit, since, logger, q)}
