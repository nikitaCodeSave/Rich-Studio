"""Download everything the studio keeps."""

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from services import backup

router = APIRouter(tags=["service"])


@router.get(
    "/api/export",
    summary="Скачать бэкап: zip с базой (черновики, посты, история) и загруженными файлами",
    response_class=FileResponse,
)
def export():
    path = backup.export_zip()
    return FileResponse(
        path, media_type="application/zip", filename=backup.export_name(), background=BackgroundTask(path.unlink)
    )
