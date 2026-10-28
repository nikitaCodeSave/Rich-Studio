"""Uploads for posts (upload:<name>)."""

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse

import settings
from errors import StudioError
from models import FetchReq
from services import media

router = APIRouter()


@router.get("/api/media", tags=["media"], summary="Загруженные файлы; used — нужен ли файл черновику или посту")
def media_list():
    return media.list_files()


@router.post("/api/media", tags=["media"], summary="Загрузить файл; в посте он указывается как upload:<name>")
async def media_upload(file: UploadFile = File(...)):
    limit = int(settings.get().max_upload_mb * 1024 * 1024)
    name = media.free_name(file.filename)
    path = media.media_dir() / name
    size = 0
    with path.open("wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                break
            f.write(chunk)
    if size > limit:  # Telegram would reject it at publish time; say so now
        path.unlink(missing_ok=True)
        raise StudioError(
            f"файл больше {settings.get().max_upload_mb:g} МБ: столько Telegram не примет "
            "(для больших файлов нужен свой сервер Bot API, см. TG_API_BASE и MAX_UPLOAD_MB)",
            status=413,
        )
    return media.describe(name)


@router.post("/api/media/fetch", tags=["media"], summary="Скачать файл по ссылке в загрузки; дальше он upload:<name>")
async def media_fetch(req: FetchReq):
    """Ходит только на публичные адреса и проверяет каждый редирект; локальная сеть: `MEDIA_FETCH_PRIVATE=1`."""
    return await media.fetch(req.url, req.filename)


@router.post(
    "/api/media/cleanup",
    tags=["media"],
    summary="Удалить файлы, которые не упоминает ни один черновик и ни один живой пост",
)
def media_cleanup():
    return {"deleted": media.delete_unused()}


@router.delete(
    "/api/media/{name}",
    tags=["media"],
    summary="Удалить файл; если он нужен отложенному посту или черновику, 409 (force=true удаляет всё равно)",
)
def media_delete(name: str, force: bool = False):
    media.delete(name, force)
    return {"ok": True}


@router.get("/media/{name}", include_in_schema=False)
def media_file(name: str):
    p = media.path_of(name)
    if p.suffix.lower() in media.INLINE:
        r = FileResponse(p)
    else:  # never render uploaded html/svg/etc. on the studio origin
        r = FileResponse(p, media_type="application/octet-stream", filename=p.name)
    r.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return r
