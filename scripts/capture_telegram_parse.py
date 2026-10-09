"""Record how the real Telegram parses rich HTML and Markdown.

Sends every case from tests/telegram_parse/cases.json with sendRichMessage, saves the blocks Telegram
answers with to tests/telegram_parse/captured.json and deletes the message right away. The preview
parity test (npm run test:parity) then checks that render.js draws the source the way Telegram does.
Needs the real bot: TG_BOT_TOKEN and a chat where it may post (PARSE_CHAT, default TG_PREVIEW_CHAT).
Messages go out silently and live for a second. Run after a Bot API update or when adding a case:
  python scripts/capture_telegram_parse.py [case-name ...]
"""

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "tests" / "telegram_parse"
CASES = DIR / "cases.json"
CAPTURED = DIR / "captured.json"


def main() -> int:
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("PARSE_CHAT") or os.environ.get("TG_PREVIEW_CHAT", "")
    if not token or not chat:
        print("нужны TG_BOT_TOKEN и PARSE_CHAT (или TG_PREVIEW_CHAT)")
        return 1
    base = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/") + "/bot" + token
    spec_version = json.loads((ROOT / "app" / "bundled" / "api.json").read_text("utf-8")).get("version")
    only = set(sys.argv[1:])
    cases = [c for c in json.loads(CASES.read_text("utf-8")) if not only or c["name"] in only]
    captured = json.loads(CAPTURED.read_text("utf-8")) if CAPTURED.exists() else {}
    failed = 0
    with httpx.Client(timeout=30) as client:

        def call(method: str, params: dict) -> dict:
            for _ in range(3):
                # never print the URL: it carries the token
                r = client.post(f"{base}/{method}", json=params).json()
                wait = (r.get("parameters") or {}).get("retry_after")
                if r.get("ok") or not wait:
                    return r
                print(f"  Telegram просит подождать {wait} с")
                time.sleep(wait + 1)
            return r

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
            time.sleep(3)  # groups allow about 20 messages a minute
    CAPTURED.write_text(json.dumps(captured, ensure_ascii=False, indent=1, sort_keys=True) + "\n", "utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
