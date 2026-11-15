"""Text with MessageEntity ranges (an ordinary text post or a caption) as input blocks with RichText.

Offsets and lengths count UTF-16 code units, as the Bot API says: an emoji outside the BMP takes two, so they are
not Python string indices. Entity types are matched to the spec by name: a type a RichText* type also has (bold,
code, custom_emoji, text_mention, date_time…) wraps its text, its other fields are taken from the entity by name;
a type an InputRichBlock* type has (pre, blockquote, expandable_blockquote) becomes a block of its own. Entities
Telegram finds in text by itself stay plain text: it finds them again on send. Any other type, or one the entity
lacks a required field for, keeps its text without formatting and adds a warning.
"""

import re
from itertools import pairwise
from typing import NamedTuple

from .source import Spec

# what Telegram detects in text on its own (InputRichMessage.skip_entity_detection); RichText* types of the same
# names need fields an entity does not have (username, hashtag…)
DETECTED = {"mention", "hashtag", "cashtag", "bot_command", "url", "email", "phone_number"}
RENAMED = {"text_link": "url"}  # MessageEntity type -> RichText type, where the names differ
CONTENT = ("text", "blocks", "alternative_text")  # where the covered text goes, in the order tried
_BLANK_LINE = re.compile(r"\n\s*\n")


class _Range(NamedTuple):
    start: int  # Python indices into the text
    end: int
    rank: tuple  # outer before inner: longer first, then in the order Telegram listed them
    node: dict  # the RichText or block without its content
    content: str  # the field the covered text goes to


def text_blocks(spec: Spec, text: str, entities: list, warnings: list[str]) -> list:
    """Paragraphs (split at blank lines) and the blocks entities stand for."""
    inline, blocks = _consts(spec, "RichText"), _consts(spec, "InputRichBlock")
    index = _utf16_index(text)
    spans: list[_Range] = []
    quotes: list[_Range] = []
    for i, e in enumerate(entities or []):
        if not isinstance(e, dict):
            continue
        kind, offset, length = str(e.get("type")), e.get("offset"), e.get("length")
        start = end = None
        if isinstance(offset, int) and isinstance(length, int):
            start, end = index.get(offset), index.get(offset + length)
        if start is None or end is None or start >= end:
            _warn(warnings, f"форматирование «{kind}» пропущено: его границы не совпадают с текстом")
            continue
        if kind in DETECTED:
            continue
        rich = RENAMED.get(kind, kind)
        name = inline.get(rich) or blocks.get(kind)
        if not name:
            _warn(warnings, f"форматирование «{kind}» перенесено обычным текстом: его нет в {spec.version}")
            continue
        if made := _node(spec, name, e, warnings):
            r = _Range(start, end, (start - end, i), *made)
            (spans if rich in inline else quotes).append(r)
    out: list = []
    pos = 0
    for q in sorted(quotes, key=lambda q: (q.start, q.rank)):
        if q.start < pos:  # blocks do not nest in a text message; the inner one keeps its text
            _warn(warnings, f"«{q.node['type']}» внутри другого блока перенесён обычным текстом")
            continue
        out += _paragraphs(text, pos, q.start, spans)
        if q.content == "blocks":  # a quote holds paragraphs
            out.append({**q.node, "blocks": _paragraphs(text, q.start, q.end, spans)})
        else:
            out.append({**q.node, q.content: _rich(text, q.start, q.end, spans)})
        pos = q.end
    return out + _paragraphs(text, pos, len(text), spans)


def _consts(spec: Spec, union: str) -> dict:
    """type value -> spec type name, for the subtypes of a union (RichText, InputRichBlock)."""
    out = {}
    for name in spec.types.get(union, {}).get("subtypes", []):
        if const := spec.type_const(name):
            out[const[1]] = name
    return out


def _utf16_index(text: str) -> dict:
    """UTF-16 offset -> Python index, for every offset that falls between characters."""
    index, unit = {}, 0
    for i, ch in enumerate(text):
        index[unit] = i
        unit += 2 if ord(ch) > 0xFFFF else 1
    index[unit] = len(text)
    return index


def _node(spec: Spec, name: str, entity: dict, warnings: list[str]) -> tuple[dict, str] | None:
    """The spec type with its own fields taken from the entity by name, and where the text goes;
    None (text without formatting) when the entity lacks a required field."""
    const = spec.type_const(name)
    node = {"type": const[1]} if const else {}
    fields = spec.types[name].get("fields", [])
    content = next((c for c in CONTENT if any(f["name"] == c for f in fields)), None)
    missing = None
    for f in fields:
        key = f["name"]
        if key in node or key == content:
            continue
        if key in entity:
            node[key] = entity[key]
        elif f["required"]:
            missing = key
            break
    if content is None or missing:
        why = f"у entity нет поля {missing}" if missing else f"в {name} некуда положить текст"
        _warn(warnings, f"форматирование «{entity.get('type')}» перенесено обычным текстом: {why}")
        return None
    return node, content


def _paragraphs(text: str, start: int, end: int, spans: list) -> list:
    out = []
    pos = start
    for m in [*_BLANK_LINE.finditer(text, start, end), None]:
        a, b = pos, m.start() if m else end
        while a < b and text[a] == "\n":
            a += 1
        while b > a and text[b - 1] == "\n":
            b -= 1
        if text[a:b].strip():
            out.append({"type": "paragraph", "text": _rich(text, a, b, spans)})
        if m:
            pos = m.end()
    return out


def _rich(text: str, start: int, end: int, spans: list):
    """RichText of text[start:end]: entities crossing the edges are cut at them, crossing each other are split."""
    inside = [r._replace(start=max(r.start, start), end=min(r.end, end)) for r in spans]
    inside = [r for r in inside if r.start < r.end]
    cuts = sorted({start, end, *(r.start for r in inside), *(r.end for r in inside)})
    pieces = []
    for a, b in pairwise(cuts):
        over = sorted((r for r in inside if r.start <= a and b <= r.end), key=lambda r: r.rank)
        pieces.append((text[a:b], over))
    return _simple(_tree(pieces))


def _tree(pieces: list) -> list:
    """Neighbouring pieces under the same outermost entity go into one node of it."""
    out: list = []
    k = 0
    while k < len(pieces):
        chunk, over = pieces[k]
        if not over:
            out.append(chunk)
            k += 1
            continue
        top, group = over[0], []
        while k < len(pieces) and pieces[k][1] and pieces[k][1][0] == top:
            group.append((pieces[k][0], pieces[k][1][1:]))
            k += 1
        if top.content == "text":
            out.append({**top.node, "text": _simple(_tree(group))})
        else:  # a custom emoji: its text is the emoji itself
            out.append({**top.node, top.content: "".join(c for c, _ in group)})
    return out


def _simple(parts: list):
    """Neighbouring strings joined; one part is the RichText itself, none is an empty string."""
    out: list = []
    for p in parts:
        if isinstance(p, str) and out and isinstance(out[-1], str):
            out[-1] += p
        elif p != "":
            out.append(p)
    if not out:
        return ""
    return out[0] if len(out) == 1 else out


def _warn(warnings: list[str], text: str) -> None:
    if text not in warnings:
        warnings.append(text)
