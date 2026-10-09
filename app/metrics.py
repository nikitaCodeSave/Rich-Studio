"""In-process counters and heartbeats for the health report: since the start of the process, never persisted.

A top-level module like settings.py, so every layer (telegram/ included) can count without importing services/.
"""

import time
from collections import Counter, deque
from typing import Any

STARTED_AT = time.time()
RECENT_PROBLEMS = 30  # newest warnings and errors kept in memory for the health report

requests: Counter[str] = Counter()  # "2xx", "4xx", "5xx"
telegram: Counter[str] = Counter()  # "<method> ok" / "<method> error"
problems: Counter[str] = Counter()  # log records by level: WARNING, ERROR, CRITICAL
recent: deque[dict] = deque(maxlen=RECENT_PROBLEMS)
# the last Telegram call outcome: health says "Telegram fails" only while the newest call failed
telegram_last: dict[str, Any] = {"ok_at": None, "error": None}
# background loops: name -> {"interval": s, "beat_at": t, "errors": n, "last_error": {...}}
loops: dict[str, dict[str, Any]] = {}


def beat(loop: str, interval: float, error: str | None = None) -> None:
    """A background loop finished one round (with an error, if `error`)."""
    now = time.time()
    entry = loops.setdefault(loop, {"interval": interval, "beat_at": None, "errors": 0, "last_error": None})
    entry["interval"], entry["beat_at"] = interval, now
    if error:
        entry["errors"] += 1
        entry["last_error"] = {"at": int(now), "error": error[:300]}


def telegram_call(method: str, ok: bool, error: str | None = None) -> None:
    telegram[f"{method} {'ok' if ok else 'error'}"] += 1
    if ok:
        telegram_last["ok_at"] = time.time()
    else:
        telegram_last["error"] = {"at": time.time(), "method": method, "error": (error or "")[:300]}


def problem(level: str, logger: str, message: str) -> None:
    problems[level] += 1
    recent.append({"at": int(time.time()), "level": level, "logger": logger, "message": message[:500]})


def reset() -> None:
    """Tests start from zero."""
    for c in (requests, telegram, problems):
        c.clear()
    recent.clear()
    loops.clear()
    telegram_last.update(ok_at=None, error=None)


def snapshot() -> dict:
    return {
        "uptime_s": int(time.time() - STARTED_AT),
        "requests": dict(requests),
        "telegram_calls": dict(telegram),
        "log_problems": dict(problems),
    }
