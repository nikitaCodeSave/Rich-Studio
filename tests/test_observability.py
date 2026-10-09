"""Logs, health report and housekeeping: what the studio tells a person or an agent about itself."""

import json
import logging
import os
import re
import time
from pathlib import Path

import pytest

import logs
import metrics
import settings
import store
from services import health, housekeeping
from test_api import BLOCKS_POST

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "1234567890:AAH" + "x" * 32


@pytest.fixture
def log_file(fresh_data):
    """The JSON log in this test's data folder, and counters from zero."""
    metrics.reset()
    housekeeping._last.clear()
    path = logs.setup()
    yield path
    logs.setup()  # close the file before the folder goes away


def _check(rep: dict, name: str) -> dict:
    return next(c for c in rep["checks"] if c["name"] == name)


# ---------- the log ----------
def test_api_calls_and_telegram_calls_go_to_the_json_log(client, tg, log_file):
    client.post("/api/send", json={"post": BLOCKS_POST}, headers={"X-Studio-Client": "mcp:test"})
    client.get("/api/health")  # polled endpoints stay out of the log
    lines = [json.loads(s) for s in log_file.read_text("utf-8").splitlines()]
    http = [x for x in lines if x.get("event") == "http"]
    assert [(x["method"], x["path"], x["status"], x["client"]) for x in http] == [
        ("POST", "/api/send", 200, "mcp:test")
    ]
    call = next(x for x in lines if x.get("event") == "telegram.call")
    assert call["method"] == "sendRichMessage" and call["chat_id"] == "@test_channel" and call["ms"] >= 0
    assert "rich_message" not in log_file.read_text("utf-8")  # no post content in the log
    assert metrics.telegram["sendRichMessage ok"] == 1 and metrics.requests["2xx"] >= 2


def test_token_never_reaches_the_log(log_file, capsys):
    logging.getLogger("rich-studio.test").warning("url https://api.telegram.org/bot%s/sendMessage", TOKEN)
    try:
        raise RuntimeError(f"boom {TOKEN}")
    except RuntimeError:
        logging.getLogger("rich-studio.test").exception("failed")
    text = log_file.read_text("utf-8")
    assert TOKEN not in text and TOKEN not in capsys.readouterr().err
    assert "<token>" in text and "boom" in text


def test_log_is_rotated_and_never_grows_past_the_cap(fresh_data, monkeypatch):
    cfg = settings.get()
    monkeypatch.setattr(cfg, "log_max_mb", 0.01)  # ~10 KB per file
    monkeypatch.setattr(cfg, "log_files", 2)
    logs.setup()
    log = logging.getLogger("rich-studio.test")
    for i in range(1000):
        log.info("line %d %s", i, "x" * 100)
    names = sorted(p.name for p in cfg.log_dir.iterdir())
    assert names == ["studio.log", "studio.log.1", "studio.log.2"]
    assert logs.disk_bytes() <= 3 * 0.01 * 1024 * 1024 + 1024
    newest = logs.read("INFO", limit=1)
    assert newest[0]["msg"].startswith("line 999")
    monkeypatch.setattr(cfg, "log_max_mb", 0)  # stderr only
    assert logs.setup() is None and logs.read() == []
    monkeypatch.undo()
    logs.setup()


def test_logs_endpoint_filters_by_level_logger_and_text(client, log_file):
    logging.getLogger("rich-studio.scheduler").info("tick")
    logging.getLogger("rich-studio.scheduler").error("scheduler broke")
    logging.getLogger("rich-studio.updates").warning("late click")
    r = client.get("/api/logs", params={"level": "WARNING"}).json()
    assert r["file"].endswith("studio.log")
    assert [x["msg"] for x in r["lines"]] == ["scheduler broke", "late click"]
    assert [x["msg"] for x in client.get("/api/logs?logger=rich-studio.scheduler").json()["lines"]] == [
        "tick",
        "scheduler broke",
    ]
    assert [x["msg"] for x in client.get("/api/logs?q=LATE").json()["lines"]] == ["late click"]
    assert client.get("/api/logs?since=" + str(time.time() + 60)).json()["lines"] == []
    assert client.get("/api/logs?level=LOUD").status_code == 422


# ---------- health ----------
def test_health_of_a_fresh_studio_is_ok_where_it_matters(client, log_file):
    rep = client.get("/api/health").json()
    assert [c["name"] for c in rep["checks"]] == list(health.CHECKS)
    for name in ("database", "telegram", "scheduler", "posts", "sends", "storage", "log", "loops"):
        assert _check(rep, name)["status"] == "ok", _check(rep, name)
    assert rep["status"] in ("ok", "warn") and isinstance(rep["problems"], list)
    assert set(rep["metrics"]) == {"uptime_s", "requests", "telegram_calls", "log_problems"}


def test_health_names_what_needs_a_person_and_what_to_do(client, log_file):
    now = int(time.time())
    store.add_post("@c", BLOCKS_POST, status="failed", error="Telegram: chat not found")
    store.add_post("@c", BLOCKS_POST, status="scheduled", scheduled_at=now - 600)
    store.idempotency.claim("k1", "send", "fp")
    store.idempotency.finish("k1", "uncertain", "пост мог уйти")
    logging.getLogger("rich-studio.scheduler").error("scheduler iteration failed")
    metrics.telegram_call("sendRichMessage", False, "Bad Gateway")
    rep = health.report()
    assert rep["status"] == "warn"
    posts = _check(rep, "posts")
    assert posts["status"] == "warn" and posts["details"]["posts"][0]["error"] == "Telegram: chat not found"
    assert _check(rep, "scheduler")["status"] == "warn" and "выключены" in _check(rep, "scheduler")["summary"]
    assert _check(rep, "sends")["status"] == "warn" and "Idempotency-Key" in _check(rep, "sends")["action"]
    assert "scheduler iteration failed" in _check(rep, "log")["summary"]
    assert "Bad Gateway" in _check(rep, "telegram")["summary"]
    assert all(c.get("action") for c in rep["checks"] if c["status"] != "ok")
    metrics.telegram_call("getMe", True)  # Telegram answers again: no longer a problem
    assert _check(health.report(), "telegram")["status"] == "ok"


def test_conflicting_bot_and_dead_loop_are_failures(client, log_file, monkeypatch):
    from services import updates

    monkeypatch.setattr(updates.STATUS, "state", "conflict")
    monkeypatch.setattr(updates.STATUS, "error", "Conflict: terminated by other getUpdates request")
    monkeypatch.setattr(settings.get(), "background", True)
    metrics.beat("scheduler", 10)
    metrics.loops["scheduler"]["beat_at"] = time.time() - 3600
    rep = health.report()
    assert rep["status"] == "fail"
    assert _check(rep, "telegram")["status"] == "fail" and "TG_UPDATES=off" in _check(rep, "telegram")["action"]
    assert _check(rep, "loops")["status"] == "fail" and "scheduler" in _check(rep, "loops")["summary"]


def test_a_broken_check_is_reported_not_raised(client, log_file, monkeypatch):
    monkeypatch.setitem(health.CHECKS, "disk", lambda: 1 / 0)
    rep = client.get("/api/health").json()
    assert _check(rep, "disk")["status"] == "fail" and "ZeroDivisionError" in _check(rep, "disk")["summary"]


def test_unused_uploads_are_counted(client, log_file, monkeypatch):
    client.post("/api/media", files={"file": ("big.png", b"x" * 2048, "image/png")})
    monkeypatch.setattr(health, "UNUSED_MEDIA_WARN", 1024)
    st = _check(health.report(), "storage")
    assert st["status"] == "warn" and "MEDIA_UNUSED_DAYS" in st["action"]
    assert st["details"]["media"] == {"files": 1, "bytes": 2048, "unused_files": 1, "unused_bytes": 2048}


# ---------- watching itself and cleaning up ----------
def test_health_changes_are_logged_once(client, log_file):
    housekeeping.watch()
    store.add_post("@c", BLOCKS_POST, status="failed", error="boom")
    housekeeping.watch()
    housekeeping.watch()  # no change, no line
    changes = [x for x in logs.read("INFO") if x.get("event") == "health.change"]
    assert [(x["check"], x["status"]) for x in changes if x["check"] == "posts"] == [("posts", "warn")]
    assert changes[-1]["action"]


def test_cleanup_removes_service_data_always_and_user_data_only_when_asked(client, tg, log_file, monkeypatch):
    cfg = settings.get()
    store.idempotency.claim("old", "send", "fp")
    with store.conn() as c:
        c.execute("update idempotency set created_at=0")
    pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    for i in range(4):
        store.add_version(pid, {"v": i}, "ui")
    for name in ("old.png", "new.png"):
        client.post("/api/media", files={"file": (name, b"\x89PNG", "image/png")})
    os.utime(cfg.media_dir / "old.png", (time.time() - 40 * 86400,) * 2)

    assert housekeeping.cleanup() == {"idempotency_keys": 1}  # defaults: the person's things stay
    assert len(store.list_versions(pid)) == 4 and (cfg.media_dir / "old.png").exists()

    monkeypatch.setattr(cfg, "versions_keep", 2)
    monkeypatch.setattr(cfg, "media_unused_days", 30)
    done = housekeeping.cleanup()
    assert done["post_versions"] == 2 and done["media"] == ["old.png"]
    assert [v["payload"] for v in store.list_versions(pid)] == [{"v": 3}, {"v": 2}]
    assert (cfg.media_dir / "new.png").exists()
    assert any(x.get("event") == "cleanup" for x in logs.read("INFO"))


def test_mcp_health_and_logs(client, log_file):
    from test_mcp import call, call_list

    logging.getLogger("rich-studio.updates").warning("late click")
    _, rep, _ = call(client, "studio_health")
    assert [c["name"] for c in rep["checks"]] == list(health.CHECKS)
    lines = call_list(client, "studio_logs", level="WARNING")
    assert [x["msg"] for x in lines] == ["late click"]


# ---------- the agent side must not drift from the code ----------
def test_monitor_skill_explains_every_check():
    skill = (ROOT / ".claude/skills/studio-health/SKILL.md").read_text("utf-8")
    explained = set(re.findall(r"^\| `([a-z]+)` \|", skill, re.MULTILINE))
    assert explained == set(health.CHECKS)


def test_monitor_agent_is_read_only():
    agent = (ROOT / ".claude/agents/studio-monitor.md").read_text("utf-8")
    front = agent.split("---")[1]
    assert re.search(r"^name: studio-monitor$", front, re.MULTILINE)
    tools = re.search(r"^tools: (.+)$", front, re.MULTILINE)
    assert tools and not {"Edit", "Write"} & {t.strip() for t in tools.group(1).split(",")}
