"""Rich Message Limits (https://core.telegram.org/bots/api#rich-message-limits): each limit right at the edge and one over.

How Telegram reads them was measured on the real Bot API 10.4 (issue #170); every measurement of the issue is a case
here with the answer Telegram gave (issue #170 and the follow-up probes on its PR). Over a limit is an error:
Telegram refuses the post, or (the length in bytes) cuts it without a word. A warning only where the studio cannot
count exactly: the characters of html/markdown before Telegram parses them, html tags no measurement had.

Numeric entities and the characters of a link or button address were measured the same way (issue #174)."""

import html as html_lib
import json
import re
import time
from pathlib import Path

import pytest

import spec
from spec.validator import (
    HTML_TAG_RE,
    LINK_DROPS,
    MAX_BYTES,
    TG_ENTITIES,
    TG_ENTITY_RE,
    keyboard_errors,
    source_chars,
    table_columns,
    tg_unescape,
)

# pure checks: no database (conftest.fresh_data); the few tests that take `client` still get theirs
pytestmark = pytest.mark.no_db

CELL = {"text": "x", "align": "left", "valign": "top"}
PARSE = Path(__file__).parent / "telegram_parse"
CAPTURED = json.loads((PARSE / "captured.json").read_text("utf-8"))
# the measurement of issue #174, render_unit.mjs checks the preview against the same table
NUMERIC = json.loads((PARSE / "numeric_entities.json").read_text("utf-8"))
RENDER_JS = (Path(__file__).parent.parent / "app" / "static" / "render.js").read_text("utf-8")


@pytest.fixture(scope="module")
def current():
    spec.current.load_initial()
    return spec.current


def check(current, blocks=None, **rm):
    return spec.validate_rich_message(current, {"blocks": blocks} if blocks is not None else rm)


def para(text="x"):
    return {"type": "paragraph", "text": text}


def photo():
    return {"type": "photo", "photo": {"type": "photo", "media": "upload:a.jpg"}}


def has(messages, fragment):
    return any(fragment in m for m in messages)


# ---------- characters ----------
def test_text_of_32768_characters_passes_and_one_more_is_an_error(current):
    """Measured: 32 768 latin letters pass, 32 769 are RICH_MESSAGE_TEXT_TOO_LONG."""
    r = check(current, [para("a" * 32768)])
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]
    r = check(current, [para("a" * 32769)])
    assert not r["ok"] and has(r["errors"], "лимит 32768"), r


def test_characters_count_emoji_alternative_text_and_formula_source(current):
    """ "including custom emoji alternative text and formula source": inline and block formulas alike."""
    extras = [
        {"type": "custom_emoji", "custom_emoji_id": "5368324170671202286", "alternative_text": "👍"},
        {"type": "mathematical_expression", "expression": "x^2"},
    ]
    formula = {"type": "mathematical_expression", "expression": "E=mc^2"}
    room = 32768 - 1 - 3 - 6
    r = check(current, [para(["a" * room, *extras]), formula])
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]
    r = check(current, [para(["a" * (room + 1), *extras]), formula])
    assert not r["ok"] and has(r["errors"], "лимит 32768")


def test_characters_count_captions_cells_summaries_and_button_text(current):
    blocks = [
        {"type": "details", "summary": "ab", "blocks": [para("c")]},
        {"type": "table", "cells": [[{**CELL, "text": {"type": "bold", "text": "de"}}]], "caption": "f"},
        {"type": "photo", "photo": {"type": "photo", "media": "x"}, "caption": {"text": "g", "credit": "h"}},
        {"type": "buttons", "buttons": [{"text": "ij", "url": "https://t.me"}]},
        {"type": "pullquote", "text": "k", "credit": "l"},
    ]
    assert check(current, blocks)["stats"]["chars"] == 12


def test_characters_are_unicode_characters_not_utf16_units(current):
    """ "UTF-8 characters": an emoji outside the BMP is one character (two UTF-16 code units, four bytes)."""
    r = check(current, [para("😀" * 100)])
    assert r["stats"]["chars"] == 100 and r["stats"]["max_string_bytes"] == 400, r["stats"]


# ---------- the silent cut by bytes ----------
CUT = "без ошибки обрежет"
ROOM = "ж" * (MAX_BYTES // 2)  # 34 990 bytes of Cyrillic, two a letter


def test_the_cut_is_a_few_bytes_under_what_telegram_kept():
    """Kept: 34 996-34 997 bytes of html and markdown source, 34 996 bytes of one string in blocks."""
    assert MAX_BYTES == 34990 and len(ROOM.encode()) == MAX_BYTES


def test_one_string_over_the_cut_is_an_error_in_blocks(current):
    """Measured: a paragraph of 20 000 «ж» went out as 17 498 of them (34 996 bytes), without an error."""
    r = check(current, [para(ROOM)])
    assert r["ok"] and r["stats"]["max_string_bytes"] == MAX_BYTES and r["stats"]["chars"] == len(ROOM), r["errors"]
    r = check(current, [para(), para(ROOM + "a")])
    assert not r["ok"] and has(r["errors"], f"rich_message.blocks[1]: строка текста 34991 байт в UTF-8: Telegram {CUT}")


def test_in_blocks_the_cut_is_for_each_string_not_the_post(current):
    """Measured: two paragraphs of 16 000 «ж» (64 000 bytes) and 300 of 60 «ж» (36 000 bytes) went out whole."""
    for blocks in ([para("ж" * 16000)] * 2, [para("ж" * 60)] * 300):
        r = check(current, blocks)
        assert r["ok"] and not has(r["warnings"], "байт"), r


@pytest.mark.parametrize(
    "text",
    [["ж" * 10000, "ж" * 10000], ["ж" * 10000, {"type": "bold", "text": "ж" * 10000}], ["ж" * 5000] * 4 + ["КОНЕЦ"]],
    ids=["two strings of 10 000 «ж»", "a string and bold", "four strings and КОНЕЦ"],
)
def test_strings_of_one_paragraph_are_cut_each_by_itself(current, text):
    """Measured: 40 000 bytes of text in one paragraph, as an array of strings, went out whole."""
    r = check(current, [para(text)])
    assert r["ok"] and not has(r["warnings"], "байт"), r


def test_in_blocks_the_characters_of_the_whole_post_still_count(current):
    """Measured: 300 paragraphs of 110 «ж» (33 000 characters) are RICH_MESSAGE_TEXT_TOO_LONG."""
    r = check(current, [para("ж" * 110)] * 300)
    assert not r["ok"] and has(r["errors"], "текст 33000 символов"), r


def test_emoji_and_formula_source_are_strings_of_their_own(current):
    r = check(current, [para("😀" * 8747 + "aa")])
    assert r["ok"] and r["stats"]["max_string_bytes"] == MAX_BYTES, r["errors"]
    assert not check(current, [para("😀" * 8747 + "aaa")])["ok"]
    formula = {"type": "mathematical_expression", "expression": "ж" * 17496}
    r = check(current, [para(), formula])
    assert has(r["errors"], "rich_message.blocks[1]: строка текста 34992 байт"), r["errors"]


def test_latin_text_over_both_limits_is_refused_not_cut(current):
    """Measured: 40 000 «a» are cut to ~35 000 and then refused as more than 32 768 characters."""
    r = check(current, [para("a" * 40000)])
    assert has(r["errors"], "лимит 32768") and not has(r["errors"], CUT), r["errors"]


def test_html_and_markdown_are_cut_as_their_source_at_the_edge(current):
    """Measured: html and markdown are cut by the bytes of the source, markup included."""
    html = "<p>" + "ж" * 17491 + "a</p>"  # 34 990 bytes
    for rm in ({"html": html}, {"markdown": ROOM}):
        r = check(current, **rm)
        assert r["ok"] and r["stats"]["bytes"] == MAX_BYTES, (rm.keys(), r["errors"])
    r = check(current, html=html.replace("a<", "aa<"))
    assert not r["ok"] and has(r["errors"], f"HTML 34991 байт в UTF-8 вместе с разметкой: Telegram {CUT}"), r
    r = check(current, markdown=ROOM + "a")
    assert not r["ok"] and has(r["errors"], "Markdown 34991 байт в UTF-8 вместе с разметкой"), r


@pytest.mark.parametrize(
    "rm",
    [
        {"html": ("<p>" + "ж" * 40 + "</p>") * 500},
        {"html": "<p>" + "ж" * 10000 + '</p><p><a href="https://example.com/' + "a" * 16000 + '">ссылка</a> КОНЕЦ</p>'},
        {"html": "<p>" + "ж" * 10000 + "</p><p>" + "<b>x</b>" * 2000 + " КОНЕЦ</p>"},
        {"markdown": "ж" * 10000 + "\n\n" + "**x** " * 2600 + "КОНЕЦ"},
    ],
    ids=["500 paragraphs of 40 «ж»", "a 16 KB link address", "2000 <b>x</b>", "markdown with 2600 **x**"],
)
def test_markup_takes_its_share_of_the_cut(current, rm):
    """Measured: 500 paragraphs of 40 «ж» (20 000 characters, 40 000 bytes of text, 43 500 of source) went out as
    403; the link address
    was cut and «КОНЕЦ» lost; the <b> tags were cut in the middle, a literal «<b» left in the text."""
    r = check(current, **rm)
    assert not r["ok"] and has(r["errors"], "байт в UTF-8 вместе с разметкой"), r


def test_the_cut_counts_the_source_as_sent(current):
    """Entities and tags are bytes of the source: what Telegram cuts, before it parses anything."""
    r = check(current, html="<p>" + "&amp;" * 7000 + "</p>")  # 7 000 characters of text, 35 007 bytes of source
    assert not r["ok"] and r["stats"]["chars"] == 7000 and r["stats"]["bytes"] == 35007, r


# ---------- blocks ----------
def test_500_blocks_pass_and_501_is_an_error(current):
    """Measured in html: 500 paragraphs pass, 501 are RICH_MESSAGE_BLOCKS_TOO_MANY."""
    r = check(current, [para()] * 500)
    assert r["ok"] and r["stats"]["blocks"] == 500, r["errors"]
    r = check(current, [para()] * 501)
    assert not r["ok"] and has(r["errors"], "лимит 500"), r


def test_a_list_is_a_block_besides_its_items(current):
    """Measured in html: a list of 499 items passes, of 500 is refused."""
    items = [{"blocks": [para()]} for _ in range(249)]  # 1 + 249 items + 249 paragraphs
    lst = {"type": "list", "items": items}
    r = check(current, [lst, para()])
    assert r["ok"] and r["stats"]["blocks"] == 500, r["errors"]
    r = check(current, [lst, para(), para()])
    assert not r["ok"] and has(r["errors"], "блоков вместе с пунктами списков и строками таблиц: 501")


def test_table_rows_count_as_blocks_and_cells_do_not(current):
    r = check(current, [{"type": "table", "cells": [[CELL, CELL]] * 499}])
    assert r["ok"] and r["stats"]["blocks"] == 500, r["errors"]
    r = check(current, [{"type": "table", "cells": [[CELL, CELL]] * 500}])
    assert not r["ok"] and has(r["errors"], "лимит 500")


def test_blocks_inside_quotes_details_and_collages_count(current):
    inner = [para()] * 200
    blocks = [
        {"type": "blockquote", "blocks": inner},  # 201
        {"type": "details", "summary": "s", "blocks": inner},  # 201
        {"type": "slideshow", "blocks": [photo()] * 2},  # 3
        {"type": "collage", "blocks": [photo()] * 2},  # 3
        *[para()] * 92,
    ]
    r = check(current, blocks)
    assert r["stats"]["blocks"] == 500 and r["ok"], r["errors"]
    blocks[0] = {"type": "blockquote", "blocks": [*inner, para()]}
    assert not check(current, blocks)["ok"]


# ---------- media ----------
def test_50_media_pass_and_51_is_an_error_counting_collages(current):
    r = check(current, [{"type": "collage", "blocks": [photo()] * 25}, *[photo()] * 25])
    assert r["ok"] and r["stats"]["media"] == 50, r["errors"]
    r = check(current, [{"type": "collage", "blocks": [photo()] * 25}, *[photo()] * 26])
    assert not r["ok"] and has(r["errors"], "медиа: 51, лимит 50"), r


def test_media_in_html_is_an_estimate_and_only_a_warning(current):
    img = '<img src="https://telegram.org/example/photo.jpg"/>'
    assert not has(check(current, html=img * 50)["warnings"], "медиа")
    r = check(current, html=img * 51)
    assert r["ok"] and has(r["warnings"], "медиа около 51, лимит 50"), r


# ---------- nesting in blocks ----------
def quotes(levels, inner=None):
    """Blockquotes nested `levels` deep around a paragraph."""
    block = inner or para()
    for _ in range(levels):
        block = {"type": "blockquote", "blocks": [block]}
    return block


KINDS = ["bold", "italic", "underline", "strikethrough"]


def fmt(levels, text="a"):
    for k in reversed(range(levels)):
        text = {"type": KINDS[k % 4], "text": text}
    return text


@pytest.mark.parametrize(
    ("blocks", "passes"),
    [
        ([para(fmt(15))], True),
        ([para(fmt(16))], False),
        ([quotes(1, para(fmt(14)))], True),
        ([quotes(1, para(fmt(15)))], False),
        ([quotes(15)], True),
        ([quotes(16)], False),
        ([{"type": "list", "items": [{"blocks": [para(fmt(14))]}]}], True),
        ([{"type": "list", "items": [{"blocks": [para(fmt(15))]}]}], False),
        ([{"type": "details", "summary": "s", "blocks": [para(fmt(14))]}], True),
        ([{"type": "table", "cells": [[{"text": fmt(15)}]]}], True),
        ([{"type": "table", "cells": [[{"text": fmt(16)}]]}], False),
    ],
    ids=[
        "paragraph+fmt15",
        "paragraph+fmt16",
        "blockquote>paragraph+fmt14",
        "blockquote>paragraph+fmt15",
        "15 blockquotes>paragraph",
        "16 blockquotes>paragraph",
        "list>item>paragraph+fmt14",
        "list>item>paragraph+fmt15",
        "details>paragraph+fmt14",
        "table cell+fmt15",
        "table cell+fmt16",
    ],
)
def test_nesting_in_blocks_as_measured(current, blocks, passes):
    """Measured: the blocks on the way to the text, the paragraph included, plus the levels of formatting, at most
    16; over it is RICH_MESSAGE_DEPTH_INVALID. A list item is not a level (list > item > paragraph is 2), the text of
    a table cell has no paragraph (a table is 1)."""
    r = check(current, blocks)
    errors = [e for e in r["errors"] if "вложенности" in e]
    assert (not errors) == passes and not has(r["warnings"], "вложенности"), r
    assert r["stats"]["depth"] == (16 if passes else 17)


@pytest.mark.parametrize(
    "blocks",
    [
        [{"type": "heading", "size": 2, "text": fmt(16)}],
        [para({"type": "url", "text": fmt(15), "url": "https://t.me"})],
        [para({"type": "spoiler", "text": fmt(15)})],
        [{"type": "pullquote", "text": fmt(16)}],
    ],
    ids=["heading+fmt16", "paragraph>url+fmt15", "paragraph>spoiler+fmt15", "pullquote+fmt16"],
)
def test_nesting_by_blocks_and_text_without_a_measurement_only_warns(current, blocks):
    """Only paragraph, blockquote, list, details, table and b/i/u/s were measured; the rest counts for a warning."""
    r = check(current, blocks)
    assert r["ok"] and r["stats"]["depth"] == 16, r
    assert has(r["warnings"], "около 17 уровней вложенности, лимит 16"), r["warnings"]


def test_the_nesting_error_names_the_deepest_block(current):
    r = check(current, [para(), quotes(16)])
    (err,) = [e for e in r["errors"] if "вложенности" in e]
    assert err.startswith("rich_message.blocks[1]" + ".blocks[0]" * 16 + ": 17 уровней вложенности, лимит 16"), err


def test_list_items_and_table_rows_are_not_levels(current):
    item = {"type": "list", "items": [{"blocks": [para()]}]}  # list, paragraph
    assert check(current, [item])["stats"]["depth"] == 2
    table = {"type": "table", "cells": [[{**CELL, "text": {"type": "bold", "text": "x"}}]]}  # table, bold
    assert check(current, [table])["stats"]["depth"] == 2


def test_arrays_of_rich_text_are_not_a_level(current):
    assert check(current, [para(["a", ["b", ["c"]]])])["stats"]["depth"] == 1


# ---------- nesting in html ----------
def bius(levels, inner="a"):
    """`levels` of <b><i><u><s>… one inside the other, as the measurements had them."""
    tags = [["b", "i", "u", "s"][k % 4] for k in range(levels)]
    return "".join(f"<{t}>" for t in tags) + inner + "".join(f"</{t}>" for t in reversed(tags))


def bq(levels, inner):
    return "<blockquote>" * levels + inner + "</blockquote>" * levels


def det(levels, inner):
    return "<details><summary>s</summary>" * levels + inner + "</details>" * levels


def li(levels, inner):
    return "<ul><li>" * levels + inner + "</li></ul>" * levels


HTML_DEPTH = {  # every html measurement of issue #170 and PR #173: name -> (source, Telegram took it)
    "p+fmt15": ("<p>" + bius(15) + "</p>", True),
    "fmt15": (bius(15), True),
    "bq1+fmt15": (bq(1, bius(15)), True),
    "bq2+fmt14": (bq(2, bius(14)), True),
    "bq8+fmt8": (bq(8, bius(8)), True),
    "bq14+fmt2": (bq(14, bius(2)), True),
    "bq15+fmt1": (bq(15, bius(1)), True),
    "bq16": (bq(16, "a"), True),
    "bq15>ul>li": (bq(15, li(1, "a")), True),
    "ul>li+fmt15": (li(1, bius(15)), True),
    "ul>li x16": (li(16, "a"), True),
    "td+fmt15": ("<table><tr><td>" + bius(15) + "</td></tr></table>", True),
    "aside+fmt15": ("<aside>" + bius(15) + "</aside>", True),
    "summary+fmt15": ("<details><summary>" + bius(15) + "</summary>a</details>", True),
    "details+fmt14": (det(1, bius(14)), True),
    "details>p+fmt14": (det(1, "<p>" + bius(14) + "</p>"), True),
    "details>bq+fmt14": (det(1, bq(1, bius(14))), True),
    "bq>details+fmt13": (bq(1, det(1, bius(13))), True),
    "details x9": (det(9, "a"), True),
    "bq>p+fmt15": (bq(1, "<p>" + bius(15) + "</p>"), True),
    "ul>li>p+fmt15": (li(1, "<p>" + bius(15) + "</p>"), True),
    "ul>li>p+fmt14": (li(1, "<p>" + bius(14) + "</p>"), True),
    "p+fmt16": ("<p>" + bius(16) + "</p>", False),
    "fmt16": (bius(16), False),
    "bq1+fmt16": (bq(1, bius(16)), False),
    "bq2+fmt15": (bq(2, bius(15)), False),
    "bq8+fmt9": (bq(8, bius(9)), False),
    "bq15+fmt2": (bq(15, bius(2)), False),
    "bq16+fmt1": (bq(16, bius(1)), False),
    "bq17": (bq(17, "a"), False),
    "bq16>ul>li": (bq(16, li(1, "a")), False),
    "ul>li+fmt16": (li(1, bius(16)), False),
    "ul>li x17": (li(17, "a"), False),
    "td+fmt16": ("<table><tr><td>" + bius(16) + "</td></tr></table>", False),
    "aside+fmt16": ("<aside>" + bius(16) + "</aside>", False),
    "summary+fmt16": ("<details><summary>" + bius(16) + "</summary>a</details>", False),
    "details+fmt15": (det(1, bius(15)), False),
    "details>p+fmt15": (det(1, "<p>" + bius(15) + "</p>"), False),
    "bq>details+fmt14": (bq(1, det(1, bius(14))), False),
    "details x16": (det(16, "a"), False),
}


@pytest.mark.parametrize("name", HTML_DEPTH)
def test_nesting_in_html_as_measured(current, name):
    """Measured: elements on the way to the text, at most 16; a list and a table are one level each; text at the top
    or right in <details> gets a paragraph of its own, text in blockquote, aside, li, td and summary does not; a <p>
    is a level only where it stands for that paragraph."""
    source, passes = HTML_DEPTH[name]
    r = check(current, html=source)
    errors = [e for e in r["errors"] if "вложенности" in e]
    assert (not errors) == passes and not has(r["warnings"], "вложенности"), r
    depth = {"details x9": 10, "ul>li>p+fmt14": 15}.get(name, 16 if passes else 17)
    assert r["stats"]["depth"] == depth, r["stats"]


def test_the_html_nesting_error_shows_where(current):
    r = check(current, html="<p>ок</p>" + bq(15, li(1, "<b><i>глубоко внутри цитат</i></b>")))
    (err,) = r["errors"]
    assert err.startswith("18 уровней вложенности, лимит 16: <blockquote> ×15 › <ul> › форматирование ×2 › «глубоко"), (
        err
    )


def test_unclosed_paragraphs_list_items_and_cells_are_siblings(current):
    """<p>a<p>b, <li>a<li>b and <td>a<td>b end the one before, as in HTML: forty of them are not forty levels."""
    for html in ("<p>a" * 40, "<ul>" + "<li>a" * 40 + "</ul>", "<table>" + "<tr><td>a<td>b" * 40 + "</table>"):
        r = check(current, html=html)
        assert r["ok"] and r["stats"]["depth"] == 1, (html[:30], r)


def test_tags_without_a_measurement_only_warn(current):
    """<h1>, <a>, <code>…, and a <p> in td, aside, summary were not measured: over the limit by them is a warning."""
    cell = "<table><tr><td><p>" + bius(15) + "</p></td></tr></table>"
    for html in ("<h1>" + bius(16) + "</h1>", "<p>" + bius(15, '<a href="https://t.me">a</a>') + "</p>", cell):
        r = check(current, html=html)
        assert r["ok"] and has(r["warnings"], "около 17 уровней вложенности, лимит 16"), (html, r)


def test_deep_html_is_checked_in_linear_time(current):
    started = time.perf_counter()
    r = check(current, html="<b>" * 50000 + "a" + "</i>" * 50000 + "<p>x" * 50000)
    assert not r["ok"] and r["stats"]["depth"] == 50001
    assert time.perf_counter() - started < 2


@pytest.mark.parametrize(
    "rm",
    [{"html": "<p>x&#xD800;</p><p><a" + "b" * 34900}, {"markdown": "x&#xD800; " + "`" * 17000 + "b" * 17900}],
    ids=["html: <a without its >", "markdown: a run of backticks without its closing run"],
)
def test_a_post_with_text_telegram_erases_is_checked_in_linear_time(current, rm):
    """One entity Telegram erases text for takes the check to the pieces of text: under the source limit a hostile
    source took 15 s (html) and 6 s (markdown) there, with the event loop of the studio standing still."""
    started = time.perf_counter()
    assert not check(current, **rm)["ok"]
    assert time.perf_counter() - started < 2


# 100 000 bytes: there is no limit on the body of a request, and the check runs in the event loop of the studio
HOSTILE = {
    "markdown: ]( without its )": {"markdown": "](" * 50000},
    "markdown: ]( and an entity Telegram erases": {"markdown": "x&#xD800; " + "](" * 50000},
    "markdown: < without its >": {"markdown": "<" * 100000},
    "markdown: < and an entity Telegram erases": {"markdown": "x&#xD800; " + "<" * 100000},
    "html: < without its >": {"html": "<" * 100000},
    "markdown: [^ without its ]": {"markdown": "[^" * 50000},
    "markdown: ![ without its ]": {"markdown": "![" * 50000},
    "html: <img without its >": {"html": "<img" * 25000},
    "markdown: a line of quote marks that is no heading": {"markdown": "x&#xD800;\n" + "> " * 50000},
    "markdown: [ at the start of every line": {"markdown": "[\n" * 50000},
    "markdown: [^ at the start of every line, one ] at the end": {"markdown": "[^\n" * 33333 + "]"},
}


@pytest.mark.parametrize("rm", HOSTILE.values(), ids=HOSTILE.keys())
def test_a_run_of_openings_without_their_end_is_checked_in_linear_time(current, rm):
    """Issue #192: from each «](» the link address was looked for up to the end of the source, so 100 000 bytes of
    them took 1 s, and 4 s with an entity Telegram erases; «<img» took 9 s."""
    started = time.perf_counter()
    assert not check(current, **rm)["ok"]
    assert time.perf_counter() - started < 0.5


def test_the_preview_tells_tags_from_text_the_same_way():
    """render.js keeps its own copy of HTML_TAG_RE (TAG): the text Telegram erases is the same piece in both."""
    found = re.search(r"const TAG = /(.+)/g;", RENDER_JS)
    assert found, "TAG not found in render.js"
    assert found[1].replace("\\/", "/") == HTML_TAG_RE.pattern


def test_markdown_nesting_is_left_to_telegram(current):
    """No measurement for markdown: its nesting is not checked."""
    r = check(current, markdown="> " * 20 + "**_глубоко_**")
    assert r["ok"] and not has(r["warnings"], "вложенности") and "depth" not in r["stats"], r


# ---------- a quote in an address ----------
@pytest.mark.parametrize(
    "href",
    [
        "https://example.com/?q=&quot;x&quot;",
        "https://example.com/?q=&#34;x",
        "https://example.com/?q=&#034;x",
        "https://example.com/?q=&#x22;x",
    ],
)
def test_a_quote_in_a_link_address_is_an_error(current, href):
    """Measured: with &quot; in href Telegram drops the link and keeps its text (html-href-quot in captured.json)."""
    r = check(current, html=f'<p>до <a href="{href}">ссылка <b>жирная</b></a> после</p>')
    assert not r["ok"], r
    (err,) = r["errors"]
    assert err.startswith('ссылка «ссылка жирная»: в адресе https://example.com/?q="x') and "%22" in err, err


def test_a_quote_in_a_single_quoted_address_is_the_same_quote(current):
    assert not check(current, html="<a href='https://example.com/?q=\"x\"'>ссылка</a>")["ok"]


@pytest.mark.parametrize(
    "href",
    [
        "https://example.com/?q=&#x27;x&#x27;",
        "https://example.com/?q=%22x%22",
        "https://example.com/?q=&quotx",
        "https://example.com/?q=&QUOT;x&QUOT;",
        "https://example.com/?q=&#X22;x",
    ],
)
def test_an_apostrophe_percent_22_or_an_entity_telegram_does_not_know_is_fine(current, href):
    """Measured: with &#x27; or %22 the link stays (html-href-apos, html-href-pct22); &quot without «;», &QUOT; and
    &#X22; Telegram does not decode, they stay in the address as typed."""
    r = check(current, html=f'<p><a href="{href}">ссылка</a></p>')
    assert r["ok"] and not r["warnings"], r


def test_a_quote_in_a_link_address_in_blocks_is_an_error(current):
    """Measured: in blocks too, a url with a " goes out as its text only."""
    link = {"type": "url", "text": "ссылка", "url": 'https://example.com/?q="x"'}
    r = check(current, [para(), para(["до ", {"type": "bold", "text": link}])])
    assert not r["ok"], r
    (err,) = r["errors"]
    assert err.startswith('rich_message.blocks[1]: ссылка «ссылка»: в адресе https://example.com/?q="x"'), err
    assert check(current, [para({**link, "url": "https://example.com/?q=%22x%22"})])["ok"]


def test_a_quote_in_a_button_address_in_html_is_an_error(current):
    """Measured: <tg-button url="…&quot;…"> is BUTTON_URL_INVALID for the whole post, %22 goes through."""
    row = '<p>x</p><tg-button-row><tg-button url="https://example.com/?q={}x{}">Кнопка</tg-button></tg-button-row>'
    r = check(current, html=row.format("&quot;", "&quot;"))
    assert (
        not r["ok"]
        and has(r["errors"], 'кнопка «Кнопка»: в адресе https://example.com/?q="x"')
        and has(r["errors"], "BUTTON_URL_INVALID")
    ), r
    assert check(current, html=row.format("%22", "%22"))["ok"]


def test_text_decodes_only_the_entities_telegram_knows(current):
    """Measured: «a &quot b &QUOT; c &#34; d» went out as «a &quot b &QUOT; c " d»."""
    r = check(current, html="<p>a &quot b &QUOT; c &#34; d</p>")
    assert r["stats"]["chars"] == len('a &quot b &QUOT; c " d'), r["stats"]


def test_telegram_decodes_exactly_16_named_entities():
    """Measured with all 2125 names of HTML5, in html and markdown: these 16, to the same characters as HTML5."""
    assert sorted(TG_ENTITIES) == sorted(
        [
            "amp",
            "apos",
            "copy",
            "gt",
            "hellip",
            "ldquo",
            "lsquo",
            "lt",
            "mdash",
            "nbsp",
            "ndash",
            "quot",
            "rdquo",
            "reg",
            "rsquo",
            "trade",
        ]
    )
    assert all(tg_unescape(f"&{name};") == html_lib.unescape(f"&{name};") for name in TG_ENTITIES)


@pytest.mark.parametrize("fmt", ["html", "markdown"])
def test_each_entity_telegram_decodes_is_one_character_and_the_rest_stay_as_typed(current, fmt):
    """Measured: the 16 are one character each; the other 2109 names, a name without «;» and &#X22; stay as typed."""
    for name in TG_ENTITIES:
        assert check(current, **{fmt: f"x&{name};y"})["stats"]["chars"] == 3, name
    for entity in ("&laquo;", "&raquo;", "&euro;", "&shy;", "&NBSP;", "&AMP;", "&nbsp", "&#X22;"):
        assert tg_unescape(entity) == entity
        assert check(current, **{fmt: f"x{entity}y"})["stats"]["chars"] == len(entity) + 2, entity


def test_the_preview_decodes_the_same_entities():
    """render.js keeps its own copy of the table (TG_ENTITIES) and of the numeric form, digits without a limit: the
    preview shows what Telegram will. What it makes of each number render_unit.mjs checks against the same
    measurement (telegram_parse/numeric_entities.json) as test_numeric_entities_decode_as_measured here."""
    table = re.search(r"const TG_ENTITIES = \{(.*?)\};", RENDER_JS, re.S)
    assert table, "TG_ENTITIES not found in render.js"
    names = set(re.findall(r"(\w+):", table[1]))
    assert names == set(TG_ENTITIES), names ^ set(TG_ENTITIES)
    numeric = TG_ENTITY_RE.pattern.split(")|", 1)[1]
    assert numeric == "#([0-9]+)|#x([0-9a-fA-F]+));", numeric
    form = re.search(r"const ENTITY_SRC = `(.*?)`;", RENDER_JS)
    assert form and form[1].endswith("|" + numeric), form


def test_numeric_entities_are_decoded_with_a_small_x():
    assert tg_unescape("&#34;&#034;&#160;&#8212;&#x22;&#X22;") == '"" —"&#X22;'


# ---------- numeric entities (measured, issue #174) ----------
@pytest.mark.parametrize(("entity", "made"), NUMERIC["text"], ids=[e for e, _ in NUMERIC["text"]])
def test_numeric_entities_decode_as_measured(entity, made):
    """Digits without a limit, 0…31 a space, 128…159 the same code point (no windows-1252), a capital X, a missing
    «;» or no digits stay as typed. In <pre> &#10; stays a line break: for the count it is one character all the same."""
    assert tg_unescape(f"a{entity}b") == f"a{made}b"


@pytest.mark.parametrize("fmt", ["html", "markdown"])
def test_numeric_entities_count_as_the_characters_telegram_makes(current, fmt):
    for entity, made in NUMERIC["text"]:
        r = check(current, **{fmt: f"x{entity}y"})
        assert r["ok"] and r["stats"]["chars"] == len(made) + 2, (entity, r)


def test_a_number_of_thousands_of_digits_is_read_without_a_500(current):
    """Python reads at most 4300 digits into an int: the zeros in front do not count, past U+10FFFF is past it."""
    assert tg_unescape("&#" + "0" * 5000 + "65;") == "A"
    r = check(current, html="<p>до</p><p>a&#" + "9" * 5000 + ";</p>")
    assert not r["ok"] and has(r["errors"], "стирает"), r


@pytest.mark.parametrize("entity", NUMERIC["vanish"])
def test_a_surrogate_or_a_number_past_unicode_erases_its_text_an_error(current, entity):
    """Measured: <p>a&#xD800;b</p> comes back as an empty paragraph, a heading or a quote left so is
    RICH_MESSAGE_CONTENT_REQUIRED for the whole post. The entity stays as typed in tg_unescape: it is no character."""
    assert tg_unescape(entity) == entity
    r = check(current, html=f"<p>до</p><p>a {entity} b</p>")
    assert not r["ok"], r
    (err,) = r["errors"]
    assert f"«a {entity} b»" in err and "RICH_MESSAGE_CONTENT_REQUIRED" in err, err
    r = check(current, markdown=f"до\n\na {entity} b")
    assert not r["ok"], r
    (err,) = r["errors"]
    assert f"a {entity} b" in err and "где кончается стёртый текст в Markdown, не замерено" in err, err


def test_only_the_text_up_to_the_nearest_tag_is_erased(current):
    """Measured: <p>a <b>b&#1114112;</b> c</p> is «a », bold «» and « c»."""
    r = check(current, html="<p>a <b>b&#1114112;</b> c</p>")
    (err,) = r["errors"]
    assert "«b&#1114112;»" in err and "«a" not in err, err
    assert r["stats"]["chars"] == len("a  c"), r["stats"]


def test_many_texts_telegram_erases_are_one_error(current):
    r = check(current, html="".join(f"<p>абзац {i}&#xD800;</p>" for i in range(5)))
    (err,) = r["errors"]
    assert "«абзац 0&#xD800;»" in err and "и ещё 4" in err and "«абзац 1&#xD800;»" in err, err
    assert "абзац 4" not in err, err


def test_in_markdown_code_entities_stay_as_typed(current):
    """Measured: in a code fence Telegram decodes no entity. An indented code block is a pre for Telegram too
    (captured md-code), and markdown-it in the preview leaves the entities there as typed."""
    fences = ["```\na&#xD800;b\n```", "~~~~\n```\na&#xD800;b\n~~~~", "    a&#xD800;b", "до\n\n    a&#xD800;b\n\n    c"]
    for src in fences:
        r = check(current, markdown=src)
        assert r["ok"] and not r["warnings"], (src, r)
    assert not check(current, markdown="```\nкод\n```\n\na&#xD800;b")["ok"]
    assert not check(current, markdown="    код\n\na&#xD800;b")["ok"]


AFTER_A_HEADING = [
    "# h\n    a&#xD800;b",
    "до\n## h ##\n\ta&#xD800;b",
    "h\n===\n    a&#xD800;b",
    "h\n---\n    a&#xD800;b",
    "***\n    a&#xD800;b",
    "до\n- - -\n    a&#xD800;b",
    "> # h\n    a&#xD800;b",
    "> h\n> ===\n    a&#xD800;b",
]


@pytest.mark.parametrize("src", AFTER_A_HEADING)
def test_in_markdown_a_line_indented_right_after_a_heading_is_code(current, src):
    """Issue #192: a heading or a thematic break is a block of its own, so an indented line right after it starts a
    code block, not the next line of a paragraph (CommonMark; markdown-it in the preview reads it so). Not measured on
    Telegram."""
    r = check(current, markdown=src)
    assert r["ok"] and not r["warnings"], r


@pytest.mark.parametrize(
    "src",
    [
        "#h\n    a&#xD800;b",
        "- # h\n    a&#xD800;b",
        "    # h\n\na&#xD800;b",
        "# h&#xD800;\n    a",
        # an underline in another quote than the text: markdown-it starts a quote with it or reads it on with the text
        "a\n> ===\n    x&#xD800;",
        "> a\n===\n    x&#xD800;",
    ],
)
def test_in_markdown_what_is_no_heading_or_the_heading_itself_is_still_erased(current, src):
    r = check(current, markdown=src)
    assert not r["ok"] and has(r["errors"], "стирает"), r


@pytest.mark.parametrize(
    "src, counts",
    [("[a](x](y))", (4, 1)), ('[a](u "](t")', (3, 1)), ('<a href="x<y">t</a>', (1, 1))],
)
def test_link_addresses_and_tags_end_where_they_did(src, counts):
    """The linear reading of #192 is the same as before it: a link address ends at the first «)», a tag at the first
    «>», whatever is in between. markdown-it reads [a](x](y)) and [a](u "](t") as a link «a»."""
    assert source_chars(src, markdown=True) == counts


def test_an_entity_in_a_link_address_with_a_bracket_is_not_erased(current):
    r = check(current, markdown="[a](&#xD800;](b))")
    assert not has(r["errors"], "стирает"), r


def test_text_telegram_erases_in_markdown_is_taken_out_of_the_count_once(current):
    """Issue #192: «<x…d>» over three paragraphs is one tag for the count, and the erased paragraph in the middle was
    taken out of the count a second time: chars -6, at least -5."""
    src = "a <x\n\nb&#xD800;c\n\nd> e"
    assert source_chars(src, markdown=True) == (len("a  e"), 2)
    r = check(current, markdown=src)
    assert not r["ok"] and r["stats"]["chars"] == len("a  e"), r


def test_in_markdown_inline_code_an_entity_telegram_erases_for_is_a_warning(current):
    """In `code` it was not measured: the preview leaves the entity as typed (markdown-it), the check warns."""
    for src in ("до `a&#xD800;b` после", "до ``a`&#xD800;`` x"):
        r = check(current, markdown=src)
        assert r["ok"], (src, r)
        (warning,) = r["warnings"]
        assert "не замерено" in warning and "сотрёт абзац" in warning, warning


def test_a_markdown_link_address_keeps_an_entity_telegram_erases_for(current):
    """Measured: in [x](…) Telegram decodes no entity, so nothing is erased there; in the text of the link it is."""
    assert check(current, markdown="до [ссылка](https://example.com/&#xD800;) после")["ok"]
    assert not check(current, markdown="до [ссылка&#xD800;](https://example.com/) после")["ok"]


def test_an_entity_telegram_cannot_make_a_character_in_an_address_is_left_to_it(current):
    """Not measured in an attribute: the entity stays as typed, no error."""
    assert check(current, html='<p><a href="https://example.com/&#xD800;">ссылка</a></p>')["ok"]


# ---------- the address of a link or a button (measured, issue #174) ----------
DROPS = ['"', "<", ">", " ", "\t", "\n", "\x00", "\x01", "\x1f", "\x85", "\xa0", "\u2003", "\u3000", "\u200b"]
ENCODED = ["&#34;", "&#x0000022;", "&quot;", "&lt;", "&gt;", "&#32;", "&#9;", "&#10;", "&#0;", "&#1;", "&#31;"]
KEPT = ["\x7f", "\x80", "\xad", "\ufeff", "'", "\\", "{|}"]


def link_html(address: str) -> str:
    return f'<p>до <a href="https://example.com/?q={address}x">ссылка</a> после</p>'


def link_blocks(address: str) -> list:
    return [para(["до ", {"type": "url", "text": "ссылка", "url": f"https://example.com/?q={address}x"}])]


@pytest.mark.parametrize("char", DROPS + ENCODED + ["&#x2003;"], ids=repr)
def test_a_character_that_makes_telegram_drop_a_link_is_an_error(current, char):
    """Measured: with " < > U+0000…U+0020 U+0085 U+00A0 U+2003 U+3000 U+200B in the address, entities decoded,
    Telegram drops the link and keeps its text, in <a href> and in the url of a url element in blocks alike."""
    in_html = html_lib.escape(char) if char in '"<>' else char
    for r in (check(current, html=link_html(in_html)), check(current, link_blocks(tg_unescape(char)))):
        assert not r["ok"], (char, r)
        (err,) = r["errors"]
        assert "ссылка «ссылка»: в адресе" in err and "выбрасывает ссылку" in err, err


def test_the_link_error_says_what_to_write_instead(current):
    (err,) = check(current, link_blocks('"x y'))["errors"]
    assert 'кавычка " — %22' in err and "пробел — %20" in err, err


@pytest.mark.parametrize("char", KEPT, ids=repr)
def test_a_character_telegram_keeps_in_a_link_address_is_fine(current, char):
    for r in (check(current, html=link_html(html_lib.escape(char))), check(current, link_blocks(char))):
        assert r["ok"] and not r["warnings"], (char, r)


def test_u2028_is_taken_out_of_a_link_address_a_warning(current):
    """Measured: Telegram takes U+2028 out of the address and keeps the link."""
    for r in (check(current, html=link_html("&#x2028;")), check(current, link_blocks("\u2028"))):
        assert r["ok"], r
        (warning,) = r["warnings"]
        assert "U+2028" in warning and "уберёт" in warning, warning


@pytest.mark.parametrize("char", ["\u2002", "\u2009", "\u202f", "\u1680"], ids=repr)
def test_a_space_without_a_measurement_in_a_link_address_only_warns(current, char):
    for r in (check(current, html=link_html(char)), check(current, link_blocks(char))):
        assert r["ok"], (char, r)
        (warning,) = r["warnings"]
        assert f"U+{ord(char):04X}" in warning and "не замерен" in warning, warning


def test_the_preview_drops_a_link_over_the_same_characters():
    """render.js keeps its own copy of the set (LINK_DROPS): the same characters, read by Python's re."""
    found = re.search(r"const LINK_DROPS = /(.+)/;", RENDER_JS)
    assert found, "LINK_DROPS not found in render.js"
    preview = re.compile(found[1])
    assert {c for c in map(chr, range(0x10000)) if preview.search(c)} == LINK_DROPS


def button(**action) -> dict:
    return {"type": "buttons", "buttons": [{"text": "Кнопка", **action}]}


def button_actions(address: str) -> list[dict]:
    """The url of a «Кнопки» button and the url of its web_app and login_url action."""
    url = f"https://example.com/?q={address}x"
    return [button(url=url), button(web_app={"url": url}), button(login_url={"url": url})]


@pytest.mark.parametrize("char", ['"', "<", "\xa0", "\u200b"], ids=repr)
def test_a_character_that_makes_telegram_refuse_a_button_is_an_error(current, char):
    """Measured: BUTTON_URL_INVALID for the whole post, in url, web_app.url and login_url.url alike."""
    for b in button_actions(char):
        r = check(current, [para(), b])
        assert not r["ok"], (char, b, r)
        (err,) = r["errors"]
        assert err.startswith("rich_message.blocks[1]: кнопка «Кнопка»: в адресе"), err
        assert "BUTTON_URL_INVALID" in err, err
    row = '<p>x</p><tg-button-row><tg-button type="web_app" url="{}">Кнопка</tg-button></tg-button-row>'
    assert not check(current, html=row.format(f"https://t.me/?q={html_lib.escape(char)}x"))["ok"]


@pytest.mark.parametrize("char", [" ", "\t"], ids=repr)
def test_a_space_or_a_tab_in_a_button_address_telegram_encodes_itself(current, char):
    """Measured: the button is taken, Telegram writes them %20 and %09."""
    for b in button_actions(char):
        r = check(current, [para(), b])
        assert r["ok"] and not r["warnings"], (b, r)


def test_u2028_is_taken_out_of_a_button_address_a_warning(current):
    for b in button_actions("\u2028"):
        r = check(current, [para(), b])
        assert r["ok"] and has(r["warnings"], "U+2028"), r


@pytest.mark.parametrize("char", [">", "\n", "\x85", "\u2003", "\u3000", "\u2002"], ids=repr)
def test_a_character_without_a_measurement_in_a_button_address_only_warns(current, char):
    for b in button_actions(char):
        r = check(current, [para(), b])
        assert r["ok"], (b, r)
        (warning,) = r["warnings"]
        assert "кнопка «Кнопка»" in warning and "не замерен" in warning, warning


def test_a_link_in_the_text_of_a_table_cell_is_a_link_not_a_button(current):
    """A cell has text and no type, like a button; the url element in its text is still a link."""
    link = {"type": "url", "text": "ссылка", "url": "https://example.com/? x"}
    (err,) = check(current, [{"type": "table", "cells": [[{**CELL, "text": link}]]}])["errors"]
    assert "ссылка «ссылка»" in err, err


def test_an_attribute_right_after_a_quote_is_found(current):
    """html.parser and the browser read href in <a title="x"href="…">: the check reads it too."""
    r = check(current, html='<p><a title="x"href="https://example.com/?q=&quot;x">ссылка</a></p>')
    assert not r["ok"] and has(r["errors"], 'ссылка «ссылка»: в адресе https://example.com/?q="x'), r
    row = '<tg-button-row><tg-button title="x"url="https://t.me/?q=&quot;x">К</tg-button></tg-button-row>'
    r = check(current, html=row)
    assert not r["ok"] and has(r["errors"], "BUTTON_URL_INVALID"), r


# ---------- upload: outside a file field ----------
UPLOAD_LINKS = {
    "url element": [para({"type": "url", "text": "файл", "url": "upload:a.jpg"})],
    "button url": [button(url="upload:a.jpg")],
    "button web_app.url": [button(web_app={"url": "upload:a.jpg"})],
    "button login_url.url": [button(login_url={"url": "upload:a.jpg"})],
}
UPLOAD_HTML = {
    "a href": '<p><a href="upload:a.jpg">файл</a></p>',
    "tg-button url": '<tg-button-row><tg-button url="upload:a.jpg">файл</tg-button></tg-button-row>',
    "img src": '<img src="upload:a.jpg">',
}


@pytest.mark.parametrize("where", [*UPLOAD_LINKS, *UPLOAD_HTML])
def test_upload_outside_a_file_field_is_an_error(current, where):
    """The studio puts a file in for upload:<name> only in a file field (telegram/uploads.py, FILE_KEYS): in a link
    Telegram would get «upload:a.jpg» as it is."""
    r = check(current, UPLOAD_LINKS[where]) if where in UPLOAD_LINKS else check(current, html=UPLOAD_HTML[where])
    assert not r["ok"], r
    (err,) = r["errors"]
    assert "upload:a.jpg" in err and "поле файла" in err, err


def test_upload_in_a_button_under_the_post_is_an_error():
    rows = [[{"text": "a", "url": "upload:a.jpg"}], [{"text": "b", "web_app": {"url": "upload:b.jpg"}}]]
    errors = keyboard_errors({"inline_keyboard": rows})
    assert len(errors) == 2 and all("поле файла" in e for e in errors), errors


def test_no_link_field_is_a_file_field(current):
    """The check looks for upload: in the url of links and buttons and in href, url and src of html: none of them is a
    field the studio puts a file in, and a file field with upload: is fine."""
    from telegram.uploads import FILE_KEYS

    assert not {"url", "href", "src"} & FILE_KEYS
    assert check(current, [photo()])["ok"]


def test_a_check_without_a_database_has_no_data_folder_to_write_to():
    """conftest.fresh_data under no_db: the data folder lies under a file, so a write by mistake fails at once."""
    import settings

    with pytest.raises(OSError):
        settings.get().media_dir.mkdir(parents=True, exist_ok=True)


def test_a_markdown_link_address_keeps_its_entities(current):
    """Measured: in [ссылка](…?q=&quot;x&quot;) Telegram decodes nothing, the link stays: no error, unlike html."""
    r = check(current, markdown="[ссылка](https://example.com/?q=&quot;x&quot;)")
    assert r["ok"] and not r["warnings"], r


def test_a_quote_in_a_button_address_in_blocks_is_an_error(current):
    """Measured: a button of a buttons block with a " in url is BUTTON_URL_INVALID, as <tg-button> in html."""
    buttons = {"type": "buttons", "buttons": [{"text": "Кнопка", "url": 'https://example.com/?q="x"'}]}
    r = check(current, [para(), buttons])
    assert not r["ok"], r
    (err,) = r["errors"]
    assert err.startswith('rich_message.blocks[1]: кнопка «Кнопка»: в адресе https://example.com/?q="x"'), err
    assert "BUTTON_URL_INVALID" in err
    buttons["buttons"][0]["url"] = "https://example.com/?q=%22x%22"
    assert check(current, [para(), buttons])["ok"]


def test_captured_parses_agree_with_the_link_check(current):
    for name in ("html-href-quot", "html-href-apos", "html-href-pct22"):
        case = CAPTURED[name]
        has_link = "url" in json.dumps(case["blocks"])
        assert check(current, html=case["source"])["ok"] == has_link, name


# ---------- table columns ----------
def test_table_of_20_columns_passes_and_21_is_an_error_with_the_table_path(current):
    """Measured in html: 20 columns pass, 21 are RICH_MESSAGE_TABLE_COLS_TOO_MANY."""
    r = check(current, [para(), {"type": "table", "cells": [[CELL] * 20, [CELL] * 3]}])
    assert r["ok"] and not r["warnings"], r
    r = check(current, [para(), {"type": "table", "cells": [[CELL] * 3, [CELL] * 21]}])
    assert not r["ok"] and "rich_message.blocks[1]: колонок в таблице 21, лимит 20" in r["errors"], r["errors"]


def test_colspan_counts_columns(current):
    """Measured in html: colspan="21" is refused too."""
    wide = {**CELL, "colspan": 2}
    assert check(current, [{"type": "table", "cells": [[wide] + [CELL] * 18]}])["ok"]
    r = check(current, [{"type": "table", "cells": [[wide] + [CELL] * 19]}])
    assert has(r["errors"], "колонок в таблице 21"), r
    r = check(current, [{"type": "table", "cells": [[{**CELL, "colspan": 21}]]}])
    assert has(r["errors"], "колонок в таблице 21"), r


def test_huge_colspan_is_an_error_not_a_hang(current):
    r = check(current, [{"type": "table", "cells": [[{**CELL, "colspan": 10**9, "rowspan": 2}], [CELL]]}])
    assert has(r["errors"], f"колонок в таблице {10**9}")


def test_a_cell_without_align_and_valign_is_not_an_error(current):
    """Required in the spec, but measured: Telegram takes such cells and sets left and middle (NOT_REQUIRED)."""
    r = check(current, [{"type": "table", "cells": [[{"text": "a"}, {"text": "b"}]]}])
    assert r["ok"] and not r["warnings"], r


def test_rowspan_takes_no_column_in_the_rows_below(current):
    """Measured: a cell with rowspan above a row of 20 cells passes, though in HTML that table is 21 columns wide."""
    tall = {**CELL, "rowspan": 2}
    r = check(current, [{"type": "table", "cells": [[tall] + [CELL] * 19, [CELL] * 20]}])
    assert r["ok"] and not r["warnings"], r
    assert table_columns([[tall] + [CELL] * 19, [CELL] * 20]) == 20


# ---------- html and markdown ----------
def test_html_text_over_the_limit_is_an_error(current):
    r = check(current, html="<p>" + "a" * 32768 + "</p>")
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]
    r = check(current, html="<p>" + "a" * 32769 + "</p>")
    assert not r["ok"] and has(r["errors"], "лимит 32768")


def test_html_entities_are_one_character(current):
    r = check(current, html="<p>a&amp;b&#x1F600;</p>")
    assert r["stats"]["chars"] == 4 and r["stats"]["bytes"] == 23  # the cut is of the source as sent


def test_spaces_of_html_layout_make_only_a_warning(current):
    """Indentation between tags may not be text for Telegram: over the limit only with it, the post is not refused."""
    html = "<p>" + "a" * 30000 + "</p>" + ("\n" + " " * 40 + "<p>а</p>") * 80  # 34 007 bytes of source
    r = check(current, html=html)
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "лимит 32768"), r["warnings"]


def test_markdown_link_addresses_are_not_text(current):
    link = "[link](https://example.com/a/very/long/address/that/is/not/text)"
    r = check(current, markdown=f"{'a' * 28000} {link * 100}")  # the source is 34 501 bytes, the text 28 601
    assert r["ok"] and not has(r["warnings"], "лимит 32768"), r


def test_markdown_markup_is_not_counted_for_the_error(current):
    r = check(current, markdown="a" * 32769)
    assert not r["ok"] and has(r["errors"], "лимит 32768")
    # 32760 letters of text in bold: the asterisks push the source over, but the text may well be under
    r = check(current, markdown="**" + "a" * 32760 + "**\n" + "- [x] **a**\n" * 4)
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "текст около"), r["warnings"]


def test_markdown_cells_past_the_header_are_not_an_error(current):
    """Telegram cuts a table row to the width of its header: the third cell here is dropped, not sent."""
    r = check(current, markdown="| a | b |\n|---|---|\n| x | y | " + "a" * 32800 + " |")
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "текст около"), r["warnings"]


@pytest.mark.parametrize("name", [k for k, v in CAPTURED.items() if v.get("blocks") and v.get("source")])
def test_the_at_least_count_is_never_over_telegrams_own(current, name):
    """The error for html/markdown rests on the lower estimate: on every real parse it is at most what Telegram made."""
    case = CAPTURED[name]
    blocks = spec.to_input_blocks(current, case["blocks"])
    exact = check(current, blocks)["stats"]["chars"]
    about, at_least = source_chars(case["source"], markdown=case["format"] == "markdown")
    assert at_least <= exact, (at_least, exact, about)


# ---------- the whole post ----------
def test_a_table_over_the_limit_does_not_go_to_telegram(client, tg):
    post = {"rich_message": {"blocks": [{"type": "table", "cells": [[CELL] * 21]}]}}
    r = client.post("/api/send", json={"post": post, "chat_id": "@chan"})
    assert r.status_code == 422 and "колонок в таблице 21" in str(r.json()), r.json()
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_a_long_russian_post_does_not_go_out_cut(client, tg):
    """The bug of issue #170: 20 000 Russian letters went to the channel as 17 497, and nobody said a word."""
    post = {"rich_message": {"html": "<p>" + "ж" * 20000 + "</p>"}}
    r = client.post("/api/send", json={"post": post, "chat_id": "@chan"})
    assert r.status_code == 422 and CUT in str(r.json()), r.json()
    assert not [c for c in tg.calls if c["method"] == "sendRichMessage"]


def test_a_rich_reply_to_a_button_is_held_to_the_same_limits(client, tg):
    """The rich reply of #163 is a rich message too: the cut, nesting and a " in a link stop the post before it goes."""
    like = {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]}

    def errors(rich_message):
        post = {
            "rich_message": {"blocks": [para()]},
            "reply_markup": like,
            "callbacks": {"like": {"reply": {"rich_message": rich_message}}},
        }
        return client.post("/api/validate", json=post).json()["errors"]

    (cut,) = errors({"html": "<p>" + "ж" * 20000 + "</p>"})
    assert cut.startswith("ответ на кнопку «👍»: ") and CUT in cut, cut
    (deep,) = errors({"blocks": [quotes(16)]})
    assert deep.startswith("ответ на кнопку «👍»: ") and "17 уровней вложенности" in deep, deep
    (link,) = errors({"html": '<p><a href="https://example.com/?q=&quot;x&quot;">ссылка</a></p>'})
    assert "%22" in link, link
    assert errors({"blocks": [{"type": "table", "cells": [[{"text": "a"}]]}]}) == []
