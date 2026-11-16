"""The Bot API spec the editor is built from."""

from fastapi import APIRouter
from pydantic.fields import FieldInfo

import models
import spec
from errors import NotFound
from services import publisher
from spec.source import field_summary

router = APIRouter(tags=["spec"])

# PostOptions and PollAfter in models.Post (`bot_api` of its fields): the parameters of sendRichMessage and sendPoll
# the studio lets you set
METHOD_TYPES = {
    "PostOptions": ("sendRichMessage", publisher.RESERVED_PARAMS),
    "PollAfter": ("sendPoll", {"chat_id"}),
}


def holds(field: FieldInfo) -> list[str]:
    """`bot_api` of a field of models.Post; a field without it still shows, with nothing to suggest inside."""
    extra = field.json_schema_extra
    types = extra.get("bot_api") if isinstance(extra, dict) else None
    return [str(t) for t in types] if isinstance(types, list) else []


def post_envelope() -> dict:
    """The post as the editor's JSON tab shows it: its own fields (models.Post, with what each holds in `bot_api`),
    then the method parameters behind options and poll_after. The types they lead to are in the summary's `types`."""
    types = {
        "Post": {
            "description": "Пост студии: rich_message и то, что студия делает с ним при отправке",
            "fields": [
                {
                    "name": name,
                    "types": holds(f),
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
    "/api/spec",
    summary="Сводка спецификации для редактора (rich-типы, методы, изменения, поля поста для подсказок, в каких чатах "
    "что не работает)",
)
def get_spec():
    post = post_envelope()
    roots = [t for shape in post["types"].values() for f in shape["fields"] for t in f["types"]]
    # chat_rules: where Telegram refuses a field or a type, read from the descriptions (spec/chats.py); the editor
    # marks what does not work in the chats the post goes to
    return {**spec.current.summary(roots), "post": post, "chat_rules": spec.chats.summary(spec.current)}


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
