"""The one fake Bot API: used by pytest (in a thread) and by tests/e2e_ui.mjs (as a server).

Run standalone: uvicorn mock_telegram:app --port 8081 (from tests/), then start the studio with
TG_BOT_TOKEN=1:x TG_API_BASE=http://127.0.0.1:8081. GET /log shows every call;
POST /push queues an update that the studio will receive through getUpdates;
POST /fail makes a method answer 400 (for checks of refused sends).
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, Request

# real answers of Telegram to known HTML/Markdown sources (scripts/capture_telegram_parse.py)
CAPTURED = Path(__file__).resolve().parent / "telegram_parse" / "captured.json"
_parsed_cache: dict = {}

TRUE_METHODS = {
    "deleteMessage",
    "pinChatMessage",
    "unpinChatMessage",
    "answerCallbackQuery",
    "approveSuggestedPost",
    "declineSuggestedPost",
}


INVITE_METHODS = {
    "createChatInviteLink",
    "editChatInviteLink",
    "revokeChatInviteLink",
    "createChatSubscriptionInviteLink",
    "editChatSubscriptionInviteLink",
}


NOT_MODIFIED = {
    "ok": False,
    "error_code": 400,
    "description": "Bad Request: message is not modified: specified new message content and "
    "reply markup are exactly the same as a current content and reply markup of the message",
}


def _message(message_id, **extra) -> dict:
    """A Message the way real Telegram returns it (not the full rich_message, which Telegram rewrites into blocks)."""
    return {
        "message_id": message_id,
        "from": {"id": 42, "is_bot": True, "first_name": "Bot", "username": "studio_test_bot"},
        "chat": {"id": -1001, "type": "channel", "title": "Тест"},
        "date": int(time.time()),
        **extra,
    }


def _content(body: dict) -> dict:
    return {"rich_message": body.get("rich_message"), "reply_markup": body.get("reply_markup")}


def parsed_blocks(rich_message) -> list:
    """The blocks Telegram answers with: recorded ones for a known source, else the text as one paragraph."""
    if isinstance(rich_message, str):  # multipart uploads send it as a JSON string
        rich_message = json.loads(rich_message)
    rm = rich_message or {}
    if "blocks" in rm:
        return rm["blocks"]
    if not _parsed_cache:
        for c in json.loads(CAPTURED.read_text("utf-8")).values():
            if c.get("blocks"):
                _parsed_cache[(c["format"], c["source"])] = c["blocks"]
    for fmt in ("html", "markdown"):
        if fmt in rm:
            return _parsed_cache.get((fmt, rm[fmt])) or [{"type": "paragraph", "text": rm[fmt]}]
    return []


class FakeTelegram:
    """Records every Bot API call and answers like Telegram would."""

    def __init__(self):
        self.calls: list[dict] = []
        self.next_id = 100
        self.next_update = 1
        self.fail: dict[str, str] = {}  # method -> description of a 400 error
        self.queue: dict[str, list] = {}  # method -> raw responses (dicts or Responses) before the normal answer
        self.updates: list[dict] = []  # what the next getUpdates returns
        self.messages: dict[tuple, dict] = {}  # (chat_id, message_id) -> content, to answer edits like Telegram
        self.links: dict[str, dict] = {}  # invite_link -> ChatInviteLink
        self.app = self._build()

    def reset(self):
        self.calls.clear()
        self.fail.clear()
        self.queue.clear()
        self.updates.clear()
        self.messages.clear()
        self.links.clear()

    def last(self, method: str) -> dict:
        return [c for c in self.calls if c["method"] == method][-1]

    def push(self, update: dict) -> dict:
        update["update_id"] = self.next_update
        self.next_update += 1
        self.updates.append(update)
        return update

    async def _get_updates(self, body: dict) -> dict:
        for _ in range(int(min(body.get("timeout") or 0, 2) * 10)):  # short long-poll
            if self.updates:
                break
            await asyncio.sleep(0.1)
        off = body.get("offset") or 0
        out = [u for u in self.updates if u["update_id"] >= off]
        self.updates.clear()
        return {"ok": True, "result": out}

    def answer(self, method: str, body: dict):
        if self.queue.get(method):
            return self.queue[method].pop(0)
        if method in self.fail:
            return {"ok": False, "error_code": 400, "description": self.fail[method]}
        if method == "getMe":
            return {
                "ok": True,
                "result": {"id": 42, "is_bot": True, "first_name": "Bot", "username": "studio_test_bot"},
            }
        if method == "getChat":
            return {"ok": True, "result": {"id": -1001, "type": "channel", "title": "Тест"}}
        if method == "getChatMember":
            return {
                "ok": True,
                "result": {"status": "administrator", "can_post_messages": True, "can_edit_messages": True},
            }
        if method in ("sendLivePhoto", "sendPaidMedia"):
            self.next_id += 1
            if method == "sendLivePhoto":
                media = {
                    "live_photo": {"file_id": "lp", "file_unique_id": "lp", "width": 720, "height": 960, "duration": 2}
                }
            else:
                media = {"paid_media": {"star_count": int(body.get("star_count") or 0), "paid_media": []}}
            return {"ok": True, "result": _message(self.next_id, caption=body.get("caption"), **media)}
        if method in ("sendRichMessage", "sendPoll"):
            self.next_id += 1
            res = _message(self.next_id)
            self.messages[(body.get("chat_id"), self.next_id)] = _content(body)
            if method == "sendRichMessage":
                res["rich_message"] = {"blocks": parsed_blocks(body.get("rich_message"))}
            if method == "sendPoll":
                res["poll"] = {"id": f"poll{self.next_id}", "question": body.get("question")}
            return {"ok": True, "result": res}
        if method == "editMessageText":
            # like Telegram: the edited Message for channel posts, 400 when nothing would change
            key = (body.get("chat_id"), body.get("message_id"))
            if self.messages.get(key) == _content(body):
                return NOT_MODIFIED
            self.messages[key] = _content(body)
            return {"ok": True, "result": _message(body.get("message_id"), edit_date=int(time.time()))}
        if method == "editMessageReplyMarkup":
            # the same "not modified" as editMessageText when the buttons are the same
            key = (body.get("chat_id"), body.get("message_id"))
            old = self.messages.get(key)
            if old is not None and old.get("reply_markup") == body.get("reply_markup"):
                return NOT_MODIFIED
            if old is not None:
                old["reply_markup"] = body.get("reply_markup")
            return {"ok": True, "result": _message(body.get("message_id"), edit_date=int(time.time()))}
        if method == "stopPoll":
            return {
                "ok": True,
                "result": {
                    "id": "x",
                    "total_voter_count": 3,
                    "is_closed": True,
                    "options": [{"text": "Да", "voter_count": 2}, {"text": "Нет", "voter_count": 1}],
                },
            }
        if method in INVITE_METHODS:
            return {"ok": True, "result": self._invite_link(method, body)}
        if method in TRUE_METHODS:
            return {"ok": True, "result": True}
        return {"ok": False, "error_code": 404, "description": "Not Found: method not mocked"}

    def _invite_link(self, method: str, body: dict) -> dict:
        """A ChatInviteLink as Telegram answers: the link's own fields back, a new t.me/+ link on create."""
        if method.startswith("create"):
            self.next_id += 1
            link = f"https://t.me/+fake{self.next_id}"
        else:
            link = body["invite_link"]
        old = self.links.get(link, {})
        if method == "revokeChatInviteLink":
            self.links[link] = {**old, "is_revoked": True}
            return self.links[link]
        keep = (
            "name",
            "expire_date",
            "member_limit",
            "creates_join_request",
            "subscription_period",
            "subscription_price",
        )
        sub = {k: old[k] for k in ("subscription_period", "subscription_price") if k in old}
        self.links[link] = {
            "invite_link": link,
            "creator": {"id": 42, "is_bot": True, "first_name": "Bot"},
            "creates_join_request": False,
            "is_primary": False,
            "is_revoked": False,
            **sub,
            **{k: body[k] for k in keep if body.get(k) not in (None, "")},
        }
        return self.links[link]

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/bot{token}/{method}")
        async def handle(token: str, method: str, request: Request):
            files: dict = {}
            if "multipart" in request.headers.get("content-type", ""):
                form = await request.form()
                body: dict = {}
                for k, v in form.multi_items():
                    if isinstance(v, str):
                        body[k] = v
                    else:
                        files[k] = (v.filename, len(await v.read()))
                for k in ("rich_message", "reply_markup"):
                    if k in body:
                        body[k] = json.loads(body[k])
            else:
                body = await request.json()
            if method == "getUpdates":
                return await self._get_updates(body)
            self.calls.append({"method": method, "token": token, "body": body, "files": files})
            return self.answer(method, body)  # a queued starlette Response (e.g. an HTML 502) goes out as is

        @app.post("/push")
        async def push(update: dict):
            return self.push(update)

        @app.post("/fail")
        def set_fail(req: dict):
            """{"method": ..., "description": ...} makes that method answer 400; description null clears it."""
            if req.get("description"):
                self.fail[req["method"]] = req["description"]
            else:
                self.fail.pop(req["method"], None)
            return {"ok": True}

        @app.get("/log")
        def get_log():
            return [{"method": c["method"], "body": c["body"]} for c in self.calls]

        return app


FAKE = FakeTelegram()
app = FAKE.app
