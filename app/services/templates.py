"""Post templates: a post with {{key}} fields (a rubric: a digest, an announcement) that fills into a new draft.

A field is {key, label, type: text | multiline | url, required, default}. {{key}} stands only in string values of
the post, never in its JSON keys. A value is always text, never markup: escaped (html.escape) in HTML, a plain JSON
string anywhere else (Telegram does not parse the text of a block). In HTML a {{key}} stands in text or in a quoted
attribute value; a link attribute (href, src) takes only a url field, as its whole value. Markdown is not filled in
this version: how to escape a value for Telegram's Markdown was never checked against Telegram, so such a template is
refused with a hint to use the visual mode or HTML.
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
# HTML attributes that hold a link (render.js URL_ATTRS): only a whole url field goes there
URL_ATTRS = {"href", "src", "url", "poster"}
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


def _source(path: Path) -> str:
    """How Telegram reads the string: the html or markdown of an InputRichMessage (the post, its first comment) is
    parsed, any other string is taken as it is."""
    return str(path[-1]) if path and path[-1] in ("html", "markdown") else "text"


def _formatted_by(parent: dict | None) -> str | None:
    """A parse_mode or entities next to the value (a media caption, a poll question): Telegram would format the
    value by them, so it could make markup or shift the entities."""
    for k, v in (parent or {}).items():
        if v and isinstance(k, str) and k.endswith(("parse_mode", "entities")):
            return k
    return None


# ---------- HTML: where a {{key}} stands ----------
def _html_places(src: str) -> list[tuple[re.Match, str | None, str]]:
    """Every {{key}} of an HTML source with its place: None in text; inside a quoted attribute value, the attribute's
    name and the whole value; "" anywhere else in a tag or a comment (a tag or attribute name, an unquoted value).
    Telegram wants every < that does not start a tag written as &lt;, so a raw < always opens a tag here."""
    tags: list[tuple[int, int]] = []  # [start, end) of every tag and comment, in order
    quoted: list[tuple[int, int, str]] = []  # [start, end) of every quoted attribute value and its attribute
    i, n = 0, len(src)
    while (lt := src.find("<", i)) >= 0:
        if src.startswith("<!--", lt):
            end = src.find("-->", lt + 4)
            i = n if end < 0 else end + 3
            tags.append((lt, i))
            continue
        j, i, eq = lt + 1, n, -1  # a tag without its ">" runs to the end
        while m := _IN_TAG.search(src, j):
            c, p = m[0], m.start()
            j = p + 1
            if c == ">":
                i = p + 1
                break
            if c == "=":
                eq = p
            elif eq >= 0 and not src[eq + 1 : p].strip():  # a quote right after "=": a quoted value
                close = src.find(c, p + 1)
                close = n if close < 0 else close
                quoted.append((p + 1, close, _attr_name(src, lt, eq)))
                j, eq = close + 1, -1
            # any other quote is a stray character of the tag, as in a browser
        tags.append((lt, i))
    places: list[tuple[re.Match, str | None, str]] = []
    starts, tag_starts = [q[0] for q in quoted], [t[0] for t in tags]
    for m in VAR.finditer(src):
        k = bisect.bisect_right(starts, m.start()) - 1
        t = bisect.bisect_right(tag_starts, m.start()) - 1
        if k >= 0 and m.end() <= quoted[k][1]:
            a, b, name = quoted[k]
            places.append((m, name, src[a:b]))
        elif t >= 0 and m.start() < tags[t][1]:
            places.append((m, "", ""))
        else:
            places.append((m, None, ""))
    return places


_IN_TAG = re.compile(r"[>\"'=]")


def _attr_name(src: str, lt: int, eq: int) -> str:
    """The attribute name before the "=" at `eq` of the tag that starts at `lt`, lowercased."""
    end = eq
    while end > lt + 1 and src[end - 1].isspace():
        end -= 1
    start = end
    while start > lt + 1 and src[start - 1] not in " \t\n\r\f\"'<>/=":
        start -= 1
    return src[start:end].lower()


def _check_html(src: str, fields: dict[str, dict], where: str) -> None:
    for m, attr, value in _html_places(src):
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


# ---------- fields and values ----------
def _url_problem(v: str) -> str | None:
    if re.search(r"[\s\"'<>\\\x00-\x1f\x7f]", v):
        return "в ссылке пробел, кавычка или угловая скобка"
    if v.startswith("upload:"):
        return None if len(v) > len("upload:") else "после upload: нужно имя файла"
    try:
        parts = urlsplit(v)
        host = parts.hostname
    except ValueError:
        return "ссылка не разбирается"
    scheme = parts.scheme.lower()
    if scheme not in URL_SCHEMES:
        return "нужна ссылка http(s)://, tg://, mailto: или tel: либо загруженный файл upload:имя"
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
    for key, f in fields.items():
        v = values.get(key, f["default"])
        if f["required"] and not (isinstance(v, str) and v.strip()):
            raise Invalid(f"заполните «{f['label']}»")
        filled[key] = _value(f, v)
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
