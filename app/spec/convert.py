"""Blocks Telegram sends back (RichBlock*) turned into blocks it accepts (InputRichBlock*).

sendRichMessage answers with the post already parsed into blocks. Fed back to the editor they let the
author switch an HTML or Markdown post to the visual mode exactly as Telegram understood it. The two
type families differ only where the spec says so: output-only fields (a list label) are dropped and
sent media (PhotoSize array, Video…) becomes InputMedia with the file_id, so nothing is uploaded again.
Everything is read from the spec; a block type the spec does not know is kept as is.
"""

from .source import Spec


def to_input_blocks(spec: Spec, blocks: list) -> list:
    by_value = {}
    for name in spec.types.get("InputRichBlock", {}).get("subtypes", []):
        const = spec.type_const(name)
        if const:
            by_value[const[1]] = name
    return [_block(spec, by_value, b) for b in blocks or []]


def _block(spec: Spec, by_value: dict, b):
    name = by_value.get(b.get("type")) if isinstance(b, dict) else None
    if not name:
        return b
    return _object(spec, by_value, b, name)


def _object(spec: Spec, by_value: dict, value: dict, type_name: str) -> dict:
    fields = {f["name"]: f["types"] for f in spec.types.get(type_name, {}).get("fields", [])}
    out = {}
    for key, v in value.items():
        if key in fields:
            out[key] = _value(spec, by_value, v, fields[key], value)
    return out


def _value(spec: Spec, by_value: dict, v, types: list, parent: dict):
    t = types[0] if len(types) == 1 else ""
    if t == "Array of InputRichBlock" and isinstance(v, list):
        return [_block(spec, by_value, x) for x in v]
    if t.startswith("Array of InputRich") and isinstance(v, list):
        item = t.removeprefix("Array of ")
        return [_object(spec, by_value, x, item) if isinstance(x, dict) else x for x in v]
    if t.startswith("InputMedia"):
        return _media(spec, t, v, parent)
    return v


def _media(spec: Spec, media_type: str, sent, parent: dict) -> dict:
    """A sent Photo/Video/… as InputMedia that reuses Telegram's file_id."""
    if isinstance(sent, list):  # photo: sizes, the largest is the original
        sizes = [s for s in sent if isinstance(s, dict)]
        sent = max(sizes, key=lambda s: (s.get("width", 0) * s.get("height", 0), s.get("file_size", 0)), default={})
    const = spec.type_const(media_type)
    file_id = sent.get("file_id", "") if isinstance(sent, dict) else ""
    out = {"type": const[1] if const else parent.get("type"), "media": file_id}
    fields = {f["name"] for f in spec.types.get(media_type, {}).get("fields", [])}
    if parent.get("has_spoiler") and "has_spoiler" in fields:
        out["has_spoiler"] = True
    return out
