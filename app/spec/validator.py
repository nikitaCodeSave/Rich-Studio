"""Structural validation of a rich message against the spec, plus Telegram's size limits."""

import html
import re
import string
from urllib.parse import urlsplit

from .parse import enum_values
from .source import Spec

PRIMITIVES = {"String", "Integer", "Float", "Boolean", "True", "InputFile"}
# RichText types with `text` and one more required String field that is NOT the visible text again (a link target,
# an anchor or reference name); every other such type (email, phone, card, TON address, mention...) repeats its text.
# Mirrored in app/static/js/spec.js (NOT_TEXT_MIRROR); tests/test_spec_coverage.py keeps both in step.
NOT_TEXT_MIRROR = frozenset({"url", "anchor_link", "reference", "reference_link"})


def text_mirror_field(spec_t: dict, const) -> str | None:
    """The one required String field (besides type and text) that repeats the visible text, or None."""
    if not const or const[1] in NOT_TEXT_MIRROR:
        return None
    req = {f["name"]: f for f in spec_t.get("fields", []) if f["required"] and f["name"] != const[0]}
    if "text" not in req or len(req) != 2:
        return None
    (other,) = (f for n, f in req.items() if n != "text")
    return other["name"] if other["types"] == ["String"] else None


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
        best_key: tuple = (0, 0)
        for t in types:
            sub = Validator(self.spec)
            sub._check_one(value, t, path)
            if not sub.errors:
                self.warnings += sub.warnings
                return
            key = (len(sub.errors), self._shape_gap(value, t))
            if best is None or key < best_key:
                best, best_key = sub, key
        if best:
            self.errors.append(
                f"{path}: не подходит ни под один из типов {', '.join(types)} (ближайший: {best.errors[0]})"
            )

    def _shape_gap(self, value, t: str) -> int:
        """How far type t is from the shape of the value (0 fits). On equal error counts the nearest type
        is the one matching the shape, so its error is shown, not the first type's."""
        if isinstance(value, list):
            return 0 if t.startswith("Array of ") else 1
        if isinstance(value, dict):
            if t.startswith("Array of ") or t in PRIMITIVES:
                return 2
            const = self.spec.type_const(t)
            return 0 if const and value.get(const[0]) == const[1] else 1
        return 0 if t in PRIMITIVES else 1

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
            if v == "" and k == text_mirror_field(spec_t, const):
                self.warnings.append(
                    f"{path}.{k}: пустое значение, а текст есть: Telegram может отклонить или не распознать {t}"
                )
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


def method_errors(spec: Spec, method: str, params: dict, where: str, skip: frozenset = frozenset()) -> list[str]:
    """Parameters of a Bot API method against the spec: missing required ones and wrong types are errors;
    fields the spec does not know are left to Telegram (a newer Bot API may have them)."""
    m = spec.methods.get(method)
    if not m or not isinstance(params, dict):
        return []
    v = Validator(spec)
    for f in m.get("fields", []):
        name = f["name"]
        if name in skip:
            continue
        if name not in params:
            if f["required"]:
                v.errors.append(f"{where}: нет обязательного поля {name}")
            continue
        v.check(params[name], f["types"], f"{where}.{name}")
    return v.errors


MEDIA_REF_RE = re.compile(r"tg://(photo|video|audio|document|animation|voice(?:_note)?)\?id=([\w\-.]+)")


# Rich Message Limits, https://core.telegram.org/bots/api#rich-message-limits (Bot API 10.4). The spec (api.json) has
# no numbers for them, only this section of the documentation does, so they are written here:
#   Up to 32768 UTF-8 characters in the rich message text, including custom emoji alternative text and formula source.
#   Up to 500 blocks, including nested blocks, list items, ordered list items, table rows, quotation blocks, and
#   details blocks. Up to 16 levels of nested formatting and blocks. Up to 50 media attachments in total.
#   Up to 20 columns in a table.
# Characters are Unicode characters (Python len): the documentation says "bytes" or "UTF-16 code units" when it means
# them (callback_data, entity offsets).
MAX_CHARS = 32768
MAX_BLOCKS = 500
MAX_DEPTH = 16
MAX_MEDIA = 50
MAX_COLUMNS = 20
# String fields (not RichText) that the character limit counts: custom emoji alternative text and formula source
COUNTED_STRINGS = frozenset({"alternative_text", "expression"})
FOLD_CHARS = 8000  # not a limit: clients fold a longer post under «Показать ещё»
FOLDED = f"больше ~{FOLD_CHARS} символов: клиент свернёт пост под «Показать ещё»"

BLOCKS = "Array of InputRichBlock"
TABLE = "Array of Array of "


def _span(cell, key: str) -> int:
    n = cell.get(key) if isinstance(cell, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 1 else 1


def table_columns(rows: list) -> tuple[int, int]:
    """Columns of a table: the widest row by colspan, and the width when a cell with rowspan also takes its columns in
    the rows below, as in HTML (the documentation does not say whether Telegram counts them so)."""
    widest = grid = 0
    below: dict[int, int] = {}  # column -> rows below that a cell with rowspan still covers
    for row in rows:
        here, below = below, {k: n - 1 for k, n in below.items() if n > 1}
        col = 0
        for c in row:
            while col in here:
                col += 1
            # a span over the limit is too wide anyway; capped, a colspan of 10**9 cannot stall the loop
            span, down = min(_span(c, "colspan"), MAX_COLUMNS + 1), _span(c, "rowspan")
            if down > 1:
                below.update(dict.fromkeys(range(col, col + span), down - 1))
            col += span
        widest = max(widest, sum(_span(c, "colspan") for c in row))
        grid = max(grid, col, *(k + 1 for k in here))
    return widest, grid


class _Count:
    """What the rich message limits count in blocks, walked by the spec types of the fields, so a block newer than
    this code is counted too. A block is an element of an Array of InputRichBlock, a list item (an object holding
    blocks) or a table row. `depth` is the strict reading of "levels of nested formatting and blocks": a block,
    a list item, a table row and a piece of formatted text (any RichText object) each add a level. `nest` counts
    blocks inside blocks only, which every reading counts."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.blocks = self.chars = self.media = self.depth = self.nest = 0
        self.deepest = self.deepest_nest = "rich_message.blocks"
        self.tables: list[tuple[str, int, int]] = []  # (path of the table, widest row, width with rowspan)
        self._tags: dict[str, dict] = {}
        self._holders: dict[str, bool] = {}

    def _reach(self, level: int, where: str):
        if level > self.depth:
            self.depth, self.deepest = level, where

    def _tagged(self, union: str, value: dict) -> str | None:
        """The subtype of a union (InputRichBlock, RichText) that the value's constant field names."""
        if union not in self._tags:
            self._tags[union] = {}
            for s in self.spec.types.get(union, {}).get("subtypes", []):
                if c := self.spec.type_const(s):
                    self._tags[union].setdefault(c[0], {})[c[1]] = s
        for key, by_value in self._tags[union].items():
            if isinstance(tag := value.get(key), str) and tag in by_value:
                return by_value[tag]
        return None

    def _holds_blocks(self, t: str) -> bool:
        if t not in self._holders:
            self._holders[t] = any(BLOCKS in f["types"] for f in self.spec.types.get(t, {}).get("fields", []))
        return self._holders[t]

    def blocks_of(self, value, level: int, nest: int, path: str):
        for i, b in enumerate(value if isinstance(value, list) else []):
            if not isinstance(b, dict):
                continue
            where = f"{path}[{i}]"
            self.blocks += 1
            self._reach(level, where)
            if nest > self.nest:
                self.nest, self.deepest_nest = nest, where
            self.fields(b, self._tagged("InputRichBlock", b), level, nest, where, where)

    def fields(self, obj: dict, t: str | None, level: int, nest: int, path: str, where: str):
        spec_t = self.spec.types.get(t) if t else None
        if spec_t is None:  # a type newer than the spec: at least its text and nested blocks
            self.text(obj.get("text"), level, nest, where)
            self.field(obj.get("blocks"), "blocks", [BLOCKS], level, nest, f"{path}.blocks", where)
            return
        for f in spec_t.get("fields", []):
            if f["name"] in obj:
                self.field(obj[f["name"]], f["name"], f["types"], level, nest, f"{path}.{f['name']}", where)

    def field(self, v, name: str, types: list, level: int, nest: int, path: str, where: str):
        one = types[0] if len(types) == 1 else ""
        if "RichText" in types:
            self.text(v, level, nest, where)
        elif BLOCKS in types:
            self.blocks_of(v, level + 1, nest + 1, path)
        elif any(t.startswith("InputMedia") for t in types):
            self.media += 1
        elif name in COUNTED_STRINGS:
            self.chars += len(v) if isinstance(v, str) else 0
        elif one.startswith(TABLE) and isinstance(v, list):
            rows = [r for r in v if isinstance(r, list)]
            for ri, row in enumerate(rows):
                self.blocks += 1
                self._reach(level + 1, where)
                for ci, c in enumerate(row):
                    if isinstance(c, dict):
                        self.obj(c, one[len(TABLE) :], level + 1, nest, f"{path}[{ri}][{ci}]", where)
            self.tables.append((where, *table_columns(rows)))
        elif one.startswith("Array of ") and isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, dict):
                    self.obj(x, one[len("Array of ") :], level, nest, f"{path}[{i}]", where)
        elif one and isinstance(v, dict):
            self.obj(v, one, level, nest, path, where)

    def obj(self, x: dict, t: str, level: int, nest: int, path: str, where: str):
        """An object inside a block: a list item holds blocks and is a block itself, a caption or a button is not."""
        if self._holds_blocks(t):
            self.blocks += 1
            level, where = level + 1, path
            self._reach(level, where)
        self.fields(x, t, level, nest, path, where)

    def text(self, rt, level: int, nest: int, where: str):
        if isinstance(rt, str):
            self.chars += len(rt)
        elif isinstance(rt, list):
            for x in rt:
                self.text(x, level, nest, where)
        elif isinstance(rt, dict):
            self._reach(level + 1, where)
            self.fields(rt, self._tagged("RichText", rt), level + 1, nest, where, where)


def _block_limits(v: Validator, blocks: list) -> dict:
    """Limits of a message in blocks: everything the documentation counts is in them, so the counts are exact and
    going over is an error; only the readings the documentation leaves open are warnings."""
    n = _Count(v.spec)
    n.blocks_of(blocks, 1, 1, "rich_message.blocks")
    if n.chars > MAX_CHARS:
        v.errors.append(f"текст {n.chars} символов с alternative_text эмодзи и исходником формул, лимит {MAX_CHARS}")
    elif n.chars > FOLD_CHARS:
        v.warnings.append(FOLDED)
    if n.blocks > MAX_BLOCKS:
        v.errors.append(f"блоков вместе с пунктами списков и строками таблиц: {n.blocks}, лимит {MAX_BLOCKS}")
    if n.media > MAX_MEDIA:
        v.errors.append(f"медиа: {n.media}, лимит {MAX_MEDIA}")
    if n.nest > MAX_DEPTH:
        v.errors.append(f"{n.deepest_nest}: {n.nest} уровней блоков друг в друге, лимит {MAX_DEPTH}")
    elif n.depth > MAX_DEPTH:
        v.warnings.append(
            f"{n.deepest}: {n.depth} уровней вложенности вместе с пунктами списков, строками таблиц и форматированием "
            f"текста, лимит {MAX_DEPTH} (как именно Telegram считает уровни, документация не уточняет)"
        )
    for where, widest, grid in n.tables:
        if widest > MAX_COLUMNS:
            v.errors.append(f"{where}: колонок в таблице {widest}, лимит {MAX_COLUMNS}")
        elif grid > MAX_COLUMNS:
            v.warnings.append(
                f"{where}: колонок в таблице {grid}, если ячейка с rowspan занимает свои колонки и в строках ниже; "
                f"лимит {MAX_COLUMNS}"
            )
    return {"blocks": n.blocks, "depth": n.depth, "chars": n.chars, "media": n.media}


TAG_RE = re.compile(r"<[^>]+>")
MD_TARGET_RE = re.compile(r"\]\([^)]*\)")  # where a link or an image points: [text](url "title")
# surely not text in Markdown: footnote names, reference definitions, code fence lines, list numbers, checkboxes
MD_MARKUP_RE = re.compile(
    r"\[\^[^\]]*\]|^[ \t]*\[[^\]]+\]:.*$|^[ \t]*(?:```|~~~).*$|^[ \t]*\d+[.)][ \t]|\[[ xX]\]", re.M
)
HTTP_MEDIA_RE = re.compile(
    r"<(?:img|video|audio|tg-document)\b[^>]*\bsrc\s*=\s*[\"']?https?://|!\[[^\]]*\]\(\s*<?https?://", re.I
)


def source_chars(src: str, markdown: bool) -> tuple[int, int]:
    """Text characters of html or markdown as far as the source tells without Telegram's parser: about how many
    (without tags and link addresses), and at least how many (also without spaces and, in Markdown, markup)."""
    text = TAG_RE.sub("", src)
    if markdown:
        text = MD_TARGET_RE.sub("]", text)
    text = html.unescape(text)
    if not markdown:
        return len(text), sum(not ch.isspace() for ch in text)
    sure = MD_MARKUP_RE.sub("", text)
    return len(text), sum(not ch.isspace() and ch not in string.punctuation for ch in sure)


def _source_limits(v: Validator, rm: dict) -> dict:
    """Limits of a message in html or markdown: Telegram makes the blocks itself, so blocks, nesting and table columns
    are not counted here, and characters only as far as the source tells: an error only when surely over."""
    src = rm.get("html") or rm.get("markdown") or ""
    src = src if isinstance(src, str) else ""
    media = [m for m in rm["media"] if isinstance(m, dict)] if isinstance(rm.get("media"), list) else []
    media_ids = {m["id"] for m in media if isinstance(m.get("id"), str)}
    refs = MEDIA_REF_RE.findall(src)
    for kind, mid in refs:
        if mid not in media_ids:
            v.errors.append(f"в тексте есть tg://{kind}?id={mid}, но в media нет элемента с id={mid!r}")
    for mid in sorted(x for x in media_ids - {mid for _, mid in refs} if x):
        v.warnings.append(f"media id={mid!r} нигде не используется в тексте")
    chars, at_least = source_chars(src, markdown=not rm.get("html"))
    if at_least > MAX_CHARS:
        v.errors.append(f"текст не меньше {at_least} символов, лимит {MAX_CHARS}")
    elif chars > MAX_CHARS:
        v.warnings.append(
            f"текст около {chars} символов, лимит {MAX_CHARS}: точно его посчитает Telegram, когда разберёт разметку"
        )
    elif chars > FOLD_CHARS:
        v.warnings.append(FOLDED)
    count = max(len(media), len(refs) + len(HTTP_MEDIA_RE.findall(src)))
    if count > MAX_MEDIA:
        v.warnings.append(f"медиа около {count}, лимит {MAX_MEDIA}")
    return {"chars": chars, "media": count}


# Telegram allows 16 levels of nesting (MAX_DEPTH); far deeper JSON is a broken payload, and walking it
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
    stats = _block_limits(v, rm["blocks"]) if rm.get("blocks") else _source_limits(v, rm)
    return {
        "ok": not v.errors,
        "errors": v.errors,
        "warnings": v.warnings,
        "stats": stats,
        "spec_version": spec.version,
    }
