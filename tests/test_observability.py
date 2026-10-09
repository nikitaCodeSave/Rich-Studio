"""Logs, health report and housekeeping: what the studio tells a person or an agent about itself."""

import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
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
    health._ready_cache.update(at=0.0, report=None)
    path = logs.setup()
    yield path
    logs.setup()  # close the file before the folder goes away


def _check(rep: dict, name: str) -> dict:
    return next(c for c in rep["checks"] if c["name"] == name)


# ---------- the log ----------
def test_api_calls_and_telegram_calls_go_to_the_json_log(client, tg, log_file):
    client.post("/api/send", json={"post": BLOCKS_POST}, headers={"X-Studio-Client": "mcp:test"})
    client.get("/api/health")  # polled endpoints stay out of the log
    logs.flush()
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
    logs.flush()
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
    logs.flush()
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


# ---------- one id per unit of work ----------
def test_request_id_ties_together_every_line_of_a_request(client, tg, log_file):
    r = client.post("/api/send", json={"post": BLOCKS_POST})
    rid = r.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", rid)  # a valid W3C trace id
    lines = client.get("/api/logs", params={"q": rid}).json()["lines"]
    assert {x["event"] for x in lines} == {"telegram.call", "http"}
    assert all(x["request_id"] == rid for x in lines)
    assert client.get("/api/drafts").headers["X-Request-ID"] != rid  # a new one per request


def test_callers_id_is_kept_and_a_traceparent_is_understood(client, log_file):
    assert client.get("/api/drafts", headers={"X-Request-ID": "agent-42"}).headers["X-Request-ID"] == "agent-42"
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    tp = f"00-{trace}-00f067aa0ba902b7-01"
    assert client.get("/api/drafts", headers={"traceparent": tp}).headers["X-Request-ID"] == trace
    bad = client.get("/api/drafts", headers={"X-Request-ID": "x" * 65 + "\n"}).headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9a-f]{32}", bad)  # never echo an unsafe header back
    zero = "00-" + "0" * 32 + "-00f067aa0ba902b7-01"
    assert client.get("/api/drafts", headers={"traceparent": zero}).headers["X-Request-ID"] != "0" * 32
    no_parent = f"00-{trace}-{'0' * 16}-01"
    assert client.get("/api/drafts", headers={"traceparent": no_parent}).headers["X-Request-ID"] != trace
    future = f"cc-{trace}-00f067aa0ba902b7-01-what-the-future-will-be"  # a newer version may add fields
    assert client.get("/api/drafts", headers={"traceparent": future}).headers["X-Request-ID"] == trace
    v00_extra = f"00-{trace}-00f067aa0ba902b7-01-extra"  # version 00 has exactly four fields
    assert client.get("/api/drafts", headers={"traceparent": v00_extra}).headers["X-Request-ID"] != trace


def test_background_rounds_and_updates_get_their_own_id(client, tg, log_file):
    import asyncio

    from services import scheduler

    pid = store.add_post("@test_channel", BLOCKS_POST, status="scheduled", scheduled_at=int(time.time()) - 5)["id"]
    with logs.bind(logs.new_id("scheduler-")):
        asyncio.run(scheduler.run_due_posts())
    call = next(x for x in logs.read() if x.get("event") == "telegram.call")
    assert call["request_id"].startswith("scheduler-") and store.get_post(pid)["status"] == "sent"
    logging.getLogger("rich-studio.updates").warning("outside any request")
    assert "request_id" not in logs.read("WARNING")[-1]


def test_each_incoming_update_is_logged_under_its_update_id(client, tg, log_file, monkeypatch):
    import asyncio

    from services import updates

    rounds = iter([[{"update_id": 7001, "message": {}}]])

    async def get_updates(method, params=None, timeout=60):
        try:
            return next(rounds)
        except StopIteration:
            raise asyncio.CancelledError from None  # stop the loop after one round

    async def boom(u):
        raise RuntimeError("bad update")

    monkeypatch.setattr(updates.tg, "call", get_updates)
    monkeypatch.setattr(updates, "handle_update", boom)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(updates.loop())
    err = next(x for x in logs.read("ERROR") if "update 7001 failed" in x["msg"])
    assert err["request_id"] == "update-7001" and "bad update" in err["exc"]


def test_problems_in_the_report_point_to_their_request(client, log_file, monkeypatch):
    from errors import StudioError
    from services import posts

    async def broken(*a, **kw):
        logging.getLogger("rich-studio.test").error("send broke")
        raise StudioError("нет")

    monkeypatch.setattr(posts, "send", broken)
    r = client.post("/api/send", json={"post": BLOCKS_POST}, headers={"X-Request-ID": "trace-me"})
    assert r.status_code == 400 and r.headers["X-Request-ID"] == "trace-me"
    err = next(r for r in health.report()["recent_problems"] if r["message"] == "send broke")
    assert err["request_id"] == "trace-me"


def test_latency_is_kept_per_endpoint_template(client, tg, log_file):
    for _ in range(3):
        pid = client.post("/api/send", json={"post": BLOCKS_POST}).json()["post"]["id"]
    client.get(f"/api/posts/{pid}/versions")
    lat = health.report()["metrics"]["http_latency"]
    assert lat["POST /api/send"]["n"] == 3
    assert "GET /api/posts/{pid}/versions" in lat and not any(pid in k for k in lat)
    tl = health.report()["metrics"]["telegram_latency"]["sendRichMessage"]
    assert tl["n"] == 3 and tl["p50_ms"] <= tl["p95_ms"] <= tl["max_ms"]


def test_readiness_is_public_and_says_only_which_checks_fail(client, log_file, monkeypatch):
    monkeypatch.setattr(settings.get(), "api_key", "secret")
    r = client.get("/readyz")
    assert r.status_code == 200 and r.json() == {"ready": True, "failing": []}
    monkeypatch.setitem(health.CHECKS, "disk", lambda: health._check("disk", "fail", "свободно 0.1 ГБ"))
    assert client.get("/readyz").status_code == 200  # anyone may poll it: the report is reused for 30 s
    health._ready_cache["at"] = 0.0
    r = client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"ready": False, "failing": ["disk"]}
    assert client.get("/api/health").status_code == 401  # the details stay behind the key


# ---------- writing off the event loop ----------
def test_timestamps_are_utc_and_since_filters_by_them(log_file):
    logging.getLogger("rich-studio.test").info("now")
    line = logs.read("INFO")[-1]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["ts"])
    assert line["ts"][:16] == time.strftime("%Y-%m-%dT%H:%M", time.gmtime(time.time()))[:16]
    assert logs.read("INFO", since=time.time() - 60)[-1]["msg"] == "now"
    assert logs.read("INFO", since=time.time() + 3600) == []


def test_the_caller_does_not_write_the_file_itself(log_file):
    import threading

    writers = []
    file_handler = next(w for w in logs._writers if isinstance(w, logging.handlers.RotatingFileHandler))
    real_emit = file_handler.emit

    def spy(record):
        writers.append(threading.current_thread().name)
        real_emit(record)

    file_handler.emit = spy
    try:
        try:
            raise ValueError("kept")
        except ValueError:
            logging.getLogger("rich-studio.test").exception("with traceback")
        logs.flush()
    finally:
        file_handler.emit = real_emit
    assert writers and threading.current_thread().name not in writers
    line = logs.read("ERROR")[-1]
    assert line["msg"] == "with traceback" and "ValueError: kept" in line["exc"]  # the traceback survives the queue


# ---------- health ----------
def test_health_of_a_fresh_studio_is_ok_where_it_matters(client, log_file):
    rep = client.get("/api/health").json()
    assert [c["name"] for c in rep["checks"]] == list(health.CHECKS)
    assert rep["version"] == settings.VERSION and rep["bot_api"].startswith("Bot API") and rep["time"].endswith("Z")
    for name in ("database", "telegram", "scheduler", "posts", "sends", "storage", "log", "loops"):
        assert _check(rep, name)["status"] == "ok", _check(rep, name)
    assert rep["status"] in ("ok", "warn") and isinstance(rep["problems"], list)
    assert set(rep["metrics"]) == {
        "uptime_s",
        "requests",
        "telegram_calls",
        "log_problems",
        "http_latency",
        "telegram_latency",
    }


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


# ---------- review fixes (security and growth) ----------
def test_token_echoed_by_a_proxy_page_never_reaches_health_or_log(client, log_file, monkeypatch):
    # a wrong TG_API_BASE or a proxy answers 404 with an HTML page that repeats the URL (with the token)
    import telegram.client as tc

    monkeypatch.setattr(settings.get(), "bot_token", TOKEN)

    for pad in (0, 248):  # the whole token, and one that the 300-character limit cuts in the middle

        async def page(url, params, files, timeout, pad=pad):
            import httpx

            path = "/bot" + url.split("/bot", 1)[1]
            return httpx.Response(404, text="<pre>Cannot POST " + "/" * pad + path + "</pre>")

        monkeypatch.setattr(tc, "_request", page)
        assert client.post("/api/raw/getMe", json={}).status_code == 502
        report = json.dumps(client.get("/api/health").json(), ensure_ascii=False)
        assert "AAHxxxx" not in report, pad
        assert "AAHxxxx" not in log_file.read_text("utf-8"), pad


def test_a_long_run_of_digits_does_not_stall_the_scrubber():
    started = time.monotonic()
    logs.scrub("1" * 60000)
    assert time.monotonic() - started < 0.2
    assert logs.scrub(f"/bot{TOKEN}/getMe") == "/bot<token>/getMe"


def test_path_in_the_access_line_is_short_and_printable(client, log_file):
    client.get("/x%1B[31mred" + "y" * 500)
    http = [x for x in logs.read() if x.get("event") == "http"][-1]
    assert "\x1b" not in http["path"] and "\\x1b" in http["path"] and len(http["path"]) <= 210


def test_unknown_http_methods_and_raw_methods_do_not_grow_metrics(client, log_file):
    for i in range(5):
        client.request(f"M{i}", "/api/drafts")
    assert not [k for k in metrics.http_ms if k.startswith("M")]
    for i in range(metrics.MAX_KEYS + 20):
        metrics.telegram_call(f"method{i}", True, ms=1)
    assert len(metrics.telegram_ms) <= metrics.MAX_KEYS + 1 and "other" in metrics.telegram_ms


def test_log_files_zero_still_rotates_and_stray_files_are_ignored(fresh_data, monkeypatch):
    cfg = settings.get()
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.log_dir / "studio.log.old").write_text("not ours")
    (cfg.log_dir / "studio.log.7").write_text("{}\n")  # left over from a larger LOG_FILES
    monkeypatch.setattr(cfg, "log_max_mb", 0.01)
    monkeypatch.setattr(cfg, "log_files", 0)
    logs.setup()
    assert not (cfg.log_dir / "studio.log.7").exists()
    for i in range(1000):
        logging.getLogger("rich-studio.test").info("line %d %s", i, "x" * 100)
    assert logs.disk_bytes() <= 2 * 0.01 * 1024 * 1024 + 1024
    assert logs.read("INFO", limit=1)[0]["msg"].startswith("line 999")
    monkeypatch.undo()
    logs.setup()


def test_log_is_readable_only_by_its_owner(log_file):
    logging.getLogger("rich-studio.test").info("x")
    assert os.stat(log_file).st_mode & 0o777 == 0o600


def test_since_out_of_range_is_refused_not_a_500(client, log_file):
    assert client.get("/api/logs", params={"since": 1e300}).status_code == 422
    assert logs.read(since=float("inf")) == []


def test_raw_chat_id_that_is_not_an_id_stays_out_of_the_log(client, tg, log_file):
    client.post("/api/raw/sendMessage", json={"chat_id": {"x": "MARKERPOSTTEXT"}, "text": "t"})
    assert "MARKERPOSTTEXT" not in log_file.read_text("utf-8")


def test_unhandled_error_keeps_its_request_id(client, log_file, monkeypatch):
    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "list_drafts", boom)
    r = client.get("/api/drafts", headers={"X-Request-ID": "boom-1"})
    assert r.status_code == 500 and r.headers["X-Request-ID"] == "boom-1"
    tb = next(x for x in logs.read("ERROR") if "exc" in x)
    assert tb["request_id"] == "boom-1" and "RuntimeError: boom" in tb["exc"]


def test_traceparent_follows_w3c(client, log_file):
    trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    get = lambda tp: client.get("/api/drafts", headers={"traceparent": tp}).headers["X-Request-ID"]  # noqa: E731
    assert get(f"01-{trace}-00f067aa0ba902b7-01-future") == trace  # later versions may add fields
    assert get(f"00-{trace.upper()}-00F067AA0BA902B7-01") != trace.upper()  # uppercase is invalid
    assert get(f"00-{trace}-{'0' * 16}-01") != trace  # an all-zero parent id is invalid


def test_watchers_own_error_line_does_not_count_as_a_log_error(log_file):
    logging.getLogger("rich-studio.health").error("x: ok → fail", extra={"event": "health.change"})
    assert health.log_problems()["status"] == "ok"
    logging.getLogger("rich-studio.test").error("real")
    for _ in range(40):
        logging.getLogger("rich-studio.test").warning("noise")
    assert health.log_problems()["status"] == "warn"  # warnings no longer push the error out


# ---------- SessionStart hook for Claude Code ----------
def _run_hook(tmp_path, status: int, body: dict | None, key_in_env: str = ""):
    """Run .claude/hooks/studio_health.py against a tiny local server that answers /api/health with `body`."""
    seen: dict = {}

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"], seen["key"] = self.path, self.headers.get("X-API-Key")
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body or {}).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / ".env").write_text("TG_BOT_TOKEN=x\nSTUDIO_API_KEY='from-dotenv'\n")
    env = {k: v for k, v in os.environ.items() if k != "STUDIO_API_KEY"}
    env.update(STUDIO_URL=f"http://127.0.0.1:{server.server_port}", CLAUDE_PROJECT_DIR=str(tmp_path))
    if key_in_env:
        env["STUDIO_API_KEY"] = key_in_env
    try:
        out = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / ".claude/hooks/studio_health.py")],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    finally:
        server.shutdown()
    return out, seen


def test_session_hook_says_one_line_when_the_studio_is_not_ok(tmp_path):
    report = {"status": "warn", "problems": ["posts: 1 постов за сутки требуют внимания", "backup: свежей копии нет"]}
    out, seen = _run_hook(tmp_path, 200, report)
    assert out.returncode == 0, out.stderr
    msg = json.loads(out.stdout)
    assert msg["systemMessage"] == msg["hookSpecificOutput"]["additionalContext"]
    assert msg["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert msg["systemMessage"].startswith("Rich Studio требует внимания: posts: 1 постов")
    assert "\n" not in msg["systemMessage"]
    assert seen == {"path": "/api/health", "key": "from-dotenv"}  # the key from .env goes in the header, not the output
    assert "from-dotenv" not in out.stdout


def test_session_hook_is_silent_when_ok_down_or_locked(tmp_path):
    out, seen = _run_hook(tmp_path, 200, {"status": "ok", "problems": []}, key_in_env="env-key")
    assert (out.returncode, out.stdout) == (0, "")
    assert seen["key"] == "env-key"  # the environment wins over .env
    out, _ = _run_hook(tmp_path, 401, {"detail": "нужен ключ"})
    assert (out.returncode, out.stdout) == (0, "")
    env = dict(os.environ, STUDIO_URL="http://127.0.0.1:9", CLAUDE_PROJECT_DIR=str(tmp_path))  # nothing listens there
    down = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / ".claude/hooks/studio_health.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert (down.returncode, down.stdout) == (0, "")


def test_session_hook_is_registered_next_to_the_format_hook():
    hooks = json.loads((ROOT / ".claude/settings.json").read_text())["hooks"]
    assert "studio_health.py" in hooks["SessionStart"][0]["hooks"][0]["command"]
    assert "format.py" in hooks["PostToolUse"][0]["hooks"][0]["command"]
