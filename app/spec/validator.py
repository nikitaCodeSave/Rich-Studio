"""Structural validation of a rich message against the spec, plus Telegram's size limits."""

import html
import re
import string
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import NamedTuple
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
# How Telegram reads them was measured on the real Bot API 10.4 (2026-11-15, issue #170: sendRichMessage to a test
# group, each message deleted right away): characters are Unicode characters (32 768 latin letters pass, 32 769 are
# refused); a list is a block besides its items; a table is as wide as its widest row by colspan, a cell with rowspan
# takes no column in the rows below; levels of nesting are counted as _Count (blocks) and _HtmlWalk (html) say.
MAX_CHARS = 32768
MAX_BLOCKS = 500
MAX_DEPTH = 16
MAX_MEDIA = 50
MAX_COLUMNS = 20
# Not in the documentation, measured as above: a longer text is not refused but cut, without a word, by its size in
# UTF-8 (a Cyrillic letter takes 2 bytes, an emoji 4), in html, markdown and blocks alike and for the post as a whole;
# only what is left is then held to MAX_CHARS. One paragraph kept 34 993-34 996 bytes, two and three paragraphs kept
# 34 986. The limit is the least of them: over it the end of the post surely does not reach the reader.
MAX_TEXT_BYTES = 34986
# Two paragraphs already lost a few bytes more than one, and posts of many paragraphs were not measured: from here on
# a warning that the cut may come before MAX_TEXT_BYTES
NEAR_CUT_BYTES = 32768
# String fields (not RichText) that the character limit counts: custom emoji alternative text and formula source
COUNTED_STRINGS = frozenset({"alternative_text", "expression"})
FOLD_CHARS = 8000  # not a limit: clients fold a longer post under «Показать ещё»
FOLDED = f"больше ~{FOLD_CHARS} символов: клиент свернёт пост под «Показать ещё»"
CUT = (
    f"Telegram без ошибки обрежет всё, что дальше ~{MAX_TEXT_BYTES} байт, и конец поста не дойдёт до читателя "
    "(буква кириллицы — 2 байта, эмодзи — 4)"
)
EXACT_LATER = "точно его посчитает Telegram, когда разберёт разметку"

BLOCKS = "Array of InputRichBlock"
TABLE = "Array of Array of "


def utf8_size(s: str) -> int:
    return len(s.encode("utf-8", "surrogatepass"))  # a lone surrogate from JSON is not a reason for a 500


def _colspan(cell) -> int:
    n = cell.get("colspan") if isinstance(cell, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 1 else 1


def table_columns(rows: list) -> int:
    """Columns of a table: its widest row by colspan. A cell with rowspan takes no column in the rows below
    (measured: a row of 20 cells under a cell with rowspan passes, though in HTML the table is 21 columns wide)."""
    return max((sum(_colspan(c) for c in row) for row in rows), default=0)


class TextSize(NamedTuple):
    """The text of a post: about how many characters and UTF-8 bytes, and at least how many (in blocks the same)."""

    chars: int
    utf8: int
    sure_chars: int
    sure_utf8: int


class _Count:
    """What the rich message limits count in blocks, walked by the spec types of the fields, so a block newer than
    this code is counted too. A block is an element of an Array of InputRichBlock, a list item (an object holding
    blocks) or a table row. `depth` is the levels of nesting as Telegram counts them in blocks (measured, issue #170):
    the blocks on the way to a piece of text, the one holding it included, plus every RichText object around it
    (formatting, a link…). A list item and a table row are blocks for the count, but not levels."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.blocks = self.chars = self.utf8 = self.media = self.depth = 0
        self.deepest = "rich_message.blocks"
        self.tables: list[tuple[str, int]] = []  # (path of the table, its columns)
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

    def blocks_of(self, value, level: int, path: str):
        for i, b in enumerate(value if isinstance(value, list) else []):
            if not isinstance(b, dict):
                continue
            where = f"{path}[{i}]"
            self.blocks += 1
            self._reach(level, where)
            self.fields(b, self._tagged("InputRichBlock", b), level, where, where)

    def fields(self, obj: dict, t: str | None, level: int, path: str, where: str):
        spec_t = self.spec.types.get(t) if t else None
        if spec_t is None:  # a type newer than the spec: at least its text and nested blocks
            self.text(obj.get("text"), level, where)
            self.field(obj.get("blocks"), "blocks", [BLOCKS], level, f"{path}.blocks", where)
            return
        for f in spec_t.get("fields", []):
            if f["name"] in obj:
                self.field(obj[f["name"]], f["name"], f["types"], level, f"{path}.{f['name']}", where)

    def field(self, v, name: str, types: list, level: int, path: str, where: str):
        one = types[0] if len(types) == 1 else ""
        if "RichText" in types:
            self.text(v, level, where)
        elif BLOCKS in types:
            self.blocks_of(v, level + 1, path)
        elif any(t.startswith("InputMedia") for t in types):
            self.media += 1
        elif name in COUNTED_STRINGS:
            self.string(v)
        elif one.startswith(TABLE) and isinstance(v, list):
            rows = [r for r in v if isinstance(r, list)]
            for ri, row in enumerate(rows):
                self.blocks += 1
                for ci, c in enumerate(row):
                    if isinstance(c, dict):
                        self.obj(c, one[len(TABLE) :], level, f"{path}[{ri}][{ci}]", where)
            self.tables.append((where, table_columns(rows)))
        elif one.startswith("Array of ") and isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, dict):
                    self.obj(x, one[len("Array of ") :], level, f"{path}[{i}]", where)
        elif one and isinstance(v, dict):
            self.obj(v, one, level, path, where)

    def obj(self, x: dict, t: str, level: int, path: str, where: str):
        """An object inside a block: a list item holds blocks and is a block itself, a caption or a button is not."""
        if self._holds_blocks(t):
            self.blocks += 1
            where = path
        self.fields(x, t, level, path, where)

    def string(self, s):
        if isinstance(s, str):
            self.chars += len(s)
            self.utf8 += utf8_size(s)

    def text(self, rt, level: int, where: str):
        if isinstance(rt, str):
            self.string(rt)
        elif isinstance(rt, list):
            for x in rt:
                self.text(x, level, where)
        elif isinstance(rt, dict):
            self._reach(level + 1, where)
            self.fields(rt, self._tagged("RichText", rt), level + 1, where, where)


def _text_limits(v: Validator, size: TextSize, exact: bool, what: str = "") -> None:
    """The text against MAX_CHARS and the silent cut at MAX_TEXT_BYTES. `exact`: the count is Telegram's own
    (blocks); otherwise only the sure part is an error, the rest a warning."""
    sure = "" if exact else "не меньше "
    about = "" if exact else "около "
    if size.sure_chars > MAX_CHARS:
        v.errors.append(f"текст {sure}{size.sure_chars} символов{what}, лимит {MAX_CHARS}")
    elif size.sure_utf8 > MAX_TEXT_BYTES:
        v.errors.append(f"текст {sure}{size.sure_utf8} байт в UTF-8{what}: {CUT}")
    elif size.chars > MAX_CHARS:
        v.warnings.append(f"текст около {size.chars} символов, лимит {MAX_CHARS}: {EXACT_LATER}")
    elif size.utf8 > MAX_TEXT_BYTES:
        v.warnings.append(
            f"текст около {size.utf8} байт в UTF-8: если их больше ~{MAX_TEXT_BYTES}, Telegram без ошибки обрежет "
            f"конец поста; {EXACT_LATER}"
        )
    elif size.utf8 > NEAR_CUT_BYTES:
        v.warnings.append(
            f"текст {about}{size.utf8} байт в UTF-8, а после ~{MAX_TEXT_BYTES} Telegram без ошибки обрезает пост; "
            "с несколькими абзацами в замерах обрезка начиналась чуть раньше, а для постов из многих абзацев не "
            "замерена: отправьте предпросмотр себе и проверьте конец поста"
        )
    elif size.chars > FOLD_CHARS:
        v.warnings.append(FOLDED)


def _block_limits(v: Validator, blocks: list) -> dict:
    """Limits of a message in blocks: everything the documentation counts is in them, so the counts are exact and
    going over is an error."""
    n = _Count(v.spec)
    n.blocks_of(blocks, 1, "rich_message.blocks")
    _text_limits(v, TextSize(n.chars, n.utf8, n.chars, n.utf8), True, " с alternative_text эмодзи и исходником формул")
    if n.blocks > MAX_BLOCKS:
        v.errors.append(f"блоков вместе с пунктами списков и строками таблиц: {n.blocks}, лимит {MAX_BLOCKS}")
    if n.media > MAX_MEDIA:
        v.errors.append(f"медиа: {n.media}, лимит {MAX_MEDIA}")
    if n.depth > MAX_DEPTH:
        v.errors.append(
            f"{n.deepest}: {n.depth} уровней вложенности, лимит {MAX_DEPTH} (уровень — каждый блок на пути к тексту, "
            "вместе с блоком, где текст, и каждое форматирование вокруг текста)"
        )
    for where, columns in n.tables:
        if columns > MAX_COLUMNS:
            v.errors.append(f"{where}: колонок в таблице {columns}, лимит {MAX_COLUMNS}")
    return {"blocks": n.blocks, "depth": n.depth, "chars": n.chars, "bytes": n.utf8, "media": n.media}


# Levels of nesting in html, measured on the real Telegram (issue #170): the elements on the way to a piece of text,
# at most MAX_DEPTH. A list (ul or ol with its li) and a table (table, tr, td) are one level each; text at the top of
# the post or right in <details> gets a paragraph of its own (one level more), text in blockquote, aside, li, td and
# summary does not. Only these tags were measured (strong, em… are the same formatting under other names), so only
# the depth they make is an error; the other tags, counted by the same reading, give a warning.
HTML_LEVELS = frozenset(
    {"p", "blockquote", "aside", "details", "ul", "ol", "table"}
    | {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del"}
)
HTML_TEXT_HOLDERS = frozenset({"p", "blockquote", "aside", "li", "td", "th", "summary"})
HTML_MAYBE_LEVELS = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6", "pre", "footer", "figure", "tg-collage", "tg-slideshow", "tg-thinking"}
    | {"tg-button-row", "tg-math-block", "video", "audio", "tg-document", "tg-map"}
    | {"a", "code", "sub", "sup", "mark", "tg-spoiler", "tg-emoji", "tg-math", "tg-time", "tg-reference"}
)
HTML_MAYBE_TEXT_HOLDERS = frozenset(
    {"h1", "h2", "h3", "h4", "h5", "h6", "pre", "footer", "figcaption", "caption", "cite", "tg-thinking"}
    | {"tg-button", "tg-math-block"}
)
HTML_MEDIA = frozenset({"hr", "img", "video", "audio", "tg-document", "tg-map", "tg-math-block"})  # blocks w/o text
# for the place named in a message: formatting goes as one run, the parts of a list or a table are not named
HTML_INLINE = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "a", "code", "sub", "sup", "mark", "tg-spoiler"}
    | {"tg-emoji", "tg-math", "tg-time", "tg-reference", "span"}
)
HTML_PARTS = frozenset({"li", "tr", "td", "th", "thead", "tbody", "tfoot"})
HTML_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"})
# a start tag that ends an open <p>, as in HTML (sloppy <p>a<p>b are siblings, not a paragraph in a paragraph)
HTML_ENDS_P = frozenset(
    {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "footer", "blockquote", "aside", "details", "summary", "ul"}
    | {"ol", "li", "table", "tr", "td", "th", "figure", "figcaption", "hr", "tg-collage", "tg-slideshow"}
    | {"tg-thinking", "tg-button-row", "tg-math-block", "tg-map"}
)


@dataclass(slots=True)
class _Open:
    """An open element: the levels down to it and whether text right in it gets a paragraph of its own."""

    tag: str
    sure: int  # levels of measured tags only
    about: int  # levels of every tag that reads as one
    bare: bool
    p: int = -1  # stack index of the open <p>, <li>, cell and row that a new one of the same kind ends
    li: int = -1
    cell: int = -1
    row: int = -1
    link: tuple[str, list] | None = None  # (address, text) of an <a> whose address has a "


class _HtmlWalk(HTMLParser):
    """The element tree of rich HTML as far as nesting and links go, in one pass and linear in the source."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [_Open("", 0, 0, True)]
        self.open: Counter[str] = Counter()
        self.links: list[tuple[str, list]] = []
        self.quoted: list[tuple[str, str]] = []  # (address, text) of links with a " in the address
        self.sure = self.about = 0
        self.sure_path = self.about_path = ""

    def _close(self, i: int):
        while len(self.stack) > i:
            e = self.stack.pop()
            self.open[e.tag] -= 1
            if e.link is not None:
                self.links.pop()
                self.quoted.append((e.link[0], "".join(e.link[1]).strip()))

    def _path(self, text: str = "") -> str:
        """Where the text is, short: «<blockquote> ×15 › <ul> › форматирование ×2 › «текст»»."""
        parts: list[list] = []
        for e in self.stack[1:]:
            if e.tag in HTML_PARTS:
                continue
            name = "форматирование" if e.tag in HTML_INLINE else f"<{e.tag}>"
            if parts and parts[-1][0] == name:
                parts[-1][1] += 1
            else:
                parts.append([name, 1])
        shown = [t + (f" ×{n}" if n > 1 else "") for t, n in parts]
        text = " ".join(text.split())
        return " › ".join(shown + ([f"«{_short(text, 20)}»"] if text else []))

    def _reach(self, sure: int, about: int, text: str = ""):
        if sure > self.sure:
            self.sure = sure
            if sure > MAX_DEPTH and not self.sure_path:
                self.sure_path = self._path(text)
        if about > self.about:
            self.about = about
            if about > MAX_DEPTH and not self.about_path:
                self.about_path = self._path(text)

    def handle_starttag(self, tag, attrs):
        top = self.stack[-1]
        for opened, ends in ((top.p, tag in HTML_ENDS_P), (top.li, tag == "li"), (top.row, tag == "tr")):
            if ends and opened >= 0:
                self._close(opened)
                top = self.stack[-1]
        if tag in ("td", "th") and top.cell >= 0:
            self._close(top.cell)
            top = self.stack[-1]
        if tag in HTML_MEDIA:
            self._reach(0, top.about + 1)
        if tag in HTML_VOID:
            return
        i = len(self.stack)
        maybe = tag in HTML_MAYBE_LEVELS and not (tag == "code" and top.tag == "pre")
        e = _Open(
            tag,
            top.sure + (tag in HTML_LEVELS),
            top.about + (tag in HTML_LEVELS or maybe),
            tag == "details" or (top.bare and tag not in HTML_TEXT_HOLDERS and tag not in HTML_MAYBE_TEXT_HOLDERS),
            i if tag == "p" else top.p,
            i if tag == "li" else -1 if tag in ("ul", "ol") else top.li,
            i if tag in ("td", "th") else -1 if tag in ("tr", "table") else top.cell,
            i if tag == "tr" else -1 if tag == "table" else top.row,
        )
        href = next((v for k, v in attrs if k == "href"), None) if tag == "a" else None
        if href and '"' in href:
            e.link = (href, [])
            self.links.append(e.link)
        self.stack.append(e)
        self.open[tag] += 1

    def handle_endtag(self, tag):
        if self.open[tag] <= 0:
            return
        i = len(self.stack) - 1
        while self.stack[i].tag != tag:
            i -= 1
        self._close(i)

    def handle_data(self, data):
        if self.links:
            self.links[-1][1].append(data)
        if data.strip():
            top = self.stack[-1]
            self._reach(top.sure + top.bare, top.about + top.bare, data)

    def close(self):
        super().close()
        self._close(1)


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


def _html_shape(v: Validator, src: str) -> int:
    """Nesting and links of html: an error where the measured reading is surely over the limit, a warning where only
    tags without a measurement take it over. Returns the levels of the measured reading."""
    walk = _HtmlWalk()
    walk.feed(src)
    walk.close()
    for href, text in walk.quoted:
        v.errors.append(
            f'ссылка «{_short(text, 30)}»: в адресе {_short(href, 60)} есть кавычка " (в HTML — &quot;), а с ней '
            "Telegram молча выбрасывает ссылку и оставляет только текст; запишите кавычку в адресе как %22"
        )
    if walk.sure > MAX_DEPTH:
        v.errors.append(
            f"{walk.sure} уровней вложенности, лимит {MAX_DEPTH}: {walk.sure_path} (уровень — каждый элемент на пути "
            "к тексту, список и таблица — по одному; у текста вне блока и прямо в <details> ещё свой абзац)"
        )
    elif walk.about > MAX_DEPTH:
        v.warnings.append(
            f"около {walk.about} уровней вложенности, лимит {MAX_DEPTH}: {walk.about_path} — если теги, для которых "
            "замера нет, считаются уровнями так же, как <b> и <blockquote>"
        )
    return walk.sure


TAG_RE = re.compile(r"<[^>]+>")
MD_TARGET_RE = re.compile(r"\]\([^)]*\)")  # where a link or an image points: [text](url "title")
# surely not text in Markdown: footnote names, reference definitions, code fence lines, list numbers, checkboxes;
# and any line with a "|": Telegram cuts table rows to the width of the header, which only a parser can tell
MD_MARKUP_RE = re.compile(
    r"^.*\|.*$|\[\^[^\]]*\]|^[ \t]*\[[^\]]+\]:.*$|^[ \t]*(?:```|~~~).*$|^[ \t]*\d+[.)][ \t]|\[[ xX]\]", re.M
)
HTTP_MEDIA_RE = re.compile(
    r"<(?:img|video|audio|tg-document)\b[^>]*\bsrc\s*=\s*[\"']?https?://|!\[[^\]]*\]\(\s*<?https?://", re.I
)


def source_size(src: str, markdown: bool) -> TextSize:
    """Text of html or markdown as far as the source tells without Telegram's parser: about how many characters and
    bytes (without tags and link addresses), and at least how many (also without spaces and, in Markdown, markup and
    table lines). tests/test_rich_limits.py keeps the second under Telegram's own count for every captured parse."""
    text = TAG_RE.sub("", src)
    if markdown:
        text = MD_TARGET_RE.sub("]", text)
    text = html.unescape(text)
    if markdown:
        sure = [ch for ch in MD_MARKUP_RE.sub("", text) if not ch.isspace() and ch not in string.punctuation]
    else:
        sure = [ch for ch in text if not ch.isspace()]
    return TextSize(len(text), utf8_size(text), len(sure), utf8_size("".join(sure)))


def _source_limits(v: Validator, rm: dict) -> dict:
    """Limits of a message in html or markdown: Telegram makes the blocks itself, so blocks and table columns are not
    counted here, characters only as far as the source tells (an error only when surely over), nesting in html by the
    measured reading and in markdown not at all (no measurement)."""
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
    is_html = bool(rm.get("html"))
    size = source_size(src, markdown=not is_html)
    _text_limits(v, size, False)
    count = max(len(media), len(refs) + len(HTTP_MEDIA_RE.findall(src)))
    if count > MAX_MEDIA:
        v.warnings.append(f"медиа около {count}, лимит {MAX_MEDIA}")
    stats = {"chars": size.chars, "bytes": size.utf8, "media": count}
    if is_html:
        stats["depth"] = _html_shape(v, src)
    return stats


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
