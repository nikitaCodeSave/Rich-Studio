"""Logging: short lines on stderr (docker logs) and JSON lines in DATA_DIR/logs with a size cap.

The file is what agents read (GET /api/logs, MCP studio_logs): one JSON object per line with ts, level,
logger, msg and the fields passed as `extra=`. Rotation keeps at most LOG_MAX_MB × (LOG_FILES + 1) on disk.
The bot token never reaches either output: every line is scrubbed after formatting.

Writing happens off the event loop: loggers hand records to a QueueHandler, and a QueueListener thread
formats and writes them (the logging cookbook's advice for async apps). `flush()` waits until it caught up.
"""

import atexit
import contextlib
import contextvars
import copy
import json
import logging
import logging.handlers
import queue
import re
import secrets
import time
from collections import deque
from collections.abc import Iterator
from pathlib import Path

import metrics
import settings

ROOT = "rich-studio"
# other loggers whose warnings and errors belong in the same file: uvicorn reports crashes of a request there
FOREIGN = ("uvicorn.error",)
FILE_NAME = "studio.log"
_TOKEN = re.compile(r"\d{5,}:[\w-]{30,}")  # a Bot API token anywhere in the text, not only in URLs
# attributes every LogRecord has; anything else came from extra= and goes into the JSON line
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_installed: list[logging.Handler] = []
_file: Path | None = None
_listener: logging.handlers.QueueListener | None = None
_queue: queue.Queue[logging.LogRecord] | None = None
_writers: list[logging.Handler] = []  # what the listener thread writes to


# One id per unit of work (an HTTP request, a round of a background loop), on every line it logs: Bot API
# calls, errors and the access line of the same request share it, and `GET /api/logs?q=<id>` shows them together.
request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_ID = re.compile(r"[A-Za-z0-9._:-]{1,64}")
# W3C Trace Context: version-traceid-parentid-flags; a version above 00 may append fields after the flags
_TRACEPARENT = re.compile(r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}(-.*)?")


def new_id(prefix: str = "") -> str:
    """A background round: prefix + 16 hex. Without a prefix, a valid W3C trace id (32 hex, random)."""
    return prefix + secrets.token_hex(8) if prefix else secrets.token_hex(16)


def _trace_id(traceparent: str) -> str | None:
    m = _TRACEPARENT.fullmatch(traceparent.strip().lower())
    if not m or m.group(1) == "ff" or (m.group(1) == "00" and m.group(4)):
        return None
    trace, parent = m.group(2), m.group(3)
    return trace if set(trace) != {"0"} and set(parent) != {"0"} else None  # all zeros are invalid ids


def incoming_id(x_request_id: str | None, traceparent: str | None) -> str:
    """The caller's id if it sent a sane one (X-Request-ID, or the trace id of a W3C traceparent), else a new one."""
    if x_request_id and _ID.fullmatch(x_request_id):
        return x_request_id
    if traceparent and (trace := _trace_id(traceparent)):
        return trace
    return new_id()


@contextlib.contextmanager
def bind(rid: str) -> Iterator[str]:
    token = request_id.set(rid)
    try:
        yield rid
    finally:
        request_id.reset(token)


class _WithRequestId(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id") and (rid := request_id.get()):
            record.request_id = rid
        return True


def scrub(text: str) -> str:
    return _TOKEN.sub("<token>", text)


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": _utc(record.created) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        line.update({k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")})
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:  # formatted already by the queue (see _Queue.prepare)
            line["exc"] = record.exc_text
        return scrub(json.dumps(line, ensure_ascii=False, default=str))


def _utc(t: float) -> str:
    """RFC 3339 in UTC (the caller adds the Z): sorts as text and has no daylight saving jumps."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))


class _Queue(logging.handlers.QueueHandler):
    """Hands a copy of the record to the listener thread with the traceback already formatted.

    The stock prepare() merges the traceback into msg; JsonLines keeps it in its own "exc" field instead.
    Extra fields (event, request_id, …) travel on the record as they are.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        out = copy.copy(record)
        out.msg, out.args = record.getMessage(), None
        if record.exc_info:
            out.exc_text = logging.Formatter().formatException(record.exc_info)
        out.exc_info = None
        return out


class Text(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


class _CountProblems(logging.Handler):
    """Warnings and errors feed the health report even when the file cannot be written."""

    def __init__(self):
        super().__init__(logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        metrics.problem(record.levelname, record.name, scrub(record.getMessage()), getattr(record, "request_id", None))


class _Forward(logging.Handler):
    """Hands warnings and errors of a foreign logger to our handlers (one file handler, one rotation)."""

    def __init__(self, targets: list[logging.Handler]):
        super().__init__(logging.WARNING)
        self.targets = targets

    def emit(self, record: logging.LogRecord) -> None:
        for h in self.targets:
            if record.levelno >= h.level:
                h.handle(record)


def flush() -> None:
    """Wait until the listener thread wrote everything logged so far (readers and tests call this)."""
    if _queue is not None:
        _queue.join()


def shutdown() -> None:
    global _listener, _queue  # noqa: PLW0603
    if _listener is not None:
        _listener.stop()  # writes what is left in the queue, then ends the thread
    _listener = _queue = None
    for h in _installed:
        for name in (ROOT, *FOREIGN):
            logging.getLogger(name).removeHandler(h)
        h.close()
    _installed.clear()
    for w in _writers:  # the file handler owns an open file
        w.close()
    _writers.clear()


def setup() -> Path | None:
    """(Re)install the handlers for the current settings; safe to call again (tests, a second create_app)."""
    global _file, _listener, _queue  # noqa: PLW0603 - one process-wide log
    cfg = settings.get()
    root = logging.getLogger(ROOT)
    shutdown()
    root.setLevel(cfg.log_level)
    root.propagate = False  # uvicorn's own config must not print our lines a second time

    err = logging.StreamHandler()
    err.addFilter(_NotFileOnly())
    err.setFormatter(Text("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    writers: list[logging.Handler] = [err]
    _file = None
    if cfg.log_max_mb > 0:
        path = cfg.log_dir / FILE_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            f = logging.handlers.RotatingFileHandler(
                path, maxBytes=int(cfg.log_max_mb * 1024 * 1024), backupCount=cfg.log_files, encoding="utf-8"
            )
        except OSError as e:  # a read-only data folder must not stop the studio
            err.handle(logging.makeLogRecord({"name": ROOT, "levelno": 30, "levelname": "WARNING", "msg": str(e)}))
        else:
            f.setFormatter(JsonLines())
            writers.append(f)
            _file = path
    _queue = queue.Queue()
    _writers.extend(writers)
    _listener = logging.handlers.QueueListener(_queue, *writers, respect_handler_level=True)
    _listener.start()
    to_queue = _Queue(_queue)
    count = _CountProblems()  # in memory and cheap: stays in the caller's thread, the report sees it at once
    for h in (to_queue, count):
        # the contextvar lives in the caller's context: read it here, the listener thread would not see it
        h.addFilter(_WithRequestId())  # handler filters see records of child loggers too
        root.addHandler(h)
        _installed.append(h)
    # httpx logs every request at INFO (getUpdates every 25 s); our telegram.call lines already say it all
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    targets: list[logging.Handler] = [count]
    if _file:
        to_file = _UvicornToFile(_queue)
        to_file.addFilter(_WithRequestId())
        targets.append(to_file)
    forward = _Forward(targets)
    for name in FOREIGN:
        logging.getLogger(name).addHandler(forward)
    _installed.append(forward)
    return _file


class _UvicornToFile(_Queue):
    """uvicorn already prints its errors to stderr: send its records to the file only."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        out = super().prepare(record)
        out._file_only = True
        return out


class _NotFileOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "_file_only", False)


atexit.register(shutdown)


def current_file() -> Path | None:
    return _file


def files() -> list[Path]:
    """The live file and its rotated copies, oldest first."""
    if _file is None:
        return []
    rotated = sorted(_file.parent.glob(FILE_NAME + ".*"), key=lambda p: -int(p.suffix[1:] or 0))
    return [p for p in [*rotated, _file] if p.exists()]


def disk_bytes() -> int:
    return sum(p.stat().st_size for p in files())


def read(
    level: str = "INFO", limit: int = 100, since: float | None = None, logger: str = "", text: str = ""
) -> list[dict]:
    """The newest `limit` lines at `level` or above, oldest first; optional filters by time, logger prefix, text."""
    floor = LEVELS.index(level.upper()) if level.upper() in LEVELS else 1
    flush()
    since_ts = _utc(since) if since else ""
    out: deque[dict] = deque(maxlen=max(1, limit))
    for path in files():
        with path.open(encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if (
                    line.get("level") in LEVELS
                    and LEVELS.index(line["level"]) >= floor
                    and line.get("ts", "") >= since_ts
                    and line.get("logger", "").startswith(logger)
                    and (not text or text.lower() in raw.lower())
                ):
                    out.append(line)
    return list(out)
