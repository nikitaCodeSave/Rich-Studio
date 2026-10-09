"""Small parsers for api.json: versions, shape check, enums and type constants in descriptions."""

import re

_ALWAYS_RE = re.compile(r'(?:always\s+[“"]([^”"]+)[”"]|must be ([a-z_]+)$)')
_QUOTED_RE = re.compile(r'[“"]([^”"]+)[”"]')


def version_tuple(v) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "")))


def check_spec_shape(d) -> str:
    """Empty string if this looks like a usable api.json, else the reason."""
    if not isinstance(d, dict):
        return "не объект"
    for k in ("version", "types", "methods"):
        if k not in d:
            return f"нет поля {k}"
    if "InputRichBlock" not in d["types"] or "sendRichMessage" not in d["methods"]:
        return "нет типов rich-сообщений"
    return ""


def enum_values(desc: str):
    """Pull 'must be one of "a", "b" or "c"' style enumerations out of a description."""
    m = re.search(r"(?:must be one of|one of|either)\s+(.*)", desc)
    if not m:
        return None
    tail = m.group(1)
    # stop at the end of the sentence
    end = re.search(r"\.\s", tail + " ")
    tail = tail[: end.start()] if end else tail
    vals = _QUOTED_RE.findall(tail)
    return vals or None


def type_const(types: dict, type_name: str):
    """For subtypes like InputRichBlockParagraph: (field, value) of the constant `type` field."""
    t = types.get(type_name) or {}
    for f in t.get("fields", []):
        if f["name"] in ("type", "source", "status"):
            m = _ALWAYS_RE.search(f.get("description", ""))
            if m:
                return f["name"], m.group(1) or m.group(2)
    return None
