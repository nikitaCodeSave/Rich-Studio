"""Request bodies. Descriptions are in Russian: they show up in /docs for people and agents."""

from typing import Any

from pydantic import BaseModel, Field


class Post(BaseModel):
    rich_message: dict = Field(..., description="InputRichMessage: ровно одно из html / markdown / blocks, плюс media")
    reply_markup: dict | None = Field(None, description="InlineKeyboardMarkup под постом")
    options: dict = Field(
        default_factory=dict,
        description="Любые другие параметры sendRichMessage: disable_notification, protect_content…",
    )
    pin: bool = Field(False, description="Закрепить пост после публикации")
    comment: dict | None = Field(
        None,
        description="InputRichMessage первого комментария: студия ответит им на пересылку поста в группу обсуждения",
    )
    callbacks: dict[str, Any] = Field(
        default_factory=dict, description="Ответы на callback-кнопки: {callback_data: текст или {text, show_alert}}"
    )
    poll_after: dict | None = Field(
        None,
        description="Параметры sendPoll (question, options…): опрос уйдёт в тот же чат сразу после поста; "
        "тема форума и disable_notification берутся из options поста, если в опросе их нет",
    )


class SendReq(BaseModel):
    post: Post
    chat_id: str | int | None = Field(None, description="@канал или числовой id; по умолчанию TG_DEFAULT_CHAT")
    chat_ids: list[str | int] | None = Field(None, description="Несколько каналов сразу (вместо chat_id)")
    draft_id: str | None = None


class PublishReq(SendReq):
    dry_run: bool = Field(
        False, description="Только показать, какие запросы уйдут в Telegram: без отправки и без записи в историю"
    )
    as_new: bool = Field(
        False,
        description="draft_id — правка опубликованного поста, а вы хотите выпустить её копией новым постом "
        "(без флага 409: правку заменяют через /api/posts/{id}/edit)",
    )


class EditReq(BaseModel):
    post: Post
    chat_id: str | int | None = None
    message_id: int


class ScheduleReq(PublishReq):
    at: int | str = Field(..., description="unix-время или ISO 8601 с часовым поясом")


class PollReq(BaseModel):
    poll: dict = Field(
        ...,
        description="Параметры sendPoll без chat_id: question, options, members_only, "
        "country_codes, description, media, type=quiz…",
    )
    chat_id: str | int | None = None
    chat_ids: list[str | int] | None = None
    at: int | str | None = Field(None, description="Отложить: unix или ISO 8601 с часовым поясом")
    pin: bool = False


class ButtonsReq(BaseModel):
    reply_markup: dict | None = Field(None, description="Новые кнопки; null убирает их")
    callbacks: dict[str, Any] | None = None


class RescheduleReq(BaseModel):
    at: int | str = Field(..., description="Новое время: unix или ISO 8601 с часовым поясом")


class PinReq(BaseModel):
    disable_notification: bool = True


class DraftReq(BaseModel):
    title: str = ""
    post: dict
    version: int | None = Field(
        None,
        description="Версия черновика, на которой основана правка; если с тех пор его сохранили "
        "в другом месте, ответ 409 вместо перезаписи. Без поля черновик перезаписывается",
    )
    post_id: str | None = Field(
        None, description="id опубликованного или отложенного поста, который правится в этом черновике"
    )
    chat_ids: list[str | int] | None = Field(
        None,
        description="Каналы, для которых черновик: публикация из него без chat_id уходит туда. "
        "Без поля остаются прежние, [] — канал по умолчанию",
    )


class ChannelReq(BaseModel):
    chat_id: str | int = Field(..., description="@канал или числовой id")


class Topic(BaseModel):
    id: int = Field(..., description="message_thread_id темы: число в ссылке t.me/c/<канал>/<тема>/<сообщение>")
    name: str = ""


class TopicsReq(BaseModel):
    topics: list[Topic]
