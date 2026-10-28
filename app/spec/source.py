"""Bot API specification: download, cache and summarize for the editor.

The spec is the machine-readable api.json from
https://github.com/PaulSonOfLars/telegram-bot-api-spec (regenerated from
core.telegram.org on every Bot API release). Everything the editor shows
(block palette, field forms, inline entity types, validation) is derived
from it, so a new Bot API release shows up after a refresh without code
changes.
"""

import json
import time
from pathlib import Path

import httpx

import settings

from .coverage import gaps
from .diff import TRACK_METHODS, TRACK_PREFIXES, diff_specs
from .parse import check_spec_shape, enum_values, type_const, version_tuple

BUNDLED = Path(__file__).resolve().parent.parent / "bundled" / "api.json"


class Spec:
    def __init__(self):
        self.data: dict = {}
        self.source = ""
        self.loaded_at = 0.0
        self.changes: list = []
        # the last download attempt, shown in the editor: {"at": unix time, "error": str | None}
        self.last_check: dict | None = None

    # ---------- loading ----------
    def _cache_file(self) -> Path:
        return settings.get().data_dir / "api.json"

    def load_initial(self):
        """Use the newest valid copy: the cache, or the bundled one after an image upgrade. A pinned spec
        (SPEC_PIN) is only the bundled one: what was tested with the image is what runs."""
        best = None
        copies = [(self._cache_file(), "cache"), (BUNDLED, "bundled")]
        if settings.get().spec_pinned:
            copies = copies[1:]
        for path, src in copies:
            try:
                data = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):  # no cache yet, or a broken file: try the next copy
                continue
            if check_spec_shape(data):
                continue
            if best is None or version_tuple(data.get("version")) > version_tuple(best[0].get("version")):
                best = (data, src, path.stat().st_mtime)
        if best:
            self.data, self.source, self.loaded_at = best
        self._load_changes()

    async def refresh(self, force: bool = False) -> dict:
        """Download the newest spec. Returns a short report."""
        if settings.get().spec_pinned:  # not a failed check: health stays ok, the editor shows why
            return {
                "updated": False,
                "version": self.version,
                "pinned": True,
                "error": "спецификация закреплена (SPEC_PIN): новая версия приходит с обновлением образа",
            }
        if (
            not force
            and time.time() - self.loaded_at < settings.get().spec_refresh_hours * 3600
            and self.source == "remote"
        ):
            return {"updated": False, "version": self.version, "reason": "fresh"}
        report = await self._download()
        self.last_check = {"at": int(time.time()), "error": report.get("error")}
        return report

    async def _download(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(settings.get().spec_url)
                r.raise_for_status()
                new = r.json()
        except (httpx.HTTPError, ValueError) as e:  # network down or not JSON: keep what we have
            return {"updated": False, "version": self.version, "error": str(e)}
        problem = check_spec_shape(new)
        if problem:
            return {"updated": False, "version": self.version, "error": f"скачанная спецификация отклонена: {problem}"}
        if version_tuple(new.get("version")) < version_tuple(self.version):
            return {
                "updated": False,
                "version": self.version,
                "error": f"скачана более старая версия {new.get('version')}",
            }
        old = self.data
        diff = diff_specs(old, new) if old else []
        self._cache_file().parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False), "utf-8")
        tmp.replace(self._cache_file())
        changed = old.get("version") != new.get("version") or bool(diff)
        self.data, self.source, self.loaded_at = new, "remote", time.time()
        if changed and diff:
            self.changes.insert(
                0, {"from": old.get("version"), "to": new.get("version"), "at": int(time.time()), "items": diff}
            )
            self._save_changes()
        return {"updated": changed, "version": self.version, "changes": diff, "gaps": gaps(self.types)}

    def _changes_file(self) -> Path:
        return settings.get().data_dir / "spec_changes.json"

    def _load_changes(self):
        try:
            self.changes = json.loads(self._changes_file().read_text("utf-8"))
        except (OSError, ValueError):
            self.changes = []

    def _save_changes(self):
        self._changes_file().write_text(json.dumps(self.changes[:50], ensure_ascii=False, indent=1), "utf-8")

    # ---------- accessors ----------
    @property
    def version(self) -> str:
        return self.data.get("version", "?")

    @property
    def types(self) -> dict:
        return self.data.get("types", {})

    @property
    def methods(self) -> dict:
        return self.data.get("methods", {})

    def type_const(self, type_name: str):
        """For subtypes like InputRichBlockParagraph: (field, value) of the constant `type` field."""
        return type_const(self.types, type_name)

    def summary(self) -> dict:
        """Compact description of everything the editor needs."""
        out_types = {}
        for name, t in self.types.items():
            if not name.startswith(TRACK_PREFIXES) and name not in (
                "Location",
                "LoginUrl",
                "SwitchInlineQueryChosenChat",
                "WebAppInfo",
                "User",
                "RichBlockTableCell",
                "RichBlockCaption",
            ):
                continue
            fields = []
            for f in t.get("fields", []):
                fields.append(
                    {
                        "name": f["name"],
                        "types": f["types"],
                        "required": f["required"],
                        "description": f.get("description", ""),
                        "enum": enum_values(f.get("description", "")),
                    }
                )
            c = self.type_const(name)
            out_types[name] = {
                "description": " ".join(t.get("description", [])),
                "fields": fields,
                "subtypes": t.get("subtypes", []),
                "const": c[1] if c else None,
                "href": t.get("href"),
            }
        methods = {}
        for m in TRACK_METHODS:
            if m in self.methods:
                mm = self.methods[m]
                methods[m] = {
                    "description": " ".join(mm.get("description", [])),
                    "fields": [
                        {
                            "name": f["name"],
                            "types": f["types"],
                            "required": f["required"],
                            "description": f.get("description", ""),
                        }
                        for f in mm.get("fields", [])
                    ],
                    "href": mm.get("href"),
                }
        return {
            "version": self.version,
            "release_date": self.data.get("release_date"),
            "changelog": self.data.get("changelog"),
            "source": self.source,
            "loaded_at": int(self.loaded_at),
            "types": out_types,
            "methods": methods,
            "changes": self.changes[:10],
            # what labels.js and render.js do not know yet in this version
            "gaps": gaps(self.types),
            "last_check": self.last_check,
        }
