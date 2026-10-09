"""Logging: short lines on stderr (docker logs) and JSON lines in DATA_DIR/logs with a size cap.

The file is what agents read (GET /api/logs, MCP studio_logs): one JSON object per line with ts, level,
logger, msg and the fields passed as `extra=`. Rotation keeps at most LOG_MAX_MB × (LOG_FILES + 1) on disk.
The bot token never reaches either output: every line is scrubbed after formatting.
"""

import json
import logging
import logging.handlers
import re
import time
from collections import deque
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


def scrub(text: str) -> str:
    return _TOKEN.sub("<token>", text)


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)) + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        line.update({k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")})
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return scrub(json.dumps(line, ensure_ascii=False, default=str))


class Text(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


class _CountProblems(logging.Handler):
    """Warnings and errors feed the health report even when the file cannot be written."""

    def __init__(self):
        super().__init__(logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        metrics.problem(record.levelname, record.name, scrub(record.getMessage()))


class _Forward(logging.Handler):
    """Hands warnings and errors of a foreign logger to our handlers (one file handler, one rotation)."""

    def __init__(self, targets: list[logging.Handler]):
        super().__init__(logging.WARNING)
        self.targets = targets

    def emit(self, record: logging.LogRecord) -> None:
        for h in self.targets:
            if record.levelno >= h.level:
                h.handle(record)


def setup() -> Path | None:
    """(Re)install the handlers for the current settings; safe to call again (tests, a second create_app)."""
    global _file  # noqa: PLW0603 - one process-wide log
    cfg = settings.get()
    root = logging.getLogger(ROOT)
    for h in _installed:
        for name in (ROOT, *FOREIGN):
            logging.getLogger(name).removeHandler(h)
        h.close()
    _installed.clear()
    root.setLevel(cfg.log_level)
    root.propagate = False  # uvicorn's own config must not print our lines a second time

    err = logging.StreamHandler()
    err.setFormatter(Text("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    handlers: list[logging.Handler] = [err, _CountProblems()]
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
            handlers.append(f)
            _file = path
    for h in handlers:
        root.addHandler(h)
        _installed.append(h)
    # httpx logs every request at INFO (getUpdates every 25 s); our telegram.call lines already say it all
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    forward = _Forward([h for h in handlers if h is not err])  # uvicorn prints to stderr itself
    for name in FOREIGN:
        logging.getLogger(name).addHandler(forward)
    _installed.append(forward)
    return _file


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
    since_ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(since)) if since else ""
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
