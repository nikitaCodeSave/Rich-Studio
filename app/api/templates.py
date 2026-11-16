"""Post templates: a post with {{key}} fields that fills into a new draft."""

from fastapi import APIRouter

from models import TemplateFillReq, TemplateReq, TemplateUpdateReq
from services import templates

router = APIRouter(tags=["templates"])


@router.get("/api/templates", summary="Шаблоны постов (без самого поста), последние сверху")
def list_templates():
    return templates.list_templates()


@router.get("/api/templates/{tid}", summary="Шаблон целиком: пост с {{key}}, поля, version")
def get_template(tid: str):
    return templates.get(tid)


@router.post("/api/templates", summary="Новый шаблон: пост с {{key}} и описание его полей")
def create_template(req: TemplateReq):
    """Значение поля всегда текст: в HTML экранируется, в блоках остаётся строкой. 422, если {{key}} стоит там,
    где значение стало бы разметкой: в теге, в ключе JSON, в атрибуте без кавычек, в Markdown. Ссылку (href/src
    в HTML, ключ url в JSON: ссылка в блоке, кнопка) подставляет только поле type url целиком, без кусков вроде
    https://site/{{slug}}, иначе тоже 422."""
    return templates.save(req.title, req.post.model_dump(), [f.model_dump() for f in req.fields])


@router.put("/api/templates/{tid}", summary="Сохранить шаблон; со старой version — 409")
def put_template(tid: str, req: TemplateUpdateReq):
    return templates.save(req.title, req.post.model_dump(), [f.model_dump() for f in req.fields], tid, req.version)


@router.delete("/api/templates/{tid}", summary="Удалить шаблон; со старой version — 409. Черновики из него остаются")
def delete_template(tid: str, version: int):
    templates.delete(tid, version)
    return {"ok": True}


@router.post(
    "/api/templates/{tid}/draft",
    summary="Заполнить шаблон: новый черновик без каналов; со старой version — 409, больше 1 МБ — 413",
)
def draft_from_template(tid: str, req: TemplateFillReq):
    """Поле без значения берёт default; пустое обязательное поле и ссылка не той схемы — 422. Ничего не публикует."""
    return templates.fill(tid, req.version, req.values, req.title)
