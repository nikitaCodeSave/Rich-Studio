"""Structural validation of a rich message against the spec, plus Telegram's size limits."""

import re
from urllib.parse import urlsplit

from .parse import enum_values
from .source import Spec

PRIMITIVES = {"String", "Integer", "Float", "Boolean", "True", "InputFile"}


class Validator:
    """Structural validation of a payload against the spec.

    Unknown fields and unknown types produce warnings, not errors: if the
    cached spec is older than Telegram, the payload is still sent as is.
    """

    def __init__(self, spec: Spec):
        self.spec = spec
        self.errors: list = []
        self.warnings: list = []

    def check(self, value, types: list, path: str):
        if not types:
            return
        # pick the first type that matches without errors
        if len(types) == 1:
            return self._check_one(value, types[0], path)
        best = None
        for t in types:
            sub = Validator(self.spec)
            sub._check_one(value, t, path)
            if not sub.errors:
                self.warnings += sub.warnings
                return
            if best is None or len(sub.errors) < len(best.errors):
                best = sub
        if best:
            self.errors.append(
                f"{path}: не подходит ни под один из типов {', '.join(types)} (ближайший: {best.errors[0]})"
            )

    def _check_one(self, value, t: str, path: str):
        if t.startswith("Array of "):
            if not isinstance(value, list):
                self.errors.append(f"{path}: ожидался массив ({t})")
                return
            inner = t[len("Array of ") :]
            for i, v in enumerate(value):
                self._check_one(v, inner, f"{path}[{i}]")
            return
        if t == "String":
            if not isinstance(value, str):
                self.errors.append(f"{path}: ожидалась строка")
            return
        if t == "Integer":
            if not (isinstance(value, int) and not isinstance(value, bool)):
                self.errors.append(f"{path}: ожидалось целое число")
            return
        if t == "Float":
            if not (isinstance(value, (int, float)) and not isinstance(value, bool)):
                self.errors.append(f"{path}: ожидалось число")
            return
        if t in ("Boolean", "True"):
            if not isinstance(value, bool):
                self.errors.append(f"{path}: ожидалось true/false")
            return
        if t == "InputFile":
            if not isinstance(value, str):
                self.errors.append(f"{path}: ожидался файл (URL, file_id или attach://)")
            return
        spec_t = self.spec.types.get(t)
        if spec_t is None:
            self.warnings.append(f"{path}: тип {t} не найден в спецификации, не проверяю")
            return
        subtypes = spec_t.get("subtypes")
        if subtypes:
            # abstract/union type: String and arrays are allowed members (RichText)
            if isinstance(value, dict):
                cand = [s for s in subtypes if s in self.spec.types]
                tagged = []
                for s in cand:
                    c = self.spec.type_const(s)
                    if c and value.get(c[0]) == c[1]:
                        tagged.append(s)
                if tagged:
                    return self.check(value, tagged, path)
                kind = value.get("type")
                obj_cand = [s for s in cand if not self.spec.type_const(s)]
                if obj_cand:
                    return self.check(value, obj_cand, path)
                self.errors.append(
                    f"{path}: неизвестный type={kind!r} для {t}; "
                    f"допустимо: {', '.join(c[1] for s in cand if (c := self.spec.type_const(s)))}"
                )
                return
            return self.check(value, subtypes, path)
        if not isinstance(value, dict):
            self.errors.append(f"{path}: ожидался объект {t}")
            return
        fields = {f["name"]: f for f in spec_t.get("fields", [])}
        for name, f in fields.items():
            if f["required"] and name not in value:
                self.errors.append(f"{path}: нет обязательного поля {name} ({t})")
        for k, v in value.items():
            f = fields.get(k)
            if f is None:
                self.warnings.append(f"{path}.{k}: поля нет в {t} (спецификация {self.spec.version})")
                continue
            const = self.spec.type_const(t)
            if const and const[0] == k:
                if v != const[1]:
                    self.errors.append(f"{path}.{k}: должно быть {const[1]!r}, а не {v!r}")
                continue
            if f["required"] and f["types"] == ["RichText"] and (v == "" or v == []):
                self.warnings.append(f"{path}.{k}: пустой текст, Telegram может отклонить блок")
            if k == "url" and (bad := url_error(v)):
                # seen only for keyboard buttons (an error there); here Telegram's verdict is unchecked
                self.warnings.append(f"{path}.{k}: {bad}")
            ev = enum_values(f.get("description", ""))
            if ev and isinstance(v, str) and v not in ev and len(ev) > 1:
                self.warnings.append(f"{path}.{k}: значение {v!r}, ожидалось одно из {ev}")
            self.check(v, f["types"], f"{path}.{k}")


def url_error(url) -> str | None:
    """Why Telegram would reject a button link: it answers 400 "URL host is empty" to "https://"."""
    if not isinstance(url, str):
        return None
    if not url.strip():
        return "пустая ссылка"
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
    except ValueError:
        return f"ссылка {url!r} не разбирается"
    if parts.scheme.lower() in ("http", "https") and not host:
        return f"в ссылке {url!r} нет адреса сайта"
    return None


# InlineKeyboardButton fields that only change how a button looks; any other field (a newer one too) is its action
BUTTON_LOOKS = {"text", "icon_custom_emoji_id", "style", "disabled"}


def keyboard_errors(markup) -> list[str]:
    """Buttons under the post whose links Telegram would reject (the whole post fails, not only the button)."""
    rows = markup.get("inline_keyboard") if isinstance(markup, dict) else None
    errors = []
    for ri, row in enumerate(rows if isinstance(rows, list) else []):
        for bi, button in enumerate(row if isinstance(row, list) else []):
            if not isinstance(button, dict):
                continue
            where = f"кнопка «{button.get('text', '')}» (ряд {ri + 1}, №{bi + 1})"
            if not set(button) - BUTTON_LOOKS:
                errors.append(f"{where}: не задано, что она делает (ссылка, ответ на нажатие, копирование…)")
            elif "url" in button and (bad := url_error(button["url"])):
                if bad == "пустая ссылка":  # often a button meant to be of another kind, with the link field left over
                    bad += ": впишите адрес или выберите кнопке другой тип (уберите поле url)"
                errors.append(f"{where}: {bad}")
    return errors


MEDIA_REF_RE = re.compile(r"tg://(photo|video|audio|document|animation|voice(?:_note)?)\?id=([\w\-.]+)")


def rich_text_len(rt) -> int:
    if rt is None:
        return 0
    if isinstance(rt, str):
        return len(rt)
    if isinstance(rt, list):
        return sum(rich_text_len(x) for x in rt)
    if isinstance(rt, dict):
        return rich_text_len(rt.get("text"))
    return 0


def _walk_blocks(blocks, depth=1, stats=None):
    stats = stats if stats is not None else {"blocks": 0, "depth": 0, "chars": 0}
    stats["depth"] = max(stats["depth"], depth)
    if not isinstance(blocks, list):
        return stats
    for b in blocks:
        if not isinstance(b, dict):
            continue
        stats["blocks"] += 1
        for key in ("text", "summary", "caption", "credit"):
            v = b.get(key)
            if isinstance(v, dict) and "text" in v and "type" not in v:
                stats["chars"] += rich_text_len(v.get("text")) + rich_text_len(v.get("credit"))
            else:
                stats["chars"] += rich_text_len(v)
        if "blocks" in b:
            _walk_blocks(b["blocks"], depth + 1, stats)
        items = b.get("items")
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict):
                _walk_blocks(it.get("blocks"), depth + 1, stats)
        cells = b.get("cells")
        for row in cells if isinstance(cells, list) else []:
            for c in row if isinstance(row, list) else []:
                if isinstance(c, dict):
                    stats["chars"] += rich_text_len(c.get("text"))
    return stats


# Telegram allows ~16 levels of blocks; far deeper JSON is a broken payload, and walking it
# recursively would hit Python's recursion limit (a 500 instead of a clear error)
MAX_JSON_DEPTH = 200


def _json_depth(value) -> int:
    depth, level = 0, [value]
    while level:
        depth += 1
        if depth > MAX_JSON_DEPTH:
            return depth
        nxt: list = []
        for x in level:
            if isinstance(x, dict):
                nxt.extend(x.values())
            elif isinstance(x, list):
                nxt.extend(x)
        level = nxt
    return depth - 1


def validate_rich_message(spec: Spec, rm: dict) -> dict:
    v = Validator(spec)
    if not isinstance(rm, dict):
        return {"ok": False, "errors": ["rich_message должен быть объектом"], "warnings": [], "stats": {}}
    if _json_depth(rm) > MAX_JSON_DEPTH:
        return {"ok": False, "errors": [f"вложенность больше {MAX_JSON_DEPTH} уровней"], "warnings": [], "stats": {}}
    present = [k for k in ("html", "markdown", "blocks") if rm.get(k) not in (None, "", [])]
    if len(present) != 1:
        v.errors.append(f"нужно ровно одно из html / markdown / blocks, сейчас: {present or 'ничего'}")
    v.check(rm, ["InputRichMessage"], "rich_message")
    stats = {}
    media_ids = {m.get("id") for m in rm.get("media", []) or [] if isinstance(m, dict)}
    if rm.get("blocks"):
        stats = _walk_blocks(rm["blocks"])
    else:
        src = rm.get("html") or rm.get("markdown") or ""
        stats = {"chars": len(re.sub(r"<[^>]+>", "", src))}
        refs = MEDIA_REF_RE.findall(src)
        for kind, mid in refs:
            if mid not in media_ids:
                v.errors.append(f"в тексте есть tg://{kind}?id={mid}, но в media нет элемента с id={mid!r}")
        unused = media_ids - {mid for _, mid in refs}
        for mid in sorted(x for x in unused if x):
            v.warnings.append(f"media id={mid!r} нигде не используется в тексте")
    stats["media"] = len(media_ids)
    if stats.get("chars", 0) > 32768:
        v.errors.append(f"текст {stats['chars']} символов, лимит 32768")
    elif stats.get("chars", 0) > 8000:
        v.warnings.append("больше ~8000 символов: клиент свернёт пост под «Показать ещё»")
    if stats["media"] > 50:
        v.warnings.append(f"{stats['media']} медиа, по неофициальным данным лимит 50")
    if stats.get("blocks", 0) > 500:
        v.warnings.append(f"{stats['blocks']} блоков, по неофициальным данным лимит 500")
    if stats.get("depth", 0) > 16:
        v.warnings.append(f"вложенность {stats['depth']}, по неофициальным данным лимит 16")
    return {
        "ok": not v.errors,
        "errors": v.errors,
        "warnings": v.warnings,
        "stats": stats,
        "spec_version": spec.version,
    }
