"""The Bot API spec the editor is built from."""

from fastapi import APIRouter

import spec
from errors import NotFound

router = APIRouter(tags=["spec"])


@router.get("/api/spec", summary="Сводка спецификации для редактора (rich-типы, методы, изменения)")
def get_spec():
    return spec.current.summary()


@router.get("/api/spec/raw", summary="Полный api.json")
def get_spec_raw():
    return spec.current.data


@router.get("/api/spec/type/{name}")
def get_spec_type(name: str):
    t = spec.current.types.get(name) or spec.current.methods.get(name)
    if not t:
        raise NotFound(f"{name} нет в спецификации {spec.current.version}")
    return t


@router.post("/api/spec/refresh", summary="Скачать свежую спецификацию и показать, что изменилось")
async def refresh_spec():
    return await spec.current.refresh(force=True)
