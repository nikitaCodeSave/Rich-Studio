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
# errors only, apart from `recent`: a burst of warnings (getUpdates while offline) must not push them out
errors: deque[dict] = deque(maxlen=RECENT_PROBLEMS)
# the health watcher logs a failing check at ERROR; counting that line as an error would keep "log" warn about itself
SELF_REPORTED = {"health.change"}
# the last Telegram call outcome: health says "Telegram fails" only while the newest call failed
telegram_last: dict[str, Any] = {"ok_at": None, "error": None}
# background loops: name -> {"interval": s, "beat_at": t, "errors": n, "last_error": {...}}
loops: dict[str, dict[str, Any]] = {}


HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
MAX_KEYS = 100  # distinct endpoints / Bot API methods; anything beyond is counted as "other"
LATENCY_KEPT = 200  # newest durations per endpoint / Bot API method: enough for p95, bounded memory
http_ms: defaultdict[str, deque[int]] = defaultdict(lambda: deque(maxlen=LATENCY_KEPT))  # "POST /api/send"
telegram_ms: defaultdict[str, deque[int]] = defaultdict(lambda: deque(maxlen=LATENCY_KEPT))


def http_observe(method: str, route: str, status: int, ms: int) -> None:
    """`route` is the template (/api/posts/{pid}), never the raw path: one entry per endpoint, not per id."""
    requests[f"{status // 100}xx"] += 1
    method = method if method in HTTP_METHODS else "OTHER"  # any token is a valid method to the HTTP parser
    http_ms[_key(http_ms, f"{method} {route}")].append(ms)


def _key(series: dict, key: str) -> str:
    """Keys come from requests (/api/raw/{method} takes any name): cap them so memory stays bounded."""
    return key if key in series or len(series) < MAX_KEYS else "other"


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


def telegram_call(
    method: str, ok: bool, error: str | None = None, ms: int | None = None, code: int | None = None
) -> None:
    method = _key(telegram_ms, method)
    telegram[f"{method} {'ok' if ok else 'error'}"] += 1
    if ms is not None:
        telegram_ms[method].append(ms)
    if ok:
        telegram_last["ok_at"] = time.time()
    else:
        telegram_last["error"] = {"at": time.time(), "method": method, "error": (error or "")[:300], "code": code}


def problem(level: str, logger: str, message: str, request_id: str | None = None, event: str | None = None) -> None:
    problems[level] += 1
    entry = {"at": int(time.time()), "level": level, "logger": logger, "message": message[:500]}
    if request_id:
        entry["request_id"] = request_id
    recent.append(entry)
    if level in ("ERROR", "CRITICAL") and event not in SELF_REPORTED:
        errors.append(entry)


def reset() -> None:
    """Tests start from zero."""
    for c in (requests, telegram, problems):
        c.clear()
    recent.clear()
    errors.clear()
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
