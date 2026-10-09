"""What changed between two versions of the Bot API spec, for the editor's change log."""

TRACK_PREFIXES = (
    "InputRich",
    "RichText",
    "RichBlock",
    "RichMessage",
    "InlineKeyboard",
    "CopyTextButton",
    "DisabledButton",
    "InputMedia",
    "LinkPreview",
)
TRACK_METHODS = (
    "sendRichMessage",
    "editMessageText",
    "sendMessage",
    "editMessageReplyMarkup",
    "pinChatMessage",
    "deleteMessage",
    "getChatMember",
    "getMe",
)


def _sig(t: dict) -> dict:
    return {f["name"]: f["types"] for f in t.get("fields", [])}


def diff_specs(old: dict, new: dict) -> list:
    """Human-readable list of additions/changes in tracked types and methods."""
    items = []
    ot, nt = old.get("types", {}), new.get("types", {})
    for name in sorted(nt):
        tracked = name.startswith(TRACK_PREFIXES)
        if name not in ot:
            if tracked or "Rich" in name:
                items.append(
                    {"kind": "type_added", "name": name, "description": " ".join(nt[name].get("description", []))[:300]}
                )
            continue
        if not tracked:
            continue
        os_, ns = _sig(ot[name]), _sig(nt[name])
        for f in ns:
            if f not in os_:
                items.append({"kind": "field_added", "name": f"{name}.{f}", "types": ns[f]})
        for f in os_:
            if f not in ns:
                items.append({"kind": "field_removed", "name": f"{name}.{f}"})
        added_sub = set(nt[name].get("subtypes", [])) - set(ot[name].get("subtypes", []))
        for s in sorted(added_sub):
            items.append({"kind": "subtype_added", "name": f"{name} → {s}"})
    om, nm = old.get("methods", {}), new.get("methods", {})
    for name in sorted(nm):
        if name not in om:
            items.append(
                {"kind": "method_added", "name": name, "description": " ".join(nm[name].get("description", []))[:300]}
            )
        elif name in TRACK_METHODS:
            os_, ns = _sig(om[name]), _sig(nm[name])
            for f in ns:
                if f not in os_:
                    items.append({"kind": "param_added", "name": f"{name}({f})", "types": ns[f]})
    return items
