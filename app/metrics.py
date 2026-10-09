"""In-process counters, latencies and heartbeats for the health report: since the start of the process, never persisted.

A top-level module like settings.py, so every layer (telegram/ included) can count without importing services/.
"""

import math
import statistics
import time
from collections import Counter, defaultdict, deque
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


LATENCY_KEPT = 200  # newest durations per endpoint / Bot API method: enough for p95, bounded memory
http_ms: defaultdict[str, deque[int]] = defaultdict(lambda: deque(maxlen=LATENCY_KEPT))  # "POST /api/send"
telegram_ms: defaultdict[str, deque[int]] = defaultdict(lambda: deque(maxlen=LATENCY_KEPT))


def http_observe(method: str, route: str, status: int, ms: int) -> None:
    """`route` is the template (/api/posts/{pid}), never the raw path: one entry per endpoint, not per id."""
    requests[f"{status // 100}xx"] += 1
    http_ms[f"{method} {route}"].append(ms)


def _latency(series: dict[str, deque[int]]) -> dict[str, dict]:
    out = {}
    for key, values in sorted(series.items()):
        ordered = sorted(values)
        p95 = ordered[min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1)]
        out[key] = {"n": len(ordered), "p50_ms": int(statistics.median(ordered)), "p95_ms": p95, "max_ms": ordered[-1]}
    return out


def beat(loop: str, interval: float, error: str | None = None) -> None:
    """A background loop finished one round (with an error, if `error`)."""
    now = time.time()
    entry = loops.setdefault(loop, {"interval": interval, "beat_at": None, "errors": 0, "last_error": None})
    entry["interval"], entry["beat_at"] = interval, now
    if error:
        entry["errors"] += 1
        entry["last_error"] = {"at": int(now), "error": error[:300]}


def telegram_call(method: str, ok: bool, error: str | None = None, ms: int | None = None) -> None:
    telegram[f"{method} {'ok' if ok else 'error'}"] += 1
    if ms is not None:
        telegram_ms[method].append(ms)
    if ok:
        telegram_last["ok_at"] = time.time()
    else:
        telegram_last["error"] = {"at": time.time(), "method": method, "error": (error or "")[:300]}


def problem(level: str, logger: str, message: str, request_id: str | None = None) -> None:
    problems[level] += 1
    entry = {"at": int(time.time()), "level": level, "logger": logger, "message": message[:500]}
    if request_id:
        entry["request_id"] = request_id
    recent.append(entry)


def reset() -> None:
    """Tests start from zero."""
    for c in (requests, telegram, problems):
        c.clear()
    recent.clear()
    loops.clear()
    http_ms.clear()
    telegram_ms.clear()
    telegram_last.update(ok_at=None, error=None)


def snapshot() -> dict:
    return {
        "uptime_s": int(time.time() - STARTED_AT),
        "requests": dict(requests),
        "telegram_calls": dict(telegram),
        "log_problems": dict(problems),
        # over the newest LATENCY_KEPT calls of each endpoint and Bot API method
        "http_latency": _latency(http_ms),
        "telegram_latency": _latency(telegram_ms),
    }
