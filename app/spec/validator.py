"""Structural validation of a rich message against the spec, plus Telegram's size limits."""

import re
import string
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from .parse import enum_values
from .source import Spec

PRIMITIVES = {"String", "Integer", "Float", "Boolean", "True", "InputFile"}
# RichText types with `text` and one more required String field that is NOT the visible text again (a link target,
# an anchor or reference name); every other such type (email, phone, card, TON address, mention...) repeats its text.
# Mirrored in app/static/js/spec.js (NOT_TEXT_MIRROR); tests/test_spec_coverage.py keeps both in step.
NOT_TEXT_MIRROR = frozenset({"url", "anchor_link", "reference", "reference_link"})
# Fields the spec marks required that Telegram does not need, measured on the real Bot API 10.4 (2026-11-16, PR #173):
# a table cell without align and valign is taken and comes back as left and middle. Not an error, so a post written
# by hand or by an agent is not held back for them.
NOT_REQUIRED = {"RichBlockTableCell": frozenset({"align", "valign"})}
# The named HTML entities Telegram decodes, measured on the real Bot API 10.4 (2026-11-16, PR #173: all 2125 names of
# HTML5 with «;», sent in html and in markdown): exactly these 16, the same in both and to the same characters as in
# HTML5. The other 2109 (&laquo; &euro; &shy; &NBSP;…) and every name without «;» stay in the text as typed.
# Numeric ones, measured on Bot API 10.4 (2026-11-16, issue #174; tests/telegram_parse/numeric_entities.json), the
# same in html text and attributes and in markdown text (in a markdown code fence entities stay as typed): with a
# small x only (&#X41; stays), digits without a limit (&#0000000000000065; is «A»); 0…31 become a space, except &#10;
# in <pre>, which stays a line break (in an inline <code> it is a space); 128…159 stay those code points (no
# windows-1252 table as in HTML5: &#128; is U+0080, not «€»); a surrogate or a number past U+10FFFF (tg_char gives
# None) makes Telegram erase the whole text it stands in, up to the nearest tag, and refuse the post when that leaves
# a heading or a quote without text (RICH_MESSAGE_CONTENT_REQUIRED). Mirrored in render.js.
TG_ENTITIES = {
    "amp": "&",
    "apos": "'",
    "copy": "©",
    "gt": ">",
    "hellip": "…",
    "ldquo": "“",
    "lsquo": "‘",
    "lt": "<",
    "mdash": "—",
    "nbsp": "\u00a0",
    "ndash": "–",
    "quot": '"',
    "rdquo": "”",
    "reg": "®",
    "rsquo": "’",
    "trade": "™",
}
# What the real Telegram refused, measured on Bot API 10.4 (MEASURED; issue #166: sendRichMessage to a test channel
# and a test forum supergroup, each message deleted right away): the whole post, every time with an error, never
# dropped in silence. key as in spec/chats.py (sendRichMessage.<parameter>, <Type>, <Type>.<field>) → chat type →
# Telegram's answer. Most of it the descriptions say too, and spec/chats.py reads them: then a warning says the post
# is refused, not only what the spec says. Where no description says it, the measurement is the rule itself: the
# switch_inline_query buttons other than _current_chat under a channel post, every one of them in a «Кнопки» block,
# callback_game anywhere (no game message is sent here). Taken in the supergroup, so not listed: the switch_inline_query
# buttons and the reply keyboards. To be measured again with a new Bot API.
MEASURED = "Bot API 10.4, 2026-11-16"
_BOTH = ("channel", "supergroup")
_INLINE_IN_CHANNEL = {"channel": "can't use … button in a channel chat"}  # the first measurement kept no button name
_NOT_INLINE = {"channel": "inline keyboard expected"}
_IN_BLOCK = {"channel": "RICH_MESSAGE_BLOCK_INVALID"}
REFUSED_IN: dict[str, dict[str, str]] = {
    "sendRichMessage.message_effect_id": dict.fromkeys(_BOTH, "can't use message effects in the chat"),
    "InlineKeyboardButton.web_app": dict.fromkeys(_BOTH, "BUTTON_TYPE_INVALID"),
    "InlineKeyboardButton.callback_game": dict.fromkeys(_BOTH, "BUTTON_TYPE_INVALID"),
    "InlineKeyboardButton.switch_inline_query": _INLINE_IN_CHANNEL,
    "InlineKeyboardButton.switch_inline_query_current_chat": _INLINE_IN_CHANNEL,
    "InlineKeyboardButton.switch_inline_query_chosen_chat": {
        "channel": "can't use switch_inline_query button in a channel chat"
    },
    "RichMessageButton.web_app": dict.fromkeys(_BOTH, "BUTTON_TYPE_INVALID"),
    "RichMessageButton.switch_inline_query": _IN_BLOCK,
    "RichMessageButton.switch_inline_query_current_chat": _IN_BLOCK,
    "RichMessageButton.switch_inline_query_chosen_chat": _IN_BLOCK,
    "ReplyKeyboardMarkup": _NOT_INLINE,
    "ReplyKeyboardRemove": _NOT_INLINE,
    "ForceReply": _NOT_INLINE,
    "InputRichBlockThinking": dict.fromkeys(_BOTH, "RICH_MESSAGE_BLOCK_UNSUPPORTED"),
}


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
            if f["required"] and name not in value and name not in NOT_REQUIRED.get(t, ()):
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
            for url in [button.get("url"), *(x.get("url") for x in button.values() if isinstance(x, dict))]:
                if isinstance(url, str) and url.startswith("upload:"):  # url, web_app.url, login_url.url
                    errors.append(f"{where}: адрес {_short(url, 60)}: {UPLOAD_IN_LINK}")
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
# UTF-8 (a Cyrillic letter takes 2 bytes, an emoji 4); only what is left is then held to MAX_CHARS. html and markdown
# are cut as the source, markup and link addresses included, before they are parsed: what Telegram kept was always
# about 34 996-34 997 bytes of source (one paragraph of Cyrillic, 100-500 short paragraphs, a 16 KB link address, a
# cut in the middle of a tag that left a literal «<b» in the text). In blocks each string of text is cut by itself:
# 20 000 «ж» in one paragraph kept 34 996 bytes, two paragraphs of 16 000 «ж» (64 000 bytes) went out whole. The
# limit is a few bytes under the least of them: over it the end surely does not reach the reader.
MAX_BYTES = 34990
# String fields (not RichText) that the character limit counts: custom emoji alternative text and formula source
COUNTED_STRINGS = frozenset({"alternative_text", "expression"})
FOLD_CHARS = 8000  # not a limit: clients fold a longer post under «Показать ещё»
FOLDED = f"больше ~{FOLD_CHARS} символов: клиент свернёт пост под «Показать ещё»"
CUT = (
    f"Telegram без ошибки обрежет её после ~{MAX_BYTES} байт, и конец не дойдёт до читателя (буква кириллицы — 2 байта, "
    "эмодзи — 4); разбейте её на несколько абзацев"
)
CUT_SOURCE = (
    f"Telegram без ошибки обрежет исходник после ~{MAX_BYTES} байт (теги и адреса ссылок тоже считаются, буква "
    "кириллицы — 2 байта, эмодзи — 4), и конец поста не дойдёт до читателя; сократите пост или отправьте его блоками"
)

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


# Measured in blocks (issue #170 and the probes on its PR): these blocks and these RichText types are levels of
# nesting, so the depth they make is an error. Every other block or RichText object (a heading, a link, a spoiler…)
# is counted the same way only for a warning: no measurement had it.
MEASURED_BLOCKS = frozenset({"paragraph", "blockquote", "list", "details", "table"})
MEASURED_FORMATTING = frozenset({"bold", "italic", "underline", "strikethrough"})


class _Count:
    """What the rich message limits count in blocks, walked by the spec types of the fields, so a block newer than
    this code is counted too. A block is an element of an Array of InputRichBlock, a list item (an object holding
    blocks) or a table row. Levels of nesting as Telegram counts them in blocks (measured, issue #170): the blocks on
    the way to a piece of text, the one holding it included, plus every RichText object around it; a list item and a
    table row are blocks for the count, but not levels (list > item > paragraph is 2 levels, a table cell 1). `sure`
    counts only the measured blocks and formatting, `about` every one. `chars` is the whole text; the cut at MAX_BYTES
    is for each string of it."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.blocks = self.chars = self.longest = self.media = self.sure = self.about = 0
        self.sure_at = self.about_at = "rich_message.blocks"
        self.tables: list[tuple[str, int]] = []  # (path of the table, its columns)
        self.cut: list[tuple[str, int]] = []  # (path of the block, UTF-8 bytes) of strings over MAX_BYTES
        # (path of the block, "link" or "button", its text, address_notes) of addresses Telegram does not take as they are
        self.addresses: list[tuple[str, str, str, list]] = []
        self._tags: dict[str, dict] = {}
        self._holders: dict[str, bool] = {}

    def _reach(self, about: int, sure: int, where: str):
        if sure > self.sure:
            self.sure, self.sure_at = sure, where
        if about > self.about:
            self.about, self.about_at = about, where

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

    def blocks_of(self, value, about: int, sure: int, path: str):
        """The blocks of an Array of InputRichBlock; about and sure are the levels of what holds them."""
        for i, b in enumerate(value if isinstance(value, list) else []):
            if not isinstance(b, dict):
                continue
            where = f"{path}[{i}]"
            self.blocks += 1
            level, measured = about + 1, sure + (b.get("type") in MEASURED_BLOCKS)
            self._reach(level, measured, where)
            self.fields(b, self._tagged("InputRichBlock", b), level, measured, where, where)

    def _address(self, kind: str, url, obj: dict, where: str):
        if isinstance(url, str) and (notes := address_notes(kind, url)):
            self.addresses.append((where, kind, _plain(obj.get("text")), notes))

    def fields(self, obj: dict, t: str | None, about: int, sure: int, path: str, where: str):
        if "text" in obj:
            if obj.get("type") == "url":  # RichTextUrl
                self._address("link", obj.get("url"), obj, where)
            elif "type" not in obj:  # RichMessageButton: its url and the url of its action (web_app, login_url…)
                actions = [x.get("url") for x in obj.values() if isinstance(x, dict) and "type" not in x]
                for url in [obj.get("url"), *actions]:
                    self._address("button", url, obj, where)
        spec_t = self.spec.types.get(t) if t else None
        if spec_t is None:  # a type newer than the spec: at least its text and nested blocks
            self.text(obj.get("text"), about, sure, where)
            self.field(obj.get("blocks"), "blocks", [BLOCKS], about, sure, f"{path}.blocks", where)
            return
        for f in spec_t.get("fields", []):
            if f["name"] in obj:
                self.field(obj[f["name"]], f["name"], f["types"], about, sure, f"{path}.{f['name']}", where)

    def field(self, v, name: str, types: list, about: int, sure: int, path: str, where: str):
        one = types[0] if len(types) == 1 else ""
        if "RichText" in types:
            self.text(v, about, sure, where)
        elif BLOCKS in types:
            self.blocks_of(v, about, sure, path)
        elif any(t.startswith("InputMedia") for t in types):
            self.media += 1
        elif name in COUNTED_STRINGS:
            self.string(v, where)
        elif one.startswith(TABLE) and isinstance(v, list):
            rows = [r for r in v if isinstance(r, list)]
            for ri, row in enumerate(rows):
                self.blocks += 1
                for ci, c in enumerate(row):
                    if isinstance(c, dict):
                        self.obj(c, one[len(TABLE) :], about, sure, f"{path}[{ri}][{ci}]", where)
            self.tables.append((where, table_columns(rows)))
        elif one.startswith("Array of ") and isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, dict):
                    self.obj(x, one[len("Array of ") :], about, sure, f"{path}[{i}]", where)
        elif one and isinstance(v, dict):
            self.obj(v, one, about, sure, path, where)

    def obj(self, x: dict, t: str, about: int, sure: int, path: str, where: str):
        """An object inside a block: a list item holds blocks and is a block itself, a caption or a button is not."""
        if self._holds_blocks(t):
            self.blocks += 1
            where = path
        self.fields(x, t, about, sure, path, where)

    def string(self, s, where: str):
        if isinstance(s, str):
            self.chars += len(s)
            size = utf8_size(s)
            self.longest = max(self.longest, size)
            if size > MAX_BYTES:
                self.cut.append((where, size))

    def text(self, rt, about: int, sure: int, where: str):
        if isinstance(rt, str):
            self.string(rt, where)
        elif isinstance(rt, list):
            for x in rt:
                self.text(x, about, sure, where)
        elif isinstance(rt, dict):
            level, measured = about + 1, sure + (rt.get("type") in MEASURED_FORMATTING)
            self._reach(level, measured, where)
            self.fields(rt, self._tagged("RichText", rt), level, measured, where, where)


def _plain(rt) -> str:
    if isinstance(rt, str):
        return rt
    if isinstance(rt, list):
        return "".join(_plain(x) for x in rt)
    return _plain(rt.get("text")) if isinstance(rt, dict) else ""


# What Telegram does with a character in the address of a link or a button, entities decoded, measured on the real
# Bot API 10.4 (2026-11-16, issue #174: <a href> and <tg-button url> in html; in blocks the url of a url element and of
# a «Кнопки» button, with its web_app.url and login_url.url; each message deleted right away). A link (<a href>, a url
# element) it drops over any of LINK_DROPS and keeps its text. A button it refuses with the whole post
# (BUTTON_URL_INVALID) over BUTTON_REFUSES; a space or a tab in a button it writes as %20 and %09 itself. U+2028 it
# takes out of either address and keeps the link or the button (a warning). U+007F, U+0080, U+00AD, U+FEFF, ' \ { | }
# stay as they are. Not measured: the other Unicode spaces (UNMEASURED_SPACES) in either, and in a button the rest of
# LINK_DROPS: a warning that says so. Buttons under the post (reply_markup) were not measured at all. Mirrored in
# render.js (LINK_DROPS); js/html-export.js writes these characters %-encoded. To be measured again with a new Bot API.
LINK_DROPS = frozenset('"<>' + "".join(map(chr, range(0x21))) + "\x85\xa0\u2003\u3000\u200b")
BUTTON_REFUSES = frozenset('"<\xa0\u200b')
BUTTON_ENCODES = frozenset(" \t")
TAKEN_OUT = "\u2028"
UNMEASURED_SPACES = frozenset(c for c in map(chr, range(0x3001)) if c.isspace()) - LINK_DROPS - {TAKEN_OUT}
BUTTON_UNMEASURED = (LINK_DROPS | UNMEASURED_SPACES) - BUTTON_REFUSES - BUTTON_ENCODES
CHAR_NAMES = {
    '"': 'кавычка "',
    " ": "пробел",
    "\t": "табуляция",
    "\n": "перевод строки",
    "\xa0": "неразрывный пробел U+00A0",
    "\u200b": "пробел нулевой ширины U+200B",
    TAKEN_OUT: "разделитель строк U+2028",
}
# upload:<name> becomes a file only in a file field (telegram/uploads.py, FILE_KEYS); no link is one
UPLOAD_IN_LINK = (
    "upload: студия подставляет только в поле файла (media блока или поста), а в ссылке Telegram получит адрес как есть"
)
UPLOAD_IN_SRC = (
    "upload: студия подставляет только в поле файла, а в атрибуте HTML Telegram получит его как есть: файл в HTML "
    'ставится как src="tg://photo?id=<id>" с элементом <id> в rich_message.media'
)


def _char(c: str) -> str:
    if c in CHAR_NAMES:
        return CHAR_NAMES[c]
    return c if c.isprintable() and not c.isspace() else f"U+{ord(c):04X}"


def _encoded(chars: list[str]) -> str:
    return ", ".join(f"{_char(c)} — {quote(c, safe='')}" for c in chars)


def address_notes(kind: str, url: str) -> list[tuple[bool, str]]:
    """What Telegram does with the address of a link (kind "link") or a button ("button"), entities decoded: a list of
    (is it an error, what to say after «ссылка «текст»: »), empty when it takes the address as it is."""
    addr = _short(url, 60)
    if url.startswith("upload:"):
        return [(True, f"адрес {addr}: {UPLOAD_IN_LINK}")]
    link = kind == "link"
    chars = list(dict.fromkeys(url))
    notes = []
    if bad := [c for c in chars if c in (LINK_DROPS if link else BUTTON_REFUSES)]:
        verdict = (
            "молча выбрасывает ссылку и оставляет только текст" if link else "отклоняет весь пост (BUTTON_URL_INVALID)"
        )
        say = f"в адресе {addr} есть {', '.join(map(_char, bad))}, а с этим Telegram {verdict}"
        notes.append((True, f"{say}; в адресе их пишут так: {_encoded(bad)}"))
    if TAKEN_OUT in chars:
        stays = "ссылка" if link else "кнопка"
        notes.append((False, f"из адреса {addr} Telegram уберёт {CHAR_NAMES[TAKEN_OUT]}, {stays} останется"))
    if odd := [c for c in chars if c in (UNMEASURED_SPACES if link else BUTTON_UNMEASURED)]:
        known = (
            "ссылку с U+00A0, U+2003 или U+3000 он выбрасывает"
            if link
            else "кнопку с кавычкой, <, U+00A0 или U+200B он отклоняет вместе с постом"
        )
        say = f"в адресе {addr} есть {', '.join(map(_char, odd))}: что с этим сделает Telegram, не замерено ({known})"
        notes.append((False, f"{say}; надёжнее записать так: {_encoded(odd)}"))
    return notes


def _note_address(v: Validator, kind: str, text: str, notes: list, at: str = ""):
    head = f"{at}{'ссылка' if kind == 'link' else 'кнопка'} «{_short(' '.join(text.split()), 30)}»: "
    for is_error, say in notes:
        (v.errors if is_error else v.warnings).append(head + say)


def _block_limits(v: Validator, blocks: list) -> dict:
    """Limits of a message in blocks: everything the documentation counts is in them, so the counts are exact and
    going over is an error, except nesting made by blocks and RichText no measurement had (a warning).
    stats["max_string_bytes"] is the longest string: the cut is for each one by itself."""
    n = _Count(v.spec)
    n.blocks_of(blocks, 0, 0, "rich_message.blocks")
    if n.chars > MAX_CHARS:
        v.errors.append(f"текст {n.chars} символов с alternative_text эмодзи и исходником формул, лимит {MAX_CHARS}")
    elif n.cut:
        for where, size in n.cut:
            v.errors.append(f"{where}: строка текста {size} байт в UTF-8: {CUT}")
    elif n.chars > FOLD_CHARS:
        v.warnings.append(FOLDED)
    for where, kind, text, notes in n.addresses:
        _note_address(v, kind, text, notes, f"{where}: ")
    if n.blocks > MAX_BLOCKS:
        v.errors.append(f"блоков вместе с пунктами списков и строками таблиц: {n.blocks}, лимит {MAX_BLOCKS}")
    if n.media > MAX_MEDIA:
        v.errors.append(f"медиа: {n.media}, лимит {MAX_MEDIA}")
    if n.sure > MAX_DEPTH:
        v.errors.append(
            f"{n.sure_at}: {n.sure} уровней вложенности, лимит {MAX_DEPTH} (уровень — каждый блок на пути к тексту, "
            "вместе с блоком, где текст, и каждое форматирование вокруг текста)"
        )
    elif n.about > MAX_DEPTH:
        v.warnings.append(
            f"{n.about_at}: около {n.about} уровней вложенности, лимит {MAX_DEPTH} — если блоки и элементы текста, для "
            "которых замера нет (заголовок, ссылка, спойлер…), считаются уровнями так же, как абзац, цитата и <b>"
        )
    for where, columns in n.tables:
        if columns > MAX_COLUMNS:
            v.errors.append(f"{where}: колонок в таблице {columns}, лимит {MAX_COLUMNS}")
    return {"blocks": n.blocks, "depth": n.sure, "chars": n.chars, "max_string_bytes": n.longest, "media": n.media}


# Levels of nesting in html, measured on the real Telegram (issue #170): the elements on the way to a piece of text,
# at most MAX_DEPTH. A list (ul or ol with its li) and a table (table, tr, td) are one level each; text at the top of
# the post or right in <details> gets a paragraph of its own (one level more), text in blockquote, aside, li, td and
# summary does not. So a <p> is a level only where it stands for that paragraph: at the top and in <details>; in
# blockquote and li it is not (measured: <blockquote><p> and <ul><li><p> take 15 levels of formatting), in td, aside,
# summary… it was not measured. Telegram checks the HTML as it parses it: what it answers is normalised (15 levels of
# b/i/u/s under <blockquote><p> come back as 4). Only these tags were measured (strong, em… are the same formatting
# under other names), so only the depth they make is an error; the other tags, by the same reading, give a warning.
HTML_LEVELS = frozenset(
    {"blockquote", "aside", "details", "ul", "ol", "table"}
    | {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del"}
)
# <p> in these is not a level (measured); in other holders of text it may be (a warning)
HTML_P_FLAT = frozenset({"blockquote", "li"})
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


TG_ENTITY_RE = re.compile(rf"&(?:({'|'.join(TG_ENTITIES)})|#([0-9]+)|#x([0-9a-fA-F]+));")
# an attribute right after the quote of the one before it counts too (<a title="x"href="…">), as in html.parser
ATTR_RE = re.compile(r"""(?<=[\s"'/])([^\s"'=/>]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]*))?""")
# A tag, to tell the text of html from its tags: a piece of text between two tags is what Telegram erases for an
# entity it makes no character of (TG_ENTITIES). No «<» inside, so a «<» without its «>» costs a look up to the next
# «<», not to the end of the post. Mirrored in render.js (TAG).
HTML_TAG_RE = re.compile(r"""</?[A-Za-z][^\s/<>]*(?:[^<>"']|"[^"<]*"|'[^'<]*')*>""")
MD_FENCE_RE = re.compile(r"[ \t]{0,3}(`{3,}|~{3,})")
MD_CODE_RE = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)", re.S)


def _code(m: re.Match) -> int:
    """The number of a numeric entity of TG_ENTITY_RE. Past U+10FFFF every number is the same, so a long one is not
    read (Python reads at most 4300 digits into an int); zeros in front do not count."""
    digits = (m[2] or m[3]).lstrip("0") or "0"
    return int(digits, 10 if m[2] else 16) if len(digits) <= 8 else 0x110000


def tg_char(n: int) -> str | None:
    """What Telegram makes of the numeric entity n (TG_ENTITIES): None when it erases the text the entity stands in.
    In <pre> &#10; stays a line break; for what the studio counts that is one character all the same."""
    if n > 0x10FFFF or 0xD800 <= n <= 0xDFFF:
        return None
    return " " if n < 32 else chr(n)


def tg_unescape(s: str) -> str:
    """HTML entities as Telegram decodes them in html text and attributes and in markdown text (TG_ENTITIES). One
    that makes Telegram erase its text stays as typed (vanishing tells which)."""

    def one(m: re.Match) -> str:
        if m[1]:
            return TG_ENTITIES[m[1]]
        c = tg_char(_code(m))
        return m[0] if c is None else c

    return TG_ENTITY_RE.sub(one, s)


def vanishing(text: str) -> str | None:
    """The first numeric entity in a piece of text that makes Telegram erase it all, or None."""
    return next((m[0] for m in TG_ENTITY_RE.finditer(text) if not m[1] and tg_char(_code(m)) is None), None)


def html_pieces(src: str) -> Iterator[tuple[str, bool]]:
    """html as its pieces of text and its tags (HTML_TAG_RE), in order: (piece, is it a tag)."""
    at = 0
    for m in HTML_TAG_RE.finditer(src):
        yield src[at : m.start()], False
        yield m[0], True
        at = m.end()
    yield src[at:], False


def md_paragraphs(src: str) -> list[str]:
    """The paragraphs of markdown as written, without its code fences: where Telegram decodes entities."""
    paras: list[str] = []
    lines: list[str] = []
    fence = ""
    for line in src.split("\n"):
        f = MD_FENCE_RE.match(line)
        if fence:
            if f and f[1][0] == fence[0] and len(f[1]) >= len(fence) and not line[f.end() :].strip():
                fence = ""
            continue
        if f:
            fence = f[1]
        if f or not line.strip():
            if lines:
                paras.append("\n".join(lines))
                lines = []
        else:
            lines.append(line)
    if lines:
        paras.append("\n".join(lines))
    return paras


def erased_texts(src: str, markdown: bool) -> list[tuple[str, str]]:
    """(the text, the entity) that Telegram erases for a numeric entity it makes no character of: in html the piece of
    text between two tags (measured); in markdown the paragraph, out of code fences (measured: there entities stay as
    typed) and out of `code` (not measured; markdown-it in the preview leaves them as typed there). Where in a
    paragraph of markdown its text ends was not measured, so the whole paragraph is named and left out of the count."""
    if markdown:
        texts = [(p, vanishing(MD_CODE_RE.sub("", p))) for p in md_paragraphs(src)]
    else:
        texts = [(p, vanishing(p)) for p, tag in html_pieces(src) if not tag]
    return [(text, entity) for text, entity in texts if entity]


def _erased(text: str, entity: str, markdown: bool) -> str:
    m = TG_ENTITY_RE.fullmatch(entity)
    what = (
        "половина суррогатной пары (U+D800…U+DFFF), а не символ: символ пишут одной сущностью (&#x1F600;) или как есть"
        if m and _code(m) <= 0xDFFF
        else "больше U+10FFFF, такого символа нет"
    )
    where = "абзац" if markdown else "текст"
    until = "текст вокруг неё до ближайшей разметки" if markdown else "весь этот текст до ближайшего тега"
    return (
        f"{where} «{_short(' '.join(text.split()), 40)}»: в нём {_short(entity, 20)} — {what}; с такой сущностью "
        f"Telegram стирает {until}, а пост, где заголовок или цитата остались без текста, отклоняет "
        "(RICH_MESSAGE_CONTENT_REQUIRED)"
    )


def _attribute(start_tag: str, name: str) -> str | None:
    """An attribute of a start tag as written («<a href='…'>»), entities decoded as Telegram does."""
    inner = start_tag[1:].split(None, 1)
    for m in ATTR_RE.finditer(" " + inner[1] if len(inner) > 1 else ""):
        if m[1].lower() == name:
            value = m[2] or ""
            return tg_unescape(value[1:-1] if value[:1] in ("'", '"') else value)
    return None


@dataclass(slots=True)
class _Open:
    """An open element: the levels down to it and whether text right in it gets a paragraph of its own."""

    tag: str
    sure: int  # levels of measured tags only
    about: int  # levels of every tag that reads as one
    bare: bool
    holder: str  # the nearest element that holds text right in it ("" at the top of the post)
    p: int = -1  # stack index of the open <p>, <li>, cell and row that a new one of the same kind ends
    li: int = -1
    cell: int = -1
    row: int = -1
    # ("link" or "button", address_notes, its text) of an <a> or <tg-button> whose address Telegram does not take as is
    link: tuple[str, list, list] | None = None


class _HtmlWalk(HTMLParser):
    """The element tree of rich HTML as far as nesting and links go, in one pass and linear in the source."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = [_Open("", 0, 0, True, "")]
        self.open: Counter[str] = Counter()
        self.links: list[tuple[str, list, list]] = []
        self.addresses: list[tuple[str, list, str]] = []  # (kind, address_notes, text) of the links and buttons closed
        self.uploads: list[tuple[str, str]] = []  # (tag, src) of a src with upload:
        self.sure = self.about = 0
        self.sure_path = self.about_path = ""

    def _close(self, i: int):
        while len(self.stack) > i:
            e = self.stack.pop()
            self.open[e.tag] -= 1
            if e.link is not None:
                self.links.pop()
                self.addresses.append((e.link[0], e.link[1], "".join(e.link[2])))

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
        self.uploads += [(tag, v) for k, v in attrs if k == "src" and v and v.startswith("upload:")]
        if tag in HTML_VOID:
            return
        i = len(self.stack)
        holds = tag == "details" or tag in HTML_TEXT_HOLDERS or tag in HTML_MAYBE_TEXT_HOLDERS
        if tag == "p":  # the paragraph text gets anyway at the top and in <details>, or none in blockquote and li
            sure = about = top.bare
            about = about or top.holder not in HTML_P_FLAT
        else:
            sure = tag in HTML_LEVELS
            about = sure or (tag in HTML_MAYBE_LEVELS and not (tag == "code" and top.tag == "pre"))
        e = _Open(
            tag,
            top.sure + sure,
            top.about + about,
            tag == "details" or (top.bare and not holds),
            tag if holds else top.holder,
            i if tag == "p" else top.p,
            i if tag == "li" else -1 if tag in ("ul", "ol") else top.li,
            i if tag in ("td", "th") else -1 if tag in ("tr", "table") else top.cell,
            i if tag == "tr" else -1 if tag == "table" else top.row,
        )
        # an address Telegram does not take as it is (address_notes). html.parser decodes every HTML5 entity in
        # attributes, Telegram only a few and numbers its own way: the value is read from the tag as written
        if tag in ("a", "tg-button"):
            kind = "link" if tag == "a" else "button"
            address = _attribute(self.get_starttag_text() or "", "href" if tag == "a" else "url")
            if address is not None and (notes := address_notes(kind, address)):
                e.link = (kind, notes, [])
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
            self.links[-1][2].append(data)
        if data.strip():
            top = self.stack[-1]
            self._reach(top.sure + top.bare, top.about + top.bare, data)

    def close(self):
        super().close()
        self._close(1)


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


# html.parser reads every numeric entity into an int, and more than 4300 digits are a ValueError (a 500): the walk
# gets them with the zeros in front taken out, and one past U+10FFFF as the first number past it
LONG_NUMBER_RE = re.compile(r"&#(?:([0-9]{9,})|([xX])([0-9a-fA-F]{9,}))")


def _short_number(m: re.Match) -> str:
    digits = (m[1] or m[3]).lstrip("0") or "0"
    if len(digits) > 8:
        digits = "1114112" if m[1] else "110000"
    return "&#" + (m[2] or "") + digits


def _html_shape(v: Validator, src: str) -> int:
    """Nesting and links of html: an error where the measured reading is surely over the limit, a warning where only
    tags without a measurement take it over. Returns the levels of the measured reading."""
    walk = _HtmlWalk()
    walk.feed(LONG_NUMBER_RE.sub(_short_number, src))
    walk.close()
    for kind, notes, text in walk.addresses:
        _note_address(v, kind, text, notes)
    for tag, src in walk.uploads:
        v.errors.append(f"<{tag} src={_short(src, 60)}>: {UPLOAD_IN_SRC}")
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


def source_chars(src: str, markdown: bool) -> tuple[int, int]:
    """Text characters of html or markdown as far as the source tells without Telegram's parser: about how many
    (without tags and link addresses), and at least how many (also without spaces and, in Markdown, markup and table
    lines). tests/test_rich_limits.py keeps the second one under Telegram's own count for every captured parse. The
    text Telegram erases for a numeric entity (erased_texts) is not counted."""
    if markdown:
        for erased, _ in erased_texts(src, markdown):
            src = src.replace(erased, "", 1)
    else:
        src = "".join(p for p, tag in html_pieces(src) if tag or not vanishing(p))
    text = TAG_RE.sub("", src)
    # entities as Telegram decodes them; in markdown the link addresses (not decoded at all) are gone already
    text = tg_unescape(MD_TARGET_RE.sub("]", text) if markdown else text)
    if not markdown:
        return len(text), sum(not ch.isspace() for ch in text)
    sure = MD_MARKUP_RE.sub("", text)
    return len(text), sum(not ch.isspace() and ch not in string.punctuation for ch in sure)


def _source_limits(v: Validator, rm: dict) -> dict:
    """Limits of a message in html or markdown: Telegram makes the blocks itself, so blocks and table columns are not
    counted here, characters only as far as the source tells (an error only when surely over), nesting in html by the
    measured reading and in markdown not at all (no measurement). The cut at MAX_BYTES is of the source as it is, so
    that count is exact (stats["bytes"])."""
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
    for text, entity in erased_texts(src, markdown=not is_html):
        v.errors.append(_erased(text, entity, markdown=not is_html))
    chars, at_least = source_chars(src, markdown=not is_html)
    size = utf8_size(src)
    if at_least > MAX_CHARS:
        v.errors.append(f"текст не меньше {at_least} символов, лимит {MAX_CHARS}")
    elif size > MAX_BYTES:
        v.errors.append(f"{'HTML' if is_html else 'Markdown'} {size} байт в UTF-8 вместе с разметкой: {CUT_SOURCE}")
    elif chars > MAX_CHARS:
        v.warnings.append(
            f"текст около {chars} символов, лимит {MAX_CHARS}: точно его посчитает Telegram, когда разберёт разметку"
        )
    elif chars > FOLD_CHARS:
        v.warnings.append(FOLDED)
    count = max(len(media), len(refs) + len(HTTP_MEDIA_RE.findall(src)))
    if count > MAX_MEDIA:
        v.warnings.append(f"медиа около {count}, лимит {MAX_MEDIA}")
    stats = {"chars": chars, "bytes": size, "media": count}
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
