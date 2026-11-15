"""Record how the real Telegram parses rich HTML and Markdown.

Sends every case from tests/telegram_parse/cases.json with sendRichMessage, saves the blocks Telegram
answers with to tests/telegram_parse/captured.json and deletes the message right away. The preview
parity test (npm run test:parity) then checks that render.js draws the source the way Telegram does.
Messages go out silently to PARSE_CHAT (default TG_PREVIEW_CHAT), a chat where the bot may post, and
live for a second. Run after a Bot API update or when adding a case. Two ways to reach Telegram:

- through a running studio, no bot token needed: STUDIO_URL (and STUDIO_TOKEN with level admin, if the
  studio is protected). The calls go to POST {STUDIO_URL}/api/raw/<method>, deleteMessage with
  X-Confirm: yes; the studio must have /api/raw on (RAW_API).
    STUDIO_URL=http://127.0.0.1:7424 PARSE_CHAT=<chat> python scripts/capture_telegram_parse.py [case-name ...]
- straight to the Bot API: TG_BOT_TOKEN (and TG_API_BASE for a local Bot API server).
    TG_BOT_TOKEN=… PARSE_CHAT=<chat> python scripts/capture_telegram_parse.py [case-name ...]

STUDIO_URL wins when both are set. Without case names every case is sent.
"""

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "tests" / "telegram_parse"
CASES = DIR / "cases.json"
CAPTURED = DIR / "captured.json"

Call = Callable[[str, dict], dict]


class StudioFailed(Exception):
    """The studio did not pass the call to Telegram (no token, no right, /api/raw off, Telegram unreachable)."""


def bot_api(client: httpx.Client, token: str) -> Call:
    """Bot API calls with the bot's own token; the answer is Telegram's as is."""
    base = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/") + "/bot" + token

    def call(method: str, params: dict) -> dict:
        # never print the URL: it carries the token
        return client.post(f"{base}/{method}", json=params).json()

    return call


def through_studio(client: httpx.Client, studio_url: str, studio_token: str = "") -> Call:
    """Bot API calls through POST /api/raw of a running studio, answered in Telegram's shape: {"ok", "result"} or
    {"ok": false, "description"}. Only Telegram's own refusal (the studio's answer carries error_code) is such an
    answer; anything else the studio says raises StudioFailed, so captured.json never records it as a parse."""
    auth = {"Authorization": f"Bearer {studio_token}"} if studio_token else {}

    def call(method: str, params: dict) -> dict:
        # deleteMessage is not a read or a send: /api/raw runs it only when asked to
        headers = {**auth, "X-Confirm": "yes"} if method == "deleteMessage" else auth
        r = client.post(f"{studio_url.rstrip('/')}/api/raw/{method}", json=params, headers=headers)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if r.status_code == 200 and "result" in body:
            return {"ok": True, "result": body["result"]}
        detail = str(body.get("detail") or f"HTTP {r.status_code}")
        if "error_code" not in body:
            raise StudioFailed(f"{method}: студия ответила {r.status_code}: {detail}")
        answer = {"ok": False, "error_code": body["error_code"], "description": detail.removeprefix("Telegram: ")}
        if body.get("retry_after"):
            answer["parameters"] = {"retry_after": body["retry_after"]}
        return answer

    return call


def with_flood_wait(call: Call) -> Call:
    def wrapped(method: str, params: dict) -> dict:
        for _ in range(3):
            r = call(method, params)
            wait = (r.get("parameters") or {}).get("retry_after")
            if r.get("ok") or not wait:
                return r
            print(f"  Telegram просит подождать {wait} с")
            time.sleep(wait + 1)
        return r

    return wrapped


def capture(call: Call, chat: str, cases: list, captured: dict, spec_version: str | None) -> int:
    """Sends the cases, writes what Telegram made of them into captured; returns how many Telegram refused."""
    failed = 0
    for c in cases:
        r = call(
            "sendRichMessage",
            {"chat_id": chat, "rich_message": {c["format"]: c["source"]}, "disable_notification": True},
        )
        if not r.get("ok"):
            failed += 1
            print(f"{c['name']}: Telegram отклонил: {r.get('description')}")
            captured[c["name"]] = {"source": c["source"], "format": c["format"], "error": r.get("description")}
            continue
        msg = r["result"]
        if not call("deleteMessage", {"chat_id": chat, "message_id": msg["message_id"]}).get("ok"):
            print(f"  не удалось удалить сообщение {msg['message_id']}: удалите его вручную")
        captured[c["name"]] = {
            "format": c["format"],
            "source": c["source"],
            "blocks": msg["rich_message"]["blocks"],
            "spec": spec_version,
            "captured_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        }
        print(f"{c['name']}: {len(msg['rich_message']['blocks'])} блоков")
        if c is not cases[-1]:
            time.sleep(3)  # groups allow about 20 messages a minute
    return failed


def main() -> int:
    studio = os.environ.get("STUDIO_URL", "").strip()
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("PARSE_CHAT") or os.environ.get("TG_PREVIEW_CHAT", "")
    if not chat or not (studio or token):
        print("нужны PARSE_CHAT (или TG_PREVIEW_CHAT) и STUDIO_URL работающей студии (или TG_BOT_TOKEN)")
        return 1
    spec_version = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8")).get("version")
    only = set(sys.argv[1:])
    cases = [c for c in json.loads(CASES.read_text("utf-8")) if not only or c["name"] in only]
    if unknown := only - {c["name"] for c in cases}:
        print(f"нет таких случаев в {CASES.name}: {', '.join(sorted(unknown))}")
        return 1
    captured = json.loads(CAPTURED.read_text("utf-8")) if CAPTURED.exists() else {}
    with httpx.Client(timeout=60) as client:
        studio_token = os.environ.get("STUDIO_TOKEN", "")
        call = through_studio(client, studio, studio_token) if studio else bot_api(client, token)
        try:
            failed = capture(with_flood_wait(call), chat, cases, captured, spec_version)
        except StudioFailed as e:
            print(f"{e}; снятое до этого записано")
            failed = 1
    CAPTURED.write_text(json.dumps(captured, ensure_ascii=False, indent=1, sort_keys=True) + "\n", "utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
