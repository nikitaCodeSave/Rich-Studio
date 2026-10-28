"""All configuration in one place, read from the environment once.

Modules call `settings.get()` at use time (never at import time), so tests and
`create_app(Settings(...))` can swap the configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = "0.1.0"  # the studio's own version: OpenAPI, /api/health
_OFF = ("off", "0", "false", "no")
_ON = ("on", "1", "true", "yes")
MODES = ("local", "server")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsError(ValueError):
    """The environment cannot be used as it is: every problem in one message, so one restart fixes them all."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("настройки не подходят:\n- " + "\n- ".join(problems))


def _flag(env, name: str, default: str = "on") -> bool:
    return env.get(name, default).strip().lower() not in _OFF


def _secret(env, name: str, problems: list[str]) -> str:
    """NAME, or the first line of the file NAME_FILE (Docker secrets); both set is a mistake."""
    path = env.get(f"{name}_FILE", "").strip()
    if not path:
        return env.get(name, "").strip()
    if env.get(name, "").strip():
        problems.append(f"заданы и {name}, и {name}_FILE: оставьте одно")
        return ""
    try:
        return Path(path).read_text("utf-8").strip()
    except OSError as e:
        problems.append(f"{name}_FILE: не читается файл {path} ({e.strerror})")
        return ""


def _number(env, name: str, default: float, problems: list[str], *, whole: bool = False) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw) if whole else float(raw)
    except ValueError:
        problems.append(f"{name}={raw!r}: нужно {'целое ' if whole else ''}число")
        return default
    if value < 0:
        problems.append(f"{name}={raw}: не может быть меньше 0")
        return default
    return value


@dataclass
class Settings:
    # local: a tool on this computer (127.0.0.1); server: reachable from the internet behind HTTPS, so the studio
    # refuses to start without a token or key, without an https STUDIO_URL, and keeps the spec pinned
    mode: str = "local"
    data_dir: Path = Path("/data")
    bot_token: str = ""
    default_chat: str = ""
    preview_chat: str = ""
    api_key: str = ""
    api_base: str = "https://api.telegram.org"
    # where the person opens the studio: agents get links to drafts and previews from it
    public_url: str = "http://localhost:8080"
    # upload_media / POST /api/media/fetch may download from local and private addresses (own servers, tests)
    media_fetch_private: bool = False
    # host names the service answers to (DNS rebinding guard)
    allowed_hosts: set[str] = field(default_factory=lambda: {"localhost", "127.0.0.1", "[::1]", "::1"})
    # getUpdates loop: button clicks, first comments, reactions, poll results
    updates_on: bool = True
    # background loops (spec refresh, scheduler, updates); tests turn them off and drive them directly
    background: bool = True
    spec_url: str = "https://raw.githubusercontent.com/PaulSonOfLars/telegram-bot-api-spec/main/api.json"
    spec_refresh_hours: float = 12.0
    # the spec comes only with the image (app/bundled/api.json, updated by PR): nothing is downloaded at run time;
    # on by default in server mode
    spec_pinned: bool = False
    # Bot API accepts uploads up to 50 MB; a self-hosted Bot API server (TG_API_BASE) up to 2000 MB
    max_upload_mb: float = 50.0
    # MCP tools that post to a channel (publish_post, schedule_post, send_poll, edit_post, reschedule_post,
    # cancel_scheduled_post); off = agents only draft and preview
    mcp_publish: bool = True
    # POST /api/raw/{method}: any Bot API method, for the admin level only; off = the route answers 403
    raw_api: bool = True
    # how long an editor login (POST /api/session, HttpOnly cookie) lasts
    session_days: float = 7.0
    # a scheduled post later than this (the service or the computer was off) is not sent but marked "missed";
    # 0 = send however late
    schedule_late_min: float = 60.0
    # daily copies of the database in DATA_DIR/backups, the newest N are kept; 0 = off
    backup_keep: int = 7
    # JSON log in DATA_DIR/logs: one file up to LOG_MAX_MB plus LOG_FILES rotated copies; 0 MB = stderr only
    log_level: str = "INFO"
    log_max_mb: float = 5.0
    log_files: int = 3
    # housekeeping of what the person made: uploads nothing mentions for N days are deleted (0 = never);
    # only the newest N versions of each published post are kept (0 = all)
    media_unused_days: float = 0.0
    versions_keep: int = 0
    # dead man's switch (healthchecks.io and the like): housekeeping opens this URL every 5 minutes, URL/fail
    # when the health report fails; silence means the studio or its background loops are down
    deadman_url: str = ""

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        """Read the environment; every unusable value is collected into one SettingsError."""
        env = os.environ if env is None else env
        problems: list[str] = []
        s = cls()
        s.mode = env.get("STUDIO_MODE", "local").strip().lower() or "local"
        if s.mode not in MODES:
            problems.append(f"STUDIO_MODE={s.mode!r}: бывает local или server")
        server = s.mode == "server"
        s.data_dir = Path(env.get("DATA_DIR", "/data"))
        s.bot_token = _secret(env, "TG_BOT_TOKEN", problems)
        s.default_chat = env.get("TG_DEFAULT_CHAT", "").strip()
        s.preview_chat = env.get("TG_PREVIEW_CHAT", "").strip()
        s.api_key = _secret(env, "STUDIO_API_KEY", problems)
        s.api_base = env.get("TG_API_BASE", s.api_base).rstrip("/")
        s.public_url = env.get("STUDIO_URL", s.public_url).strip().rstrip("/") or s.public_url
        s.allowed_hosts |= {h.strip().lower() for h in env.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
        s.updates_on = _flag(env, "TG_UPDATES")
        s.background = _flag(env, "STUDIO_BACKGROUND")
        s.spec_url = env.get("SPEC_URL", s.spec_url)
        s.spec_refresh_hours = _number(env, "SPEC_REFRESH_HOURS", 12, problems)
        s.spec_pinned = _flag(env, "SPEC_PIN", "on" if server else "off")
        own_server = s.api_base != cls.api_base
        s.media_fetch_private = env.get("MEDIA_FETCH_PRIVATE", "").strip().lower() in _ON
        s.max_upload_mb = _number(env, "MAX_UPLOAD_MB", 2000 if own_server else 50, problems)
        s.mcp_publish = _flag(env, "MCP_PUBLISH")
        s.raw_api = _flag(env, "RAW_API")
        s.session_days = _number(env, "SESSION_DAYS", 7, problems)
        s.schedule_late_min = _number(env, "SCHEDULE_LATE_MIN", 60, problems)
        s.backup_keep = int(_number(env, "BACKUP_KEEP", 7, problems, whole=True))
        s.log_level = env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if s.log_level not in LOG_LEVELS:
            problems.append(f"LOG_LEVEL={s.log_level!r}: бывает {', '.join(LOG_LEVELS)}")
        s.log_max_mb = _number(env, "LOG_MAX_MB", 5, problems)
        s.log_files = int(_number(env, "LOG_FILES", 3, problems, whole=True))
        s.media_unused_days = _number(env, "MEDIA_UNUSED_DAYS", 0, problems)
        s.versions_keep = int(_number(env, "VERSIONS_KEEP", 0, problems, whole=True))
        s.deadman_url = env.get("DEADMAN_URL", "").strip().rstrip("/")
        if s.deadman_url and not s.deadman_url.startswith(("https://", "http://")):
            problems.append("DEADMAN_URL: нужен адрес http(s)://")
        if server:
            problems += s._server_problems()
        if problems:
            raise SettingsError(problems)
        return s

    def _server_problems(self) -> list[str]:
        """What a studio open to the internet must have. Tokens live in the database: main checks them."""
        out = []
        if not self.public_url.startswith("https://"):
            out.append("STUDIO_MODE=server: STUDIO_URL должен быть https://… (адрес, который отдаёт Caddy)")
        else:  # the public name is the one the browser and agents send as Host
            self.allowed_hosts.add(self.public_url.split("://", 1)[1].split("/", 1)[0].lower())
        if not self.bot_token:
            out.append("STUDIO_MODE=server: не задан TG_BOT_TOKEN (или TG_BOT_TOKEN_FILE)")
        return out


_current: Settings | None = None


def get() -> Settings:
    global _current  # noqa: PLW0603 - one process-wide configuration
    if _current is None:
        _current = Settings.from_env()
    return _current


def use(s: Settings) -> Settings:
    global _current  # noqa: PLW0603
    _current = s
    return s
