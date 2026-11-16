"""Request bodies. Descriptions are in Russian: they show up in /docs for people and agents."""

from typing import Any, Literal

from pydantic import BaseModel, Field


def bot_api(*types: str) -> dict:
    """What a field of the post holds in Bot API terms (`bot_api` in OpenAPI): a type of the spec, or PostOptions and
    PollAfter, the parameters of sendRichMessage and sendPoll the studio lets you set (api/specs.py). The editor's
    JSON tab suggests the fields from it."""
    return {"bot_api": list(types)}


class Post(BaseModel):
    rich_message: dict = Field(
        ...,
        description="InputRichMessage: ровно одно из html / markdown / blocks, плюс media",
        json_schema_extra=bot_api("InputRichMessage"),
    )
    reply_markup: dict | None = Field(
        None, description="InlineKeyboardMarkup под постом", json_schema_extra=bot_api("InlineKeyboardMarkup")
    )
    options: dict = Field(
        default_factory=dict,
        description="Любые другие параметры sendRichMessage: disable_notification, protect_content…",
        json_schema_extra=bot_api("PostOptions"),
    )
    pin: bool = Field(False, description="Закрепить пост после публикации", json_schema_extra=bot_api("Boolean"))
    comment: dict | None = Field(
        None,
        description="InputRichMessage первого комментария: студия ответит им на пересылку поста в группу обсуждения",
        json_schema_extra=bot_api("InputRichMessage"),
    )
    callbacks: dict[str, Any] = Field(
        default_factory=dict,
        description="Ответы на callback-кнопки: {callback_data: текст или объект}. Объект — параметры "
        "answerCallbackQuery (text, show_alert, url, cache_time) и по желанию reply: параметры sendRichMessage для "
        "rich-ответа, который видит только нажавший (Bot API 10.4): {rich_message: InputRichMessage, "
        "ephemeral_message_parameters?: {replace_callback_query_message: true — вместо поста}}. Студия добавит "
        "callback_query_id и всё равно ответит answerCallbackQuery; ответ проверяется как пост и не повторяется. "
        "Работает и под постом канала; замена поста — только у нажавшего, без кнопок поста и до перезапуска Telegram "
        "(эфемерные сообщения могут исчезать и сами)",
        json_schema_extra=bot_api("Object"),
    )
    poll_after: dict | None = Field(
        None,
        description="Параметры sendPoll (question, options…): опрос уйдёт в тот же чат сразу после поста; "
        "тема форума и disable_notification берутся из options поста, если в опросе их нет",
        json_schema_extra=bot_api("PollAfter"),
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


class MediaPostReq(BaseModel):
    kind: Literal["live_photo", "paid_media"] = Field(
        ..., description="live_photo — sendLivePhoto, paid_media — sendPaidMedia (фото и видео за звёзды)"
    )
    params: dict = Field(
        ...,
        description="Параметры метода без chat_id, как в Bot API. live_photo: {live_photo: видео, photo: кадр, "
        "caption?}; paid_media: {star_count, media: [{type: photo|video|live_photo, media, photo?}], caption?}. "
        "Файлы из загрузок — upload:<имя>",
    )
    chat_id: str | int | None = None
    chat_ids: list[str | int] | None = None
    at: int | str | None = Field(None, description="Отложить: unix или ISO 8601 с часовым поясом")
    pin: bool = False


class ButtonsReq(BaseModel):
    reply_markup: dict | None = Field(None, description="Новые кнопки; null убирает их")
    callbacks: dict[str, Any] | None = Field(
        None, description="Ответы на кнопки, как callbacks поста (с rich-ответом reply); null — прежние"
    )


class RescheduleReq(BaseModel):
    at: int | str = Field(..., description="Новое время: unix или ISO 8601 с часовым поясом")


class PinReq(BaseModel):
    disable_notification: bool = True


class FetchReq(BaseModel):
    url: str = Field(..., description="Ссылка http(s) на файл")
    filename: str | None = Field(None, description="Имя файла; по умолчанию из ссылки")


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


class InviteLinkLimits(BaseModel):
    name: str | None = Field(
        None, description="Название ссылки до 32 символов: откуда придут люди (видно только админам)"
    )
    expire_date: int | str | None = Field(None, description="Когда ссылка перестанет работать: unix или ISO 8601")
    member_limit: int | None = Field(None, description="Сколько человек смогут вступить по ссылке (1–99999)")
    creates_join_request: bool = Field(
        False, description="Вступление по заявке: её принимает админ (тогда без member_limit)"
    )


class InviteLinkReq(InviteLinkLimits):
    chat_id: str | int | None = Field(None, description="@канал или числовой id; по умолчанию TG_DEFAULT_CHAT")
    subscription_price: int | None = Field(
        None,
        description="Платная подписка: звёзд в месяц (1–10000) через createChatSubscriptionInviteLink; "
        "тогда без срока, предела и заявок",
    )


class InviteLinkEditReq(InviteLinkLimits):
    """Непереданное поле остаётся как было; снять: name "", expire_date 0, member_limit 0, creates_join_request false."""

    invite_link: str = Field(..., description="Ссылка из /api/invite-links: https://t.me/+…")
    creates_join_request: bool | None = Field(  # type: ignore[assignment]
        None, description="Вступление по заявке: true включить, false снять, не передано — как было"
    )


class InviteLinkRef(BaseModel):
    invite_link: str = Field(..., description="Ссылка из /api/invite-links: https://t.me/+…")


class ApproveSuggestedReq(BaseModel):
    send_date: int | str | None = Field(
        default=None,
        description="Когда опубликовать: unix или ISO 8601; только если автор не выбрал время сам. Пусто — сразу",
    )


class DeclineSuggestedReq(BaseModel):
    comment: str | None = Field(default=None, description="Комментарий автору предложения, до 128 символов")


class TemplateField(BaseModel):
    key: str = Field(
        ..., description="Имя поля: в посте стоит как {{key}}; латинская буква или _, дальше буквы, цифры, _"
    )
    label: str = Field("", description="Подпись в форме заполнения; пусто — имя поля")
    type: Literal["text", "multiline", "url"] = Field(
        "text",
        description="text — строка, multiline — несколько строк, url — ссылка http(s)/tg/mailto/tel, #якорь, /путь "
        "или файл upload:имя (только такое поле ставится в href и src; файл — в медиа поста, не в атрибут HTML)",
    )
    required: bool = Field(True, description="Без значения черновик не создаётся (поле без значения берёт default)")
    default: str = Field("", description="Значение, если поле не заполнили")


class TemplateReq(BaseModel):
    title: str = Field(..., description="Название шаблона (рубрики)")
    post: Post = Field(
        ...,
        description="Пост, как для отправки: {{key}} стоит только в строковых значениях (в HTML — в тексте или в "
        "атрибуте в кавычках, в href/src и в JSON-ключе url — только поле типа url целиком); в Markdown поля пока "
        "не подставляются",
    )
    fields: list[TemplateField] = Field(default_factory=list, description="Поля шаблона: каждое стоит в посте")


class TemplateUpdateReq(TemplateReq):
    version: int = Field(..., description="version прочитанного шаблона: если его сохранили после этого, ответ 409")


class TemplateFillReq(BaseModel):
    version: int = Field(..., description="version шаблона, по которому заполнялась форма: иначе 409")
    values: dict[str, str] = Field(default_factory=dict, description="Значения по key; поле без значения берёт default")
    title: str = Field("", description="Название нового черновика; пусто — название шаблона")
