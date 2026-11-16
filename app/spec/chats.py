"""Where a part of a post works: the chats Telegram refuses it in, read from the descriptions of the spec.

The Bot API documentation says it in a few plain phrases on a field or a type: «Available only in private chats…»
(a web_app button), «Not supported in channels…» (switch_inline_query_current_chat, a reply keyboard, ForceReply),
«…; for private chats only» (message_effect_id), «may be used only in sendRichMessageDraft» (the thinking block).
The studio reads these phrases instead of keeping a list of fields (AGENTS.md, rule 2): a new field with such a phrase
is warned about the day the spec has it, and a phrase Telegram rewords or drops stops the warning instead of leaving a
stale one. What a measurement found and no description says is in validator.REFUSED_IN. Only warnings (rule 1): the
post still goes as it is, Telegram has the last word.
"""

import re
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple

from .source import Spec, reachable
from .validator import MAX_JSON_DEPTH, REFUSED_IN, _attribute, _json_depth

SEND = "sendRichMessage"  # every post, first comment and rich reply goes with it
# chat types are Chat.type: private, group, supergroup, channel; and a rich reply to a button press, an ephemeral message
EPHEMERAL = "ephemeral"
# the phrases of the descriptions, Bot API 10.4, and the chat types each one rules out
PHRASES = (
    (
        re.compile(r"\b(?:only in private chats|in private chats only|for private chats only)\b", re.I),
        frozenset({"group", "supergroup", "channel"}),
    ),
    (re.compile(r"\bnot supported in channels\b", re.I), frozenset({"channel"})),
    # the direct messages chat of a channel is a supergroup, and the studio does not know which supergroup is one
    (re.compile(r"\bfor direct messages chats only\b", re.I), frozenset({"private", "group", "channel"})),
    (re.compile(r"\bnot supported for\b[^.]*\bephemeral messages\b", re.I), frozenset({EPHEMERAL})),
)
# «The block may be used only in sendRichMessageDraft»: a post goes with sendRichMessage, so never in a post
ONLY_IN = re.compile(r"\bmay be used only in ([a-z]\w*)")
# «…, corresponding to the custom HTML tag <tg-thinking>.»: the tag of a block in rich HTML. Only a plain list of tags
# that ends the sentence: «<a> with the attribute name» is not every <a>
HTML_TAGS = re.compile(r"\bcorresponding to the (?:custom )?HTML tags? ((?:<[a-z][\w-]*/?>(?:,? or |, )?)+)\.")
# a button in rich HTML is <tg-button type="…">, the type naming the field of RichMessageButton that is its action
# (url without a type when it has a url, else callback_data), as render.js and html-export.js read it
HTML_BUTTON = ("tg-button", "RichMessageButton")
START_TAG = re.compile(r"<([a-z][\w-]*)\b[^>]*>", re.I)
SENTENCE = re.compile(r"(?<=\.)\s+(?=[A-Z])")
# advice shown with a warning about a field of this type; written by hand
HINTS = {
    "WebAppInfo": (
        "вместо неё кнопка-ссылка (url) с прямой ссылкой на Mini App https://t.me/<бот>/<приложение>?startapp=…, "
        "она открывает приложение и из канала (core.telegram.org/bots/webapps#direct-link-mini-apps)"
    ),
}
# how a warning names the chat: «не работает в канале «Новости»»
PLACES = {"channel": "в канале", "supergroup": "в группе", "group": "в группе", "private": "в личке"}


class Said(NamedTuple):
    """Why a chat type refuses it: the sentence of the description, or Telegram's own answer and when it was
    measured (`measured`, empty for the spec)."""

    why: str
    measured: str = ""


@dataclass(frozen=True, slots=True)
class Rule:
    """Where Telegram refuses a parameter, a field or a type: in chats of the types in `refused` (each with its
    reason), or in any post (`only_in`: the only method it belongs to, `why`: the sentence that says so)."""

    refused: dict[str, Said] = field(default_factory=dict)
    only_in: str = ""
    why: str = ""
    hint: str = ""


def read(description: str) -> Rule | None:
    """The rule a description states, or None: a field without such a phrase works everywhere as far as the spec says."""
    refused: dict[str, Said] = {}
    only_in = why = ""
    for sentence in SENTENCE.split(description.strip()):
        for phrase, chats in PHRASES:
            if phrase.search(sentence):
                for c in sorted(chats):
                    refused.setdefault(c, Said(sentence))
        if (m := ONLY_IN.search(sentence)) and m[1] != SEND:
            only_in, why = m[1], sentence
    return Rule(refused, only_in, why) if refused or only_in else None


# the rules of the last spec read (a check runs on every edit in the editor): {"data": spec.data, "rules": …}
_cache: dict[str, Any] = {}


def rules(spec: Spec) -> dict[str, Rule]:
    """Every rule for a sendRichMessage request: "sendRichMessage.<parameter>", "<Type>" and "<Type>.<field>" of the
    types its parameters lead to; the measured ones (REFUSED_IN) for fields the spec still has."""
    if _cache.get("data") is spec.data:
        return _cache["rules"]
    out: dict[str, Rule] = {}
    params = (spec.methods.get(SEND) or {}).get("fields", [])

    def add(key: str, description: str, types: tuple[str, ...] = ()) -> None:
        if rule := read(description):
            out[key] = replace(rule, hint=_hint(types))

    for f in params:
        add(f"{SEND}.{f['name']}", f.get("description", ""), tuple(f["types"]))
    for name in sorted(reachable(spec.types, [t for f in params for t in f["types"]])):
        t = spec.types[name]
        add(name, " ".join(t.get("description", [])))
        for f in t.get("fields", []):
            add(f"{name}.{f['name']}", f.get("description", ""), tuple(f["types"]))
    for key, (chats, answer, when) in REFUSED_IN.items():
        owner, _, name = key.partition(".")
        known = next((f for f in spec.types.get(owner, {}).get("fields", []) if f["name"] == name), None)
        rule = out.get(key) or Rule()
        new = {c: Said(answer, when) for c in sorted(chats) if c not in rule.refused}  # the spec has the last word
        if known is not None and new:  # a field gone from the spec takes its measurement with it
            out[key] = replace(rule, refused={**rule.refused, **new}, hint=rule.hint or _hint(known["types"]))
    _cache.update(data=spec.data, rules=out)
    return out


def _hint(types) -> str:
    return next((HINTS[t] for t in types if t in HINTS), "")


def summary(spec: Spec) -> dict:
    """The rules as the editor reads them (GET /api/spec `chat_rules`)."""
    return {
        key: {
            "refused": {c: {"why": s.why, "measured": s.measured or None} for c, s in r.refused.items()},
            "only_in": r.only_in or None,
            "why": r.why or None,
            "hint": r.hint or None,
        }
        for key, r in rules(spec).items()
    }


def place(chat_type: str, is_forum: bool, title: str | None, chat_id) -> tuple[str, str]:
    """(chat type, where in words) of a chat the post goes to, for warnings()."""
    word = "в форуме" if chat_type == "supergroup" and is_forum else PLACES.get(chat_type, f"в чате типа {chat_type}")
    return chat_type, f"{word} «{title}»" if title else f"{word} {chat_id}"


class _Walk:
    """The objects and the fields of a request, walked by the spec types of its parameters: (path, key, value) for
    every object ("<Type>") and every field of it ("<Type>.<field>"), whatever their depth."""

    def __init__(self, spec: Spec):
        self.spec = spec
        self.hits: list[tuple[str, str, Any]] = []

    def value(self, v, types: list[str], path: str) -> None:
        if isinstance(v, list):
            items = self._items(types, ())
            for i, x in enumerate(v if items else []):
                self.value(x, items, f"{path}[{i}]")
        elif isinstance(v, dict) and (t := self._type_of(v, types, ())):
            self.hits.append((path, t, v))
            fields = {f["name"]: f for f in self.spec.types[t].get("fields", [])}
            for k, x in v.items():
                if (f := fields.get(k)) is not None:
                    self.hits.append((f"{path}.{k}", f"{t}.{k}", x))
                    self.value(x, f["types"], f"{path}.{k}")

    def _items(self, types: list[str], seen: tuple) -> list[str]:
        """What an array of these types holds; a union may be an array itself (RichText)."""
        out = []
        for t in types:
            if t.startswith("Array of "):
                out.append(t.removeprefix("Array of "))
            elif t not in seen and (subs := self.spec.types.get(t, {}).get("subtypes")):
                out += self._items(subs, (*seen, t))
        return out

    def _type_of(self, v: dict, types: list[str], seen: tuple) -> str | None:
        """The type of an object: the member of a union its constant field names, else the first whose required
        fields it has (reply_markup: an inline keyboard, a reply keyboard, ForceReply…)."""
        for t in types:
            st = self.spec.types.get(t)
            if st is None or t in seen:
                continue
            if st.get("subtypes"):
                if sub := self._type_of(v, st["subtypes"], (*seen, t)):
                    return sub
            elif c := self.spec.type_const(t):
                if v.get(c[0]) == c[1]:
                    return t
            elif all(f["name"] in v for f in st.get("fields", []) if f["required"]):
                return t
        return None


def _button(path: str, value) -> str:
    """A button of the keyboard under the post as the author sees it, the way keyboard_errors names it."""
    m = re.fullmatch(r"reply_markup\.inline_keyboard\[(\d+)\]\[(\d+)\]", path)
    if not m or not isinstance(value, dict):
        return path
    return f"кнопка «{value.get('text', '')}» (ряд {int(m[1]) + 1}, №{int(m[2]) + 1})"


def _message(spec: Spec, where: str, what: str, rule: Rule, here: list[tuple[str, str]]) -> str:
    """One warning: `here` are the (chat type, where in words) that refuse it; each reason once."""
    if rule.only_in:
        return (
            f"{where}: {what}Telegram принимает только в {rule.only_in}, а пост уходит через {SEND} — в "
            f"спецификации ({spec.version}): «{rule.why.rstrip('.')}»"
        )
    places = dict.fromkeys(words for _, words in here)
    said = "; ".join(
        f"так отвечает Telegram: «{s.why}» (замер: {s.measured}; в спецификации этого нет)"
        if s.measured
        else f"в спецификации ({spec.version}): «{s.why.rstrip('.')}»"
        for s in dict.fromkeys(rule.refused[t] for t, _ in here)
    )
    hint = f". {rule.hint[:1].upper()}{rule.hint[1:]}" if rule.hint else ""
    return f"{where}: {what}не работает {', '.join(places)} — {said}{hint}"


def warnings(
    spec: Spec, post: dict, places: list[tuple[str, str]], at: str = "", options: str = "options"
) -> list[str]:
    """What Telegram refuses in this request where it goes. `post` holds rich_message, reply_markup and the other
    parameters of sendRichMessage under `options` (at the top with options=""); `places` are (chat type, where in
    words), place() makes them; `at` goes in front of every path. A rule for any post (`only_in`) holds without places.
    Each thing is named once, however many chats refuse it."""
    if not isinstance(post, dict) or _json_depth(post) > MAX_JSON_DEPTH:
        return []
    rs = rules(spec)
    params = {f["name"]: f for f in (spec.methods.get(SEND) or {}).get("fields", [])}
    walk = _Walk(spec)
    opts = (post.get(options) if options else post) or {}
    parts = {**(opts if isinstance(opts, dict) else {}), "rich_message": post.get("rich_message")}
    if post.get("reply_markup") is not None:
        parts["reply_markup"] = post["reply_markup"]
    for k, v in parts.items():
        if (f := params.get(k)) is not None:
            path = k if not options or k in ("rich_message", "reply_markup") else f"{options}.{k}"
            walk.hits.append((path, f"{SEND}.{k}", v))
            walk.value(v, f["types"], path)
    out: list[str] = []
    for path, key, _ in walk.hits:
        rule = rs.get(key)
        here = [(t, words) for t, words in places if rule and t in rule.refused]
        if rule and (rule.only_in or here):
            owner, _, name = key.partition(".")
            if name:  # the path ends at the field; a button of the keyboard is named by its text
                parent = path.rsplit(".", 1)[0]
                named = _button(parent, _value_at(walk.hits, parent))
                where, what = (f"{named}, {name}", "") if named != parent and not at else (at + path, "")
            else:
                where, what = at + path, f"{owner} "
            out.append(_message(spec, where, what, rule, here))
    src = (post.get("rich_message") or {}).get("html") if isinstance(post.get("rich_message"), dict) else None
    if isinstance(src, str):
        out += _html(spec, src, rs, places, at)
    return out


def _value_at(hits: list[tuple[str, str, Any]], path: str):
    return next((v for p, _, v in hits if p == path), None)


def _html(spec: Spec, src: str, rs: dict[str, Rule], places: list[tuple[str, str]], at: str) -> list[str]:
    """The same rules in rich HTML: a block by its tag (from «corresponding to the HTML tag <x>» of its type), a
    button by the field its type="…" names. Each tag or button action once."""
    by_tag: dict[str, str] = {}
    for name in rs:
        if "." not in name and name in spec.types:
            said = HTML_TAGS.search(" ".join(spec.types[name].get("description", [])))
            for tag in re.findall(r"<([a-z][\w-]*)/?>", said[1]) if said else []:
                by_tag.setdefault(tag, name)
    out: list[str] = []
    seen: set[str] = set()
    for m in START_TAG.finditer(src):
        tag = m[1].lower()
        key, shown = by_tag.get(tag, ""), f"<{tag}>"
        if tag == HTML_BUTTON[0]:
            action = _attribute(m[0], "type") or ("url" if _attribute(m[0], "url") is not None else "callback_data")
            key, shown = f"{HTML_BUTTON[1]}.{action}", f'<{tag} type="{action}">'
        rule = rs.get(key)
        here = [(t, words) for t, words in places if rule and t in rule.refused]
        if rule and (rule.only_in or here) and key not in seen:
            seen.add(key)
            what = f"{shown} ({key}) " if "." not in key else f"{shown} "
            out.append(_message(spec, f"{at}rich_message.html", what, rule, here))
    return out
