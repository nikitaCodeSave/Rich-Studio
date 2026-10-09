"""The one fake Bot API: used by pytest (in a thread) and by tests/e2e_ui.mjs (as a server).

Run standalone: uvicorn mock_telegram:app --port 8081 (from tests/), then start the studio with
TG_BOT_TOKEN=1:x TG_API_BASE=http://127.0.0.1:8081. GET /log shows every call;
POST /push queues an update that the studio will receive through getUpdates.
"""

import asyncio
import json

from fastapi import FastAPI, Request

TRUE_METHODS = {
    "editMessageText",
    "deleteMessage",
    "pinChatMessage",
    "unpinChatMessage",
    "editMessageReplyMarkup",
    "answerCallbackQuery",
}


class FakeTelegram:
    """Records every Bot API call and answers like Telegram would."""

    def __init__(self):
        self.calls: list[dict] = []
        self.next_id = 100
        self.next_update = 1
        self.fail: dict[str, str] = {}  # method -> description of a 400 error
        self.queue: dict[str, list[dict]] = {}  # method -> raw responses returned before the normal answer
        self.updates: list[dict] = []  # what the next getUpdates returns
        self.app = self._build()

    def reset(self):
        self.calls.clear()
        self.fail.clear()
        self.queue.clear()
        self.updates.clear()

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

    def answer(self, method: str, body: dict) -> dict:
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
        if method in ("sendRichMessage", "sendPoll"):
            self.next_id += 1
            res: dict = {"message_id": self.next_id, "chat": {"id": -1001}}
            if method == "sendPoll":
                res["poll"] = {"id": f"poll{self.next_id}", "question": body.get("question")}
            return {"ok": True, "result": res}
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
        if method in TRUE_METHODS:
            return {"ok": True, "result": True}
        return {"ok": False, "error_code": 404, "description": "Not Found: method not mocked"}

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
            return self.answer(method, body)

        @app.post("/push")
        async def push(update: dict):
            return self.push(update)

        @app.get("/log")
        def get_log():
            return [{"method": c["method"], "body": c["body"]} for c in self.calls]

        return app


FAKE = FakeTelegram()
app = FAKE.app
