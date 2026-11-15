"""The Bot API spec the editor is built from."""

from fastapi import APIRouter

import models
import spec
from errors import NotFound
from services import publisher
from spec.source import field_summary

router = APIRouter(tags=["spec"])

# What each field of a post (models.Post, the studio's own envelope) holds in the Bot API terms, for the JSON tab's
# completion. PostOptions and PollAfter are the parameters of sendRichMessage and sendPoll the studio lets you set.
POST_TYPES = {
    "rich_message": ["InputRichMessage"],
    "reply_markup": ["InlineKeyboardMarkup"],
    "options": ["PostOptions"],
    "pin": ["Boolean"],
    "comment": ["InputRichMessage"],
    "callbacks": ["Object"],
    "poll_after": ["PollAfter"],
}
METHOD_TYPES = {
    "PostOptions": ("sendRichMessage", publisher.RESERVED_PARAMS),
    "PollAfter": ("sendPoll", {"chat_id"}),
}


def post_envelope() -> dict:
    """The post as the editor's JSON tab shows it: its own fields, then the method parameters behind two of them.
    The types they lead to are in the summary's `types`."""
    types = {
        "Post": {
            "description": "Пост студии: rich_message и то, что студия делает с ним при отправке",
            "fields": [
                {
                    "name": name,
                    "types": POST_TYPES[name],
                    "required": f.is_required(),
                    "description": f.description or "",
                    "enum": None,
                }
                for name, f in models.Post.model_fields.items()
            ],
        }
    }
    for name, (method, omit) in METHOD_TYPES.items():
        m = spec.current.methods.get(method) or {}
        types[name] = {
            "description": f"Параметры {method}, кроме {', '.join(sorted(omit))}: их задаёт студия",
            "fields": [field_summary(f) for f in m.get("fields", []) if f["name"] not in omit],
        }
    return {"root": "Post", "types": types}


@router.get(
    "/api/spec", summary="Сводка спецификации для редактора (rich-типы, методы, изменения, поля поста для подсказок)"
)
def get_spec():
    post = post_envelope()
    roots = [t for shape in post["types"].values() for f in shape["fields"] for t in f["types"]]
    return {**spec.current.summary(roots), "post": post}


@router.get("/api/spec/raw", summary="Полный api.json")
def get_spec_raw():
    return spec.current.data


@router.get("/api/spec/type/{name}", summary="Точные поля типа или метода Bot API из текущей спецификации")
def get_spec_type(name: str):
    t = spec.current.types.get(name) or spec.current.methods.get(name)
    if not t:
        raise NotFound(f"{name} нет в спецификации {spec.current.version}")
    return t


@router.post("/api/spec/refresh", summary="Скачать свежую спецификацию и показать, что изменилось")
async def refresh_spec():
    return await spec.current.refresh(force=True)
