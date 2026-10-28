"""Kinds of messages the studio sends: a rich post, a poll, a Live Photo, paid media (ADR 0013).

One record per kind says everything that differs between them: the Bot API method, where the stored payload keeps
the method's parameters, what a person calls it, whether it can be replaced, where its buttons live and what is
checked before it goes out. The kind is stored with the post (`posts.kind`); sending, the scheduler, edits, buttons
and the list of posts read it from there and never guess it from the payload's keys. The editor gets `describe()`
in /api/config.

A new kind of message: a record in KINDS (and its check below), a route or MCP tool that calls `posts.send_kind`,
a dialog in the editor. Nothing else branches on the kind.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import spec
from errors import Invalid, StudioError

RICH, POLL, LIVE_PHOTO, PAID_MEDIA = "rich", "poll", "live_photo", "paid_media"
STARS_MAX = 25000  # sendPaidMedia.star_count: 1-25000


def poll_error(poll, label: str = "опрос после поста") -> str | None:
    """What sendPoll would refuse before it is asked: a poll after a post goes out only once the post is sent,
    a scheduled poll only at its time."""
    if not isinstance(poll, dict):
        return f"{label}: ожидался объект sendPoll"
    if "chat_id" in poll:
        return f"{label}: chat_id задаётся не в опросе"
    if not str(poll.get("question") or "").strip():
        return f"{label}: пустой вопрос"
    opts = poll.get("options")
    texts = [o.get("text") if isinstance(o, dict) else o for o in opts] if isinstance(opts, list) else []
    if len([t for t in texts if str(t or "").strip()]) < 2:
        return f"{label}: нужно минимум два варианта"
    if len(texts) > 12:
        return f"{label}: больше 12 вариантов"
    if len(str(poll["question"])) > 300:
        return f"{label}: вопрос длиннее 300 символов"
    if poll.get("type") == "quiz" and not poll.get("correct_option_ids") and poll.get("correct_option_id") is None:
        return f"{label}: у викторины не отмечен правильный ответ"
    normalized = {**poll, "options": [o if isinstance(o, dict) else {"text": str(o)} for o in opts or []]}
    bad = spec.method_errors(spec.current, "sendPoll", normalized, label, frozenset({"chat_id"}))
    return bad[0] if bad else None


def _prepare_poll(poll: dict) -> dict:
    poll = dict(poll)
    if "chat_id" in poll:
        raise StudioError("chat_id задаётся не в poll")
    if not poll.get("question") or not isinstance(poll.get("options"), list) or len(poll["options"]) < 2:
        raise StudioError("нужны question и минимум два варианта в options")
    poll["options"] = [o if isinstance(o, dict) else {"text": str(o)} for o in poll["options"]]
    # the same checks as a poll after a post: a scheduled poll would otherwise fail only at its time
    if bad := poll_error(poll, "опрос"):
        raise Invalid({"message": "опрос не прошёл проверку", "ok": False, "errors": [bad], "warnings": []})
    return poll


def _media_error(k: "Kind", params) -> str | None:
    """What sendLivePhoto / sendPaidMedia would refuse before it is asked (a scheduled one only at its time)."""
    if not isinstance(params, dict):
        return f"{k.label}: ожидался объект {k.method}"
    if "chat_id" in params:
        return f"{k.label}: chat_id задаётся не здесь"
    if k.name == PAID_MEDIA:
        media = params.get("media")
        if not isinstance(media, list) or not 1 <= len(media) <= 10:
            return f"{k.label}: в media от 1 до 10 фото или видео"
        stars = params.get("star_count")
        if isinstance(stars, int) and not isinstance(stars, bool) and not 1 <= stars <= STARS_MAX:
            return f"{k.label}: star_count от 1 до {STARS_MAX} звёзд"
    bad = spec.method_errors(spec.current, k.method, params, k.label, frozenset({"chat_id"}))
    return bad[0] if bad else None


def _prepare_media(k: "Kind", params) -> dict:
    if bad := _media_error(k, params):
        raise Invalid({"message": "сообщение не прошло проверку", "ok": False, "errors": [bad], "warnings": []})
    if params.get("reply_markup") and (errs := spec.keyboard_errors(params["reply_markup"])):
        raise Invalid({"message": "кнопки не прошли проверку", "ok": False, "errors": errs, "warnings": []})
    return dict(params)


@dataclass(frozen=True)
class Kind:
    name: str
    method: str  # the Bot API method that sends it
    label: str  # how messages to a person name it
    # where the payload keeps the method's parameters ({key: params, "pin": …}); None: a rich post, whose payload
    # is the studio's own (rich_message, options, reply_markup, comment, poll_after…)
    key: str | None = None
    badge: str | None = None  # the mark in the list of posts; None: none
    editable: bool = False  # its content can be replaced (editMessageText), and it opens in the post editor
    edit_refusal: str = ""  # why not, when it cannot
    buttons_in_params: bool = False  # its keyboard is a parameter of its own method, not the post's reply_markup
    stoppable: bool = False  # stopPoll applies
    media: bool = False  # sent through /api/media-post (send_media_post)
    title_field: str = ""  # the parameter that titles it in the list of posts
    title_default: str = ""  # set: the title is the field's first line, this when it is empty
    # checks the parameters a person gave and returns them as they are stored; raises StudioError / Invalid
    prepare: Callable[[dict], dict] | None = field(default=None, compare=False, repr=False)

    def params(self, post: dict) -> dict:
        """The method's own parameters inside a stored payload ({} when it has none)."""
        p = post.get(self.key) if self.key else None
        return p if isinstance(p, dict) else {}

    def buttons_holder(self, payload: dict) -> dict:
        """The dict whose reply_markup is this message's keyboard."""
        return self.params(payload) if self.buttons_in_params else payload

    def title(self, payload: dict) -> str:
        """The first line in the list of posts for a kind with its own parameters; a rich post is titled by listing."""
        text = str(self.params(payload).get(self.title_field) or "")
        if not self.title_default:
            return text
        return next((x.strip() for x in text.splitlines() if x.strip()), "") or self.title_default


def _media(name: str, method: str, label: str, badge: str, title_default: str) -> Kind:
    return Kind(
        name=name,
        method=method,
        label=label,
        key=name,
        badge=badge,
        edit_refusal=f"{label} заменить нельзя; можно удалить или сменить кнопки (POST /api/posts/{{id}}/buttons)",
        buttons_in_params=True,
        media=True,
        title_field="caption",
        title_default=title_default,
        prepare=lambda params: _prepare_media(KINDS[name], params),
    )


KINDS: dict[str, Kind] = {
    k.name: k
    for k in (
        Kind(name=RICH, method="sendRichMessage", label="пост", editable=True),
        Kind(
            name=POLL,
            method="sendPoll",
            label="опрос",
            key="poll",
            badge="опрос",
            edit_refusal="опрос заменить нельзя; можно закрыть или удалить",
            stoppable=True,
            title_field="question",
            prepare=_prepare_poll,
        ),
        _media(LIVE_PHOTO, "sendLivePhoto", "Live Photo", "Live Photo", "Live Photo"),
        _media(PAID_MEDIA, "sendPaidMedia", "платный контент", "за звёзды", "Платный контент"),
    )
}
MEDIA = [k.name for k in KINDS.values() if k.media]


def get(name: str | None) -> Kind:
    """The kind stored with a post; a row from before kinds were stored is a rich post."""
    k = KINDS.get(name or RICH)
    if not k:
        raise StudioError(f"неизвестный тип сообщения {name!r}: {', '.join(KINDS)}")
    return k


def describe() -> dict[str, dict]:
    """What the editor needs to know about each kind, for /api/config."""
    return {
        k.name: {
            "label": k.label,
            "badge": k.badge,
            "editable": k.editable,
            "stoppable": k.stoppable,
            "media": k.media,
        }
        for k in KINDS.values()
    }
