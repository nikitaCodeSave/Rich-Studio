"""Answers to the callback buttons of a post (`callbacks`): their shape, their check, what one press sends.

`callbacks[callback_data]` is the text of answerCallbackQuery, or an object: the parameters of answerCallbackQuery
(text, show_alert, url, cache_time…) as they are, and, by choice, `reply`: the parameters of sendRichMessage for an
ephemeral rich reply that only the person who pressed sees (Bot API 10.4), in place of the post itself when
`reply.ephemeral_message_parameters.replace_callback_query_message` is true. The studio adds what is known only at
the press, `callback_query_id`; everything else goes to Telegram as it is (docs/decisions/0021-ephemeral-callback-reply.md).

A press: the reply first, then answerCallbackQuery, always (or the button keeps spinning), whatever the reply did.
A reply is not a post: nothing is written before it goes, and it is never sent again. A reply that did not go is a
line in the log (`callback.reply_failed`) and, for a post, a count next to its clicks (`posts.reply_errors`).
"""

import asyncio
import logging
import time

import spec
import store
import telegram as tg
from errors import StudioError

log = logging.getLogger("rich-studio.callbacks")

REPLY = "reply"
# Telegram takes an ephemeral reply to a callback query only this long after the press ("Ephemeral Messages and
# Commands" in the Bot API docs; api.json has no number for it). CallbackQuery has no date, so the studio counts
# from the moment getUpdates brought the press: the press itself was no later than that
WINDOW = 15.0
# less than this left of the window: the reply would come too late to be taken, so it is not tried
MIN_LEFT = 1.0
TOO_LATE = "query is too old"  # answerCallbackQuery for a press that came while the studio was off


def _labels(markup) -> dict[str, str]:
    """callback_data → the text on its button, so a message names the button the author sees."""
    rows = markup.get("inline_keyboard") if isinstance(markup, dict) else None
    out: dict[str, str] = {}
    for row in rows if isinstance(rows, list) else []:
        for b in row if isinstance(row, list) else []:
            if isinstance(b, dict) and isinstance(b.get("callback_data"), str) and isinstance(b.get("text"), str):
                out.setdefault(b["callback_data"], b["text"])
    return out


def check(post: dict) -> dict:
    """Errors and warnings of the answers to the post's buttons, each with the button it is for. A text and an object
    of answerCallbackQuery fields are what they always were: anything odd in them is a warning, as Telegram may still
    take it. The rich reply is checked like a post: its errors stop the post."""
    cbs = post.get("callbacks")
    if cbs is None or cbs == {}:
        return {"errors": [], "warnings": []}
    if not isinstance(cbs, dict):
        return {"errors": ["callbacks: нужен объект {callback_data: ответ}"], "warnings": []}
    labels = _labels(post.get("reply_markup"))
    errors: list[str] = []
    warnings: list[str] = []
    for data, answer in cbs.items():
        where = f"ответ на кнопку «{labels.get(data) or data}»"
        e, w = _check_answer(answer)
        errors += [f"{where}: {x}" for x in e]
        warnings += [f"{where}: {x}" for x in w]
    return {"errors": errors, "warnings": warnings}


def _check_answer(answer) -> tuple[list[str], list[str]]:
    if isinstance(answer, str):
        return [], []
    if not isinstance(answer, dict):
        return [], ["ни текст, ни объект: на нажатие придёт пустой ответ"]
    errors: list[str] = []
    v = spec.Validator(spec.current)
    method = spec.current.methods.get("answerCallbackQuery") or {}
    fields = {f["name"]: f for f in method.get("fields", [])}
    for k, value in answer.items():
        if k == REPLY:
            continue
        if k == "callback_query_id":
            errors.append("callback_query_id задаёт студия при нажатии")
        elif (f := fields.get(k)) is None:
            v.warnings.append(f"{k}: поля нет в answerCallbackQuery (спецификация {spec.current.version})")
        else:
            v.check(value, f["types"], k)
    warnings = v.warnings + v.errors  # the answer worked like this before: Telegram decides, not the studio
    reply = answer.get(REPLY)
    if reply is not None:
        e, w = _check_reply(reply)
        errors += e
        warnings += w
        if isinstance(answer.get("cache_time"), int) and answer["cache_time"] > 0:
            warnings.append("cache_time: повторное нажатие в это время не дойдёт до студии, и rich-ответа не будет")
    return errors, warnings


def _check_reply(reply) -> tuple[list[str], list[str]]:
    """The reply is a sendRichMessage request without its chat and its query: rich_message like a post, the other
    parameters by the spec, fields the spec does not know as warnings."""
    if not isinstance(reply, dict):
        return [f"{REPLY}: нужен объект с параметрами sendRichMessage: {{rich_message: {{html|markdown|blocks}}}}"], []
    if "rich_message" not in reply:
        hint = (
            " (html, markdown или blocks кладутся внутрь rich_message)"
            if {"html", "markdown", "blocks"} & set(reply)
            else ""
        )
        return [f"{REPLY}: нет обязательного поля rich_message{hint}"], []
    r = spec.validate_rich_message(spec.current, reply["rich_message"])
    errors, warnings = [_in_reply(m) for m in r["errors"]], [_in_reply(m) for m in r["warnings"]]
    v = spec.Validator(spec.current)
    method = spec.current.methods.get("sendRichMessage") or {}
    fields = {f["name"]: f for f in method.get("fields", [])}
    for k, value in reply.items():
        if k == "rich_message":
            continue
        if k == "chat_id":  # the reply goes to the person who pressed, never to a chat: nothing can go public
            errors.append(f"{REPLY}.chat_id: ответ уходит только нажавшему, чат не задаётся")
        elif (f := fields.get(k)) is None:
            v.warnings.append(f"{REPLY}.{k}: поля нет в sendRichMessage (спецификация {spec.current.version})")
        elif k == "ephemeral_message_parameters" and isinstance(value, dict):
            for own, why in (
                ("callback_query_id", "его задаёт студия при нажатии"),
                ("receiver_user_id", "кто нажмёт, заранее не известно, а Telegram примет ответ только нажавшему"),
            ):
                if own in value:
                    errors.append(f"{REPLY}.{k}.{own}: {why}")
            v.check(
                {x: y for x, y in value.items() if x not in ("callback_query_id", "receiver_user_id")},
                f["types"],
                f"{REPLY}.{k}",
            )
        else:
            v.check(value, f["types"], f"{REPLY}.{k}")
    errors += v.errors + spec.keyboard_errors(reply.get("reply_markup"))
    warnings += v.warnings
    if _labels(reply.get("reply_markup")):
        warnings.append(f"{REPLY}.reply_markup: на кнопки callback_data внутри ответа студия не отвечает")
    return errors, warnings


def _in_reply(message: str) -> str:
    """The validator's paths start at rich_message; here it lies inside reply, as in the JSON of the post."""
    return f"{REPLY}.{message}" if message.startswith("rich_message") else message


async def answer(cq: dict, callbacks: dict, post_id: str | None, received: float | None = None) -> None:
    """One press of a button: the rich reply first, if the button has one, then answerCallbackQuery, always.
    `received`: time.monotonic() when getUpdates brought the press (None: just now)."""
    data = cq.get("data") or ""
    ans = callbacks.get(data) if isinstance(callbacks, dict) else None
    if isinstance(ans, str):
        ans = {"text": ans}
    params = {k: v for k, v in ans.items() if k != REPLY} if isinstance(ans, dict) else {}
    params["callback_query_id"] = cq["id"]
    reply = ans.get(REPLY) if isinstance(ans, dict) else None
    if isinstance(reply, dict):
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        await _reply(cq["id"], data, reply, post_id, chat, received)
    try:
        await tg.call("answerCallbackQuery", params)  # always, or the button keeps spinning
    except tg.TelegramError as e:
        # the press came while the studio was off: Telegram no longer takes an answer, the click still counts
        if TOO_LATE not in e.description:
            raise
        log.warning("button click came too late to answer: %s", e.description)


async def _reply(cq_id: str, data: str, reply: dict, post_id: str | None, chat, received: float | None) -> None:
    """sendRichMessage as an ephemeral reply to the press. Never raises (except a stop) and never repeats: a reply
    is worth nothing after the window, and one that may have gone must not come twice."""
    waited = time.monotonic() - received if received is not None else 0.0
    left = WINDOW - waited
    if left < MIN_LEFT:
        _failed(
            post_id,
            chat,
            data,
            "late",
            f"нажатие ждало в студии {waited:.0f} с, а Telegram принимает ответ "
            f"{WINDOW:.0f} с после нажатия: rich-ответ не отправлен",
        )
        return
    own = reply.get("ephemeral_message_parameters") or {}
    if not isinstance(own, dict):  # the check refuses it; a payload stored before the check must not stop the answer
        _failed(post_id, chat, data, "not_sent", "ephemeral_message_parameters не объект: rich-ответ не отправлен")
        return
    params = {**reply, "ephemeral_message_parameters": {**own, "callback_query_id": cq_id}}
    try:
        await asyncio.wait_for(tg.call("sendRichMessage", params, timeout=left), left)
    except tg.Unreachable as e:  # no connection was made: nothing went
        _failed(post_id, chat, data, "not_sent", e.description, e.code)
    except tg.TelegramError as e:  # refused: a late press, a chat where Telegram does not take it, a wrong field
        _failed(post_id, chat, data, "refused", e.description, e.code)
    except StudioError as e:  # the request itself is wrong (a missing upload): nothing went
        _failed(post_id, chat, data, "not_sent", str(e))
    except TimeoutError:
        _failed(post_id, chat, data, "may_have_gone", f"Telegram не ответил за {left:.0f} с: ответ мог дойти")
    except Exception as e:  # noqa: BLE001 - mid-request: it may have reached the person, so it is not sent again
        _failed(post_id, chat, data, "may_have_gone", f"{type(e).__name__}: {e}. Ответ мог дойти")
    else:
        log.info(
            "rich reply to button %s sent",
            data,
            extra={
                "event": "callback.reply",
                "post_id": post_id,
                "chat_id": chat,
                "callback_data": data,
                "replace": bool(own.get("replace_callback_query_message")),
            },
        )


def _failed(post_id: str | None, chat, data: str, outcome: str, error: str, code: int | None = None) -> None:
    """The log line and, for a post, the count next to its clicks. Never raises: the answer still has to go."""
    log.warning(
        "rich reply to button %s did not go (%s): %s",
        data,
        outcome,
        error,
        extra={
            "event": "callback.reply_failed",
            "post_id": post_id,
            "chat_id": chat,
            "callback_data": data,
            "outcome": outcome,
            "code": code,
        },
    )
    if post_id:
        try:
            store.note_reply_error(post_id, data, error)
        except Exception:
            log.exception("could not record the failed rich reply of post %s", post_id)
