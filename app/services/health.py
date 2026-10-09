"""Health report: what works, what needs a person, and what to do about it.

Each check returns {name, status: ok | warn | fail, summary, action?, details?}. `action` is written for
the person and for an agent alike: what to look at or which call fixes it. The report only reads; nothing
here changes data.
"""

import shutil
import time
from collections.abc import Callable
from typing import Any

import logs
import metrics
import settings
import spec
import store

from . import media, updates

ORDER = {"ok": 0, "warn": 1, "fail": 2}
DAY = 24 * 3600
DISK_WARN = 1024**3  # bytes free in DATA_DIR
DISK_FAIL = 200 * 1024**2
OVERDUE_GRACE = 120  # s past the time before a waiting scheduled post counts as stuck
UNUSED_MEDIA_WARN = 200 * 1024**2
RECENT_ERRORS = 3600  # errors in the log this recent make the "log" check warn
BACKUP_STALE = 36 * 3600


def _check(name: str, status: str, summary: str, action: str | None = None, details: dict | None = None) -> dict:
    out: dict[str, Any] = {"name": name, "status": status, "summary": summary}
    if action:
        out["action"] = action
    if details:
        out["details"] = details
    return out


def database() -> dict:
    have, want = store.stats.schema_ok()
    counts = store.stats.post_counts()
    if have != want:
        return _check(
            "database", "fail", f"схема базы {have}, код ждёт {want}", "перезапустите студию: миграции идут при старте"
        )
    return _check("database", "ok", f"постов: {sum(counts.values())}", details={"posts": counts})


def disk() -> dict:
    cfg = settings.get()
    free = shutil.disk_usage(cfg.data_dir).free
    text = f"свободно {free / 1024**3:.1f} ГБ в {cfg.data_dir}"
    if free < DISK_FAIL:
        return _check("disk", "fail", text, "освободите место: без него не сохранятся посты и черновики")
    if free < DISK_WARN:
        return _check("disk", "warn", text, "места мало: удалите ненужные файлы (POST /api/media/cleanup)")
    return _check("disk", "ok", text)


def telegram() -> dict:
    cfg = settings.get()
    if not cfg.bot_token:
        return _check("telegram", "warn", "TG_BOT_TOKEN не задан: публиковать нельзя", "задайте токен в .env")
    st = updates.STATUS
    last = metrics.telegram_last
    err = last["error"]
    details = {"updates": st.as_dict(), "last_error": err}
    if st.state == "conflict":
        return _check(
            "telegram",
            "fail",
            f"приём событий остановлен: {st.error}",
            "этого бота читает другой процесс или стоит вебхук: остановите его или задайте TG_UPDATES=off",
            details=details,
        )
    if err and (last["ok_at"] is None or err["at"] > last["ok_at"]):
        return _check(
            "telegram",
            "warn",
            f"последний вызов {err['method']} не удался: {err['error']}",
            "проверьте связь с api.telegram.org и права бота (GET /api/check)",
            details=details,
        )
    if st.state == "error":
        return _check("telegram", "warn", f"приём событий: {st.error}", "проверьте связь с Telegram", details=details)
    note = "приём событий выключен (TG_UPDATES=off)" if st.state == "off" else f"приём событий: {st.state}"
    return _check("telegram", "ok", note, details=details)


def _expected_loops() -> dict[str, float]:
    cfg = settings.get()
    from . import backup, housekeeping, scheduler

    loops: dict[str, float] = {"scheduler": scheduler.INTERVAL, "housekeeping": housekeeping.INTERVAL, "spec": 1800}
    if cfg.backup_keep > 0:
        loops["backup"] = backup.CHECK_INTERVAL
    if cfg.updates_on:
        loops["updates"] = 60
    return loops


def loops() -> dict:
    if not settings.get().background:
        return _check("loops", "ok", "фоновые циклы выключены (STUDIO_BACKGROUND=off)")
    now, uptime = time.time(), time.time() - metrics.STARTED_AT
    dead, failing = [], []
    for name, interval in _expected_loops().items():
        seen = metrics.loops.get(name)
        limit = 3 * interval + 300
        if (seen and now - seen["beat_at"] > limit) or (not seen and uptime > limit):
            dead.append(name)
        elif seen and seen["last_error"] and now - seen["last_error"]["at"] < 2 * interval + 60:
            failing.append(f"{name}: {seen['last_error']['error']}")
    if dead:
        return _check(
            "loops",
            "fail",
            "не отвечают: " + ", ".join(dead),
            "перезапустите студию (docker compose restart) и посмотрите ошибки в журнале",
            details={"loops": metrics.loops},
        )
    if failing:
        return _check(
            "loops",
            "warn",
            "; ".join(failing),
            "посмотрите журнал (GET /api/logs?level=ERROR)",
            details={"loops": metrics.loops},
        )
    return _check("loops", "ok", "работают", details={"loops": metrics.loops})


def scheduler() -> dict:
    late = store.stats.overdue(int(time.time()) - OVERDUE_GRACE)
    if not late:
        return _check("scheduler", "ok", "очередь в порядке")
    why = "" if settings.get().background else " (фоновые циклы выключены)"
    return _check(
        "scheduler",
        "warn",
        f"{len(late)} отложенных постов ждут дольше срока{why}",
        "проверьте, что студия работает без ошибок; GET /api/posts покажет очередь",
        details={"posts": late},
    )


def posts() -> dict:
    found = store.stats.problem_posts(int(time.time()) - DAY)
    if not found:
        return _check("posts", "ok", "за сутки без сбоев")
    return _check(
        "posts",
        "warn",
        f"{len(found)} постов за сутки требуют внимания (не ушли, пропущены или без первого комментария)",
        "прочитайте error у каждого; пропущенный отложите заново (POST /api/posts/{id}/reschedule) или опубликуйте",
        details={"posts": found},
    )


def sends() -> dict:
    found = store.stats.uncertain_sends(int(time.time()) - DAY)
    if not found:
        return _check("sends", "ok", "неясных отправок нет")
    return _check(
        "sends",
        "warn",
        f"{len(found)} отправок за сутки могли уйти, а могли нет",
        "посмотрите канал: если поста нет, отправьте заново с новым Idempotency-Key; повтор с прежним не пройдёт",
        details={"sends": found},
    )


def spec_source() -> dict:
    s, cfg = spec.current, settings.get()
    age_h = (time.time() - s.loaded_at) / 3600 if s.loaded_at else None
    details = {"version": s.version, "source": s.source, "last_check": s.last_check}
    if s.last_check and s.last_check.get("error") and (age_h is None or age_h > 2 * cfg.spec_refresh_hours):
        return _check(
            "spec",
            "warn",
            f"{s.version}: свежую спецификацию не скачать ({s.last_check['error']})",
            "проверьте доступ к SPEC_URL; редактор работает на сохранённой копии",
            details=details,
        )
    return _check("spec", "ok", f"{s.version} ({s.source})", details=details)


def backups() -> dict:
    cfg = settings.get()
    if cfg.backup_keep <= 0:
        return _check(
            "backup", "warn", "ежедневная копия базы выключена (BACKUP_KEEP=0)", "скачивайте zip: GET /api/export"
        )
    copies = sorted(cfg.backup_dir.glob("studio-*.db")) if cfg.backup_dir.exists() else []
    if not cfg.background:
        return _check("backup", "ok", f"копий: {len(copies)} (фоновые циклы выключены)")
    newest = copies[-1].stat().st_mtime if copies else None
    if (newest and time.time() - newest > BACKUP_STALE) or (not newest and time.time() - metrics.STARTED_AT > 2 * 3600):
        return _check(
            "backup",
            "warn",
            "свежей копии базы нет больше суток",
            "посмотрите журнал: GET /api/logs?logger=rich-studio.backup",
        )
    return _check("backup", "ok", f"копий: {len(copies)}, последняя {copies[-1].name if copies else 'ещё не снята'}")


def _size(path) -> int:
    if not path.exists():
        return 0
    return path.stat().st_size if path.is_file() else sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def storage() -> dict:
    cfg = settings.get()
    m = media.usage()
    used: dict[str, Any] = {
        "database_bytes": sum(_size(cfg.db_path.with_name(cfg.db_path.name + s)) for s in ("", "-wal")),
        "media": m,
        "backups_bytes": _size(cfg.backup_dir),
        "logs_bytes": logs.disk_bytes(),
        "post_versions": store.stats.version_count(),
    }
    cap = cfg.log_max_mb * 1024 * 1024 * (cfg.log_files + 1)
    if cap and used["logs_bytes"] > 1.5 * cap:
        return _check(
            "storage", "warn", "журнал больше заданного предела", "проверьте LOG_MAX_MB и LOG_FILES", details=used
        )
    unused = m["unused_bytes"]
    if unused > UNUSED_MEDIA_WARN:
        return _check(
            "storage",
            "warn",
            f"ненужные загрузки занимают {unused / 1024**2:.0f} МБ",
            "удалите их (POST /api/media/cleanup, кнопка «Удалить ненужные») или задайте MEDIA_UNUSED_DAYS",
            details=used,
        )
    total = used["database_bytes"] + m["bytes"] + used["backups_bytes"] + used["logs_bytes"]
    return _check("storage", "ok", f"данные занимают {total / 1024**2:.1f} МБ", details=used)


def log_problems() -> dict:
    since = time.time() - RECENT_ERRORS
    errors = [r for r in metrics.recent if r["level"] in ("ERROR", "CRITICAL") and r["at"] >= since]
    if errors:
        return _check(
            "log",
            "warn",
            f"ошибок в журнале за час: {len(errors)}; последняя: {errors[-1]['message']}",
            "GET /api/logs?level=ERROR покажет их с трассировкой",
            details={"errors": errors[-5:]},
        )
    return _check("log", "ok", "ошибок за час нет", details={"file": str(logs.current_file() or "не пишется")})


CHECKS: dict[str, Callable[[], dict]] = {
    "database": database,
    "disk": disk,
    "telegram": telegram,
    "loops": loops,
    "scheduler": scheduler,
    "posts": posts,
    "sends": sends,
    "spec": spec_source,
    "backup": backups,
    "storage": storage,
    "log": log_problems,
}


def report() -> dict:
    checks = []
    for name, fn in CHECKS.items():
        try:
            checks.append(fn())
        except Exception as e:  # noqa: BLE001 - a broken check is itself a finding, never a broken report
            checks.append(_check(name, "fail", f"проверка не выполнилась: {type(e).__name__}: {e}"))
    status = max((c["status"] for c in checks), key=ORDER.__getitem__)
    now = time.time()
    return {
        "status": status,
        # which build is answering: an agent comparing two reports must know whether the studio was upgraded
        "version": settings.VERSION,
        "bot_api": spec.current.version,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "checked_at": int(now),
        "problems": [f"{c['name']}: {c['summary']}" for c in checks if c["status"] != "ok"],
        "checks": checks,
        "metrics": metrics.snapshot(),
        "recent_problems": list(metrics.recent)[-10:],
    }
