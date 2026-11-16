"""Post templates: a post with {{key}} fields (a rubric: a digest, an announcement) that fills into a new draft.

A field is {key, label, type: text | multiline | url, required, default}. {{key}} stands only in string values of
the post, never in its JSON keys. A value is always text, never markup: escaped (html.escape) in HTML, a plain JSON
string anywhere else (Telegram does not parse the text of a block). In HTML a {{key}} stands in text or in a quoted
attribute value. A link (an HTML link attribute such as href or src, a JSON string under the key url) takes only a url
field, as its whole value, and never a file upload:name (a file goes only in a media field). The callback_data of a
button with an answer in callbacks takes no field: a key of callbacks is never filled. Markdown is not filled
in this version: how to escape a value for Telegram's Markdown was never checked against Telegram, so such a template
is refused with a hint to use the visual mode or HTML.
Filling changes nothing in the post but the {{key}} marks (AGENTS.md rule 1), and makes a new draft without channels.
The size of the filled post is counted before it is built: one field repeated many times must not build gigabytes.
"""

import bisect
import html
import json
import re
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

import store
from errors import Conflict, Invalid, NotFound, StudioError

VAR = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]{0,31})\}\}")
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
TYPES = ("text", "multiline", "url")
MAX_FIELDS = 50
MAX_BYTES = 1 << 20  # a template, and a post filled from it, as JSON
MAX_DEPTH = 200  # as the validator's MAX_JSON_DEPTH: deeper is a broken post, not a template
# what a url field may hold: the links the preview opens (render.js safeUrl) and an uploaded file
URL_SCHEMES = ("http", "https", "tg", "mailto", "tel")
# HTML attributes that hold a link (render.js URL_ATTRS and the other link attributes of HTML and SVG): only a
# whole url field goes there, checked like safeUrl
URL_ATTRS = {
    "href",
    "src",
    "url",
    "poster",
    "srcset",
    "xlink:href",
    "cite",
    "data",
    "action",
    "formaction",
    "background",
    "ping",
}
# the JSON key of a link (RichTextUrl, InlineKeyboardButton, WebAppInfo, LoginUrl…), as validator.py reads it: only a
# whole url field goes there, as in an HTML link attribute. Told by the key's name, not by a list of blocks
URL_KEY = "url"
# the post's answers to its buttons (services/callbacks.py): its keys are callback_data, not names of fields
CALLBACKS = "callbacks"
# what a button of the post gives the bot when pressed: its answer is the key of callbacks with this value
CALLBACK_DATA = "callback_data"
MARKDOWN = (
    "{{…}} в Markdown пока не подставляется: переведите пост в визуальный режим или в HTML и сохраните шаблон снова"
)


def mark(key: str) -> str:
    return "{{" + key + "}}"


# ---------- the post as strings with their places ----------
Path = tuple[str | int, ...]


def _strings(value, path: Path = (), parent: dict | None = None) -> Iterator[tuple[Path, str, dict | None]]:
    """Every string value of the post with its path and the object it belongs to (for a string in a list, the
    object holding the list). A {{key}} in a JSON key is refused: a field fills values, not the shape."""
    if len(path) > MAX_DEPTH:
        raise Invalid(f"вложенность больше {MAX_DEPTH} уровней")
    if isinstance(value, str):
        yield path, value, parent
    elif isinstance(value, dict):
        for k, v in value.items():
            if VAR.search(k):
                raise Invalid(f"{_where((*path, k))}: {{{{…}}}} в ключе JSON — переменная стоит только в значении")
            yield from _strings(v, (*path, k), value)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, (*path, i), parent)


def _map(value, fn: Callable[[Path, str], str], path: Path = ()):
    if isinstance(value, str):
        return fn(path, value)
    if isinstance(value, dict):
        return {k: _map(v, fn, (*path, k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_map(v, fn, (*path, i)) for i, v in enumerate(value)]
    return value


def _where(path: Path) -> str:
    return "".join(f"[{p}]" if isinstance(p, int) else (f".{p}" if i else p) for i, p in enumerate(path)) or "пост"


def _key(path: Path) -> str:
    """The JSON key a string stands under (for a string in a list, the key of the list), as telegram/uploads reads it.
    A string answer in callbacks stands under its button's callback_data, which names no field: "" then."""
    for i in range(len(path) - 1, -1, -1):
        if isinstance(p := path[i], str):
            return "" if i == 1 and path[0] == CALLBACKS else p
    return ""


def _source(path: Path) -> str:
    """How Telegram reads the string: the html or markdown of an InputRichMessage (the post, its first comment) is
    parsed, any other string is taken as it is."""
    key = _key(path)
    return key if key in ("html", "markdown") and path[-1] == key else "text"


def _formatted_by(parent: dict | None) -> str | None:
    """A parse_mode or entities next to the value (a media caption, a poll question): Telegram would format the
    value by them, so it could make markup or shift the entities."""
    for k, v in (parent or {}).items():
        if v and isinstance(k, str) and k.endswith(("parse_mode", "entities")):
            return k
    return None


# ---------- HTML: where a {{key}} stands ----------
# The scan follows the HTML5 tokenizer (https://html.spec.whatwg.org/#tokenization), the way the preview (a browser)
# reads the post, and refuses whatever HTML5 calls a parse error inside a tag or a comment: Telegram's own parser may
# recover from it otherwise, and a {{key}} after such a place could land among the attributes. Inside a quoted value
# and in text an escaped value has no ", ', < or >, so it cannot leave its place.
WS = " \t\n\r\f"  # HTML5 whitespace; \v, NBSP and other Unicode spaces are ordinary characters there
# elements whose content is not parsed as tags (RAWTEXT, RCDATA, PLAINTEXT)
RAW_TEXT = {"script", "style", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext", "textarea", "title"}


class _Unclear(Exception):
    """An HTML construct the template refuses: browsers and Telegram may read it differently."""


def _html_scan(src: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, int]]]:
    """The quoted attribute values of an HTML source as (start, end, attribute name), and every tag and comment as
    (start, end); the rest is text. Raises _Unclear on anything HTML5 reads with a parse error."""
    quoted: list[tuple[int, int, str]] = []
    marks: list[tuple[int, int]] = []
    n, i = len(src), 0
    while (lt := src.find("<", i)) >= 0:
        if src.startswith("<!--", lt):
            if src.startswith((">", "->"), lt + 4):
                raise _Unclear("комментарий <!--> или <!---> закрывается сразу")
            end, bang = src.find("-->", lt + 4), src.find("--!>", lt + 4)
            if bang >= 0 and (end < 0 or bang < end):
                raise _Unclear("комментарий закрыт --!>")
            if end < 0:
                raise _Unclear("комментарий не закрыт")
            i = end + 3
            marks.append((lt, i))
            continue
        start = lt + 2 if src.startswith("</", lt) else lt + 1
        if not (start < n and src[start].isascii() and src[start].isalpha()):
            raise _Unclear("знак < не начинает тег (в тексте пишите &lt;), или это <!…>, <?…> или </ без имени")
        j = start
        while j < n and src[j] not in WS and src[j] not in "/>":
            if src[j] in "\"'<=`":
                raise _Unclear(f"знак {src[j]} в имени тега")
            j += 1
        name = src[start:j].lower()
        j = _tag_rest(src, j, quoted)
        marks.append((lt, j))
        if start == lt + 1 and name in RAW_TEXT:
            raise _Unclear(f"<{name}>: его содержимое браузер не разбирает как теги")
        i = j
    return quoted, marks


def _tag_rest(src: str, j: int, quoted: list[tuple[int, int, str]]) -> int:
    """The attributes of a tag after its name, as HTML5 reads them; the index after its ">"."""
    n = len(src)
    while True:
        while j < n and src[j] in WS:
            j += 1
        if j >= n:
            raise _Unclear("тег не закрыт")
        c = src[j]
        if c == ">":
            return j + 1
        if c == "/":
            if src.startswith(">", j + 1):
                return j + 2
            raise _Unclear("/ внутри тега не перед >")
        if c == "=":
            raise _Unclear("= без имени атрибута")
        k = j
        while k < n and src[k] not in WS and src[k] not in "/>=":
            if src[k] in "\"'<":
                raise _Unclear(f"знак {src[k]} в имени атрибута")
            k += 1
        attr = src[j:k].lower()
        while k < n and src[k] in WS:
            k += 1
        if not src.startswith("=", k):
            j = k  # an attribute without a value
            continue
        k += 1
        while k < n and src[k] in WS:
            k += 1
        if k >= n or src[k] == ">":
            raise _Unclear(f"у атрибута {attr} после = нет значения")
        if src[k] in "\"'":
            close = src.find(src[k], k + 1)
            if close < 0:
                raise _Unclear(f"кавычка значения {attr} не закрыта")
            quoted.append((k + 1, close, attr))
            j = close + 1
            if j < n and src[j] not in WS and src[j] not in "/>":
                raise _Unclear(f"после значения {attr} нет пробела")
            continue
        while k < n and src[k] not in WS and src[k] != ">":  # an unquoted value
            if src[k] in "\"'<=`":
                raise _Unclear(f"знак {src[k]} в значении {attr} без кавычек")
            k += 1
        j = k


def _html_places(src: str) -> list[tuple[re.Match, str | None, str]]:
    """Every {{key}} of an HTML source with its place: None in text; inside a quoted attribute value, the attribute's
    name and the whole value; "" anywhere else in a tag or a comment (a tag or attribute name, an unquoted value)."""
    quoted, marks = _html_scan(src)
    starts, mark_starts = [q[0] for q in quoted], [t[0] for t in marks]
    places: list[tuple[re.Match, str | None, str]] = []
    for m in VAR.finditer(src):
        k = bisect.bisect_right(starts, m.start()) - 1
        t = bisect.bisect_right(mark_starts, m.start()) - 1
        if k >= 0 and m.end() <= quoted[k][1]:
            a, b, name = quoted[k]
            places.append((m, name, src[a:b]))
        elif t >= 0 and m.start() < marks[t][1]:
            places.append((m, "", ""))
        else:
            places.append((m, None, ""))
    return places


def _check_html(src: str, fields: dict[str, dict], where: str) -> None:
    try:
        places = _html_places(src)
    except _Unclear as e:
        raise Invalid(
            f"{where}: HTML с полями разбирается неоднозначно ({e}): поправьте разметку, иначе значение поля "
            "может оказаться внутри тега"
        ) from None
    for m, attr, value in places:
        if attr is None:
            continue
        if attr == "":
            raise Invalid(
                f"{where}: {m[0]} внутри тега HTML (имя тега или атрибута, значение без кавычек, комментарий): "
                'переменная стоит только в тексте или в значении атрибута в кавычках, например title="{{имя}}"'
            )
        if attr.startswith("on"):
            raise Invalid(f"{where}: {m[0]} в обработчике {attr}: туда переменные не подставляются")
        if attr in URL_ATTRS and (value != m[0] or fields[m[1]]["type"] != "url"):
            raise Invalid(
                f'{where}: {m[0]} в атрибуте {attr}: ссылку подставляет только поле типа url, целиком ({attr}="{m[0]}")'
            )
        if attr in URL_ATTRS and fields[m[1]]["default"].startswith("upload:"):
            raise Invalid(f"{where}: {m[0]}: {UPLOAD_IN_HTML}")


# upload:<name> becomes attach:// only in the post's media fields (telegram/), never inside HTML or in a link
UPLOAD_IN_HTML = (
    "файл upload: в атрибуте HTML не подставляется — в HTML-посте файл лежит в rich_message.media, а в тексте "
    "на него ссылается tg://photo?id=…"
)
UPLOAD_IN_JSON_LINK = (
    "файл upload: в ссылке JSON (url) не подставляется — файл ставится в медиа-поле поста (media у блока или в "
    "rich_message.media)"
)


def _link_fields(payload: dict) -> dict[str, str]:
    """The fields standing in a link of a saved template (an HTML link attribute such as href, a JSON url), each with
    why a file cannot go there."""
    out: dict[str, str] = {}
    for path, s, _ in _strings(payload):
        if not VAR.search(s):
            continue
        if _source(path) == "html":
            out.update((m[1], UPLOAD_IN_HTML) for m, attr, _ in _html_places(s) if attr in URL_ATTRS)
        elif _key(path) == URL_KEY:
            out.update((k, UPLOAD_IN_JSON_LINK) for k in VAR.findall(s))
    return out


# ---------- fields and values ----------
def _url_problem(v: str) -> str | None:
    if re.search(r"[\s\"'<>\\\x00-\x1f\x7f]", v):
        return "в ссылке пробел, кавычка или угловая скобка"
    if v.startswith("upload:"):
        return None if len(v) > len("upload:") else "после upload: нужно имя файла"
    if v.startswith("#") or (v.startswith("/") and not v.startswith("//")):  # an anchor, a path (render.js safeUrl)
        return None
    try:
        parts = urlsplit(v)
        host = parts.hostname
    except ValueError:
        return "ссылка не разбирается"
    scheme = parts.scheme.lower()
    if scheme not in URL_SCHEMES:
        return "нужна ссылка http(s)://, tg://, mailto:, tel:, #якорь или /путь либо загруженный файл upload:имя"
    if scheme in ("http", "https") and not host:
        return "в ссылке нет адреса сайта"
    return None


def _value(field: dict, value) -> str:
    """The value as it goes into the post; a link is checked (and trimmed)."""
    if not isinstance(value, str):
        raise Invalid(f"«{field['label']}»: значение должно быть строкой")
    if field["type"] == "url" and value.strip():
        value = value.strip()
        if bad := _url_problem(value):
            raise Invalid(f"«{field['label']}»: {bad}")
    return value


def _fields(raw: list[dict]) -> list[dict]:
    if len(raw) > MAX_FIELDS:
        raise Invalid(f"полей больше {MAX_FIELDS}")
    out: list[dict] = []
    for f in raw:
        key = f.get("key")
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise Invalid(f"имя поля {key!r}: латинская буква или _, дальше латинские буквы, цифры и _, до 32 знаков")
        if any(x["key"] == key for x in out):
            raise Invalid(f"поле {key} описано дважды")
        kind = f.get("type") or "text"
        if kind not in TYPES:
            raise Invalid(f"поле {key}: тип {kind!r}, а нужен один из {', '.join(TYPES)}")
        label = str(f.get("label") or "").strip() or key
        if len(label) > 100:
            raise Invalid(f"поле {key}: подпись длиннее 100 знаков")
        item = {"key": key, "label": label, "type": kind, "required": bool(f.get("required", True)), "default": ""}
        item["default"] = _value(item, f.get("default") or "")
        out.append(item)
    return out


def _check(payload: dict, fields: list[dict]) -> None:
    """Every {{key}} stands where its value can only be text, and every field stands somewhere."""
    by_key = {f["key"]: f for f in fields}
    used: set[str] = set()
    answers = payload.get(CALLBACKS)
    answered = set(answers) if isinstance(answers, dict) else set()
    for path, s, parent in _strings(payload):
        keys = VAR.findall(s)
        if not keys:
            continue
        where = _where(path)
        if unknown := [k for k in keys if k not in by_key]:
            raise Invalid(f"{where}: {mark(unknown[0])} — такого поля нет: опишите его в fields")
        used.update(keys)
        source = _source(path)
        if source == "markdown":
            raise Invalid(f"{where}: {MARKDOWN}")
        if fmt := _formatted_by(parent):
            raise Invalid(
                f"{where}: рядом задан {fmt}, Telegram разметит значение по нему: уберите {fmt} или переменную"
            )
        if s.startswith("upload:"):
            raise Invalid(
                f"{where}: часть ссылки upload: не подставляется — файл подставляет поле целиком "
                f'("{mark("файл")}" со значением upload:имя)'
            )
        if _key(path) == URL_KEY:
            link = by_key[keys[0]]
            if not VAR.fullmatch(s) or link["type"] != "url":
                raise Invalid(
                    f"{where}: {mark(keys[0])} в ссылке: ссылку подставляет только поле типа url, целиком "
                    f'("{URL_KEY}": "{mark(keys[0])}")'
                )
            if link["default"].startswith("upload:"):
                raise Invalid(f"{where}: {mark(keys[0])}: {UPLOAD_IN_JSON_LINK}")
        # a button of the post (not of a rich reply: the studio does not answer those) with an answer in callbacks.
        # A key of callbacks is never a field, so another value would leave the button without its answer
        if _key(path) == CALLBACK_DATA and path[0] != CALLBACKS:
            data = VAR.sub(lambda m: by_key[m[1]]["default"], s)
            if data in answered:
                raise Invalid(
                    f"{where}: {mark(keys[0])} в callback_data: кнопка связана с ответом на нажатие в {CALLBACKS} "
                    f"(«{data}»), а ключ {CALLBACKS} полем не меняется — после заполнения кнопка останется без "
                    "ответа. Оставьте callback_data как есть"
                )
        if source == "html":
            _check_html(s, by_key, where)
    if unused := [k for k in by_key if k not in used]:
        raise Invalid(f"поле {unused[0]} нигде не стоит в посте: вставьте {mark(unused[0])} или уберите поле")


def _json_len(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _too_big(what: str) -> StudioError:
    return StudioError(f"{what} больше {MAX_BYTES >> 20} МБ", status=413)


# ---------- what the routes and tools call ----------
def list_templates() -> list[dict]:
    return store.templates.list_templates()


def get(tid: str) -> dict:
    t = store.templates.get_template(tid)
    if not t:
        raise NotFound(f"шаблона {tid} нет")
    return t


def _changed(tid: str) -> StudioError:
    current = store.templates.get_template(tid)
    if not current:
        return NotFound(f"шаблона {tid} нет")
    return Conflict(
        {
            "message": f"шаблон изменили после того, как его открыли (сейчас version {current['version']}): "
            "откройте его заново",
            "current": current,
        }
    )


def save(title: str, post: dict, fields: list[dict], tid: str | None = None, version: int | None = None) -> dict:
    """A new template, or (with tid) an existing one overwritten only if nobody saved it since `version`."""
    title = (title or "").strip()
    if not title:
        raise Invalid("у шаблона нет названия")
    if len(title) > 200:
        raise Invalid("название шаблона длиннее 200 знаков")
    if not isinstance(post.get("rich_message"), dict):
        raise Invalid("в шаблоне нет rich_message")
    if _json_len(post) + _json_len(fields) > MAX_BYTES:
        raise _too_big("шаблон")
    clean = _fields(fields)
    _check(post, clean)
    if tid is None:
        return store.templates.create_template(title, post, clean)
    if version is None:
        raise Invalid("передайте version прочитанного шаблона: так чужая правка не пропадёт")
    t = store.templates.update_template(tid, title, post, clean, version)
    if t is None:
        raise _changed(tid)
    return t


def delete(tid: str, version: int) -> None:
    if not store.templates.delete_template(tid, version):
        raise _changed(tid)


def fill(tid: str, version: int, values: dict, title: str = "") -> dict:
    """A new draft (without channels) from the template with the values put in; a field left out takes its default."""
    t = get(tid)
    if t["version"] != version:
        raise _changed(tid)
    fields = {f["key"]: f for f in t["fields"]}
    if extra := sorted(set(values) - set(fields)):
        raise Invalid(f"в шаблоне нет полей {', '.join(extra)}")
    filled: dict[str, str] = {}
    in_links = _link_fields(t["payload"])
    for key, f in fields.items():
        v = values.get(key, f["default"])
        if f["required"] and not (isinstance(v, str) and v.strip()):
            raise Invalid(f"заполните «{f['label']}»")
        filled[key] = _value(f, v)
        if key in in_links and filled[key].startswith("upload:"):
            raise Invalid(f"«{f['label']}»: {in_links[key]}")
    as_source = {"text": filled, "html": {k: html.escape(v, quote=True) for k, v in filled.items()}}
    # the size of the filled post from the template and the size of each value: nothing is built yet
    grows = {src: {k: _json_len(v) - 2 for k, v in vals.items()} for src, vals in as_source.items()}
    size = _json_len(t["payload"])
    for path, s, _ in _strings(t["payload"]):
        extra_len = grows["html" if _source(path) == "html" else "text"]
        size += sum(extra_len[m[1]] - len(m[0]) for m in VAR.finditer(s))
    if size > MAX_BYTES:
        raise _too_big("заполненный пост")

    def put(path: Path, s: str) -> str:
        if not VAR.search(s):
            return s
        html_source = _source(path) == "html"
        vals = as_source["html" if html_source else "text"]
        out = VAR.sub(lambda m: vals[m[1]], s)
        if not html_source and not VAR.fullmatch(s) and out.startswith("upload:"):
            raise Invalid(f"{_where(path)}: ссылка upload: собралась из частей — файл подставляет одно поле целиком")
        return out

    post = _map(t["payload"], put)
    return store.save_draft(None, (title or "").strip() or t["title"], post)  # type: ignore[return-value]
