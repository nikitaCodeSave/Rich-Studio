"""All configuration in one place, read from the environment once.

Modules call `settings.get()` at use time (never at import time), so tests and
`create_app(Settings(...))` can swap the configuration.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

_OFF = ("off", "0", "false", "no")


def _flag(env, name: str, default: str = "on") -> bool:
    return env.get(name, default).strip().lower() not in _OFF


@dataclass
class Settings:
    data_dir: Path = Path("/data")
    bot_token: str = ""
    default_chat: str = ""
    preview_chat: str = ""
    api_key: str = ""
    api_base: str = "https://api.telegram.org"
    # host names the service answers to (DNS rebinding guard)
    allowed_hosts: set[str] = field(default_factory=lambda: {"localhost", "127.0.0.1", "[::1]", "::1"})
    # getUpdates loop: button clicks, first comments, reactions, poll results
    updates_on: bool = True
    # background loops (spec refresh, scheduler, updates); tests turn them off and drive them directly
    background: bool = True
    spec_url: str = "https://raw.githubusercontent.com/PaulSonOfLars/telegram-bot-api-spec/main/api.json"
    spec_refresh_hours: float = 12.0
    # Bot API accepts uploads up to 50 MB; a self-hosted Bot API server (TG_API_BASE) up to 2000 MB
    max_upload_mb: float = 50.0
    # MCP tools that post to a channel (publish_post, schedule_post, edit_post); off = agents only draft and preview
    mcp_publish: bool = True
    # a scheduled post later than this (the service or the computer was off) is not sent but marked "missed";
    # 0 = send however late
    schedule_late_min: float = 60.0
    # daily copies of the database in DATA_DIR/backups, the newest N are kept; 0 = off
    backup_keep: int = 7

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "studio.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        s = cls()
        s.data_dir = Path(env.get("DATA_DIR", "/data"))
        s.bot_token = env.get("TG_BOT_TOKEN", "").strip()
        s.default_chat = env.get("TG_DEFAULT_CHAT", "").strip()
        s.preview_chat = env.get("TG_PREVIEW_CHAT", "").strip()
        s.api_key = env.get("STUDIO_API_KEY", "").strip()
        s.api_base = env.get("TG_API_BASE", s.api_base).rstrip("/")
        s.allowed_hosts |= {h.strip().lower() for h in env.get("ALLOWED_HOSTS", "").split(",") if h.strip()}
        s.updates_on = _flag(env, "TG_UPDATES")
        s.background = _flag(env, "STUDIO_BACKGROUND")
        s.spec_url = env.get("SPEC_URL", s.spec_url)
        s.spec_refresh_hours = float(env.get("SPEC_REFRESH_HOURS", "12"))
        own_server = s.api_base != cls.api_base
        s.max_upload_mb = float(env.get("MAX_UPLOAD_MB", "2000" if own_server else "50"))
        s.mcp_publish = _flag(env, "MCP_PUBLISH")
        s.schedule_late_min = float(env.get("SCHEDULE_LATE_MIN", "60"))
        s.backup_keep = int(env.get("BACKUP_KEEP", "7"))
        return s


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
