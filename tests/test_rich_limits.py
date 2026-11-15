"""Rich Message Limits (https://core.telegram.org/bots/api#rich-message-limits): each limit right at the edge and one over.

How Telegram reads them was measured on the real Bot API 10.4 (issue #170); every measurement of the issue is a case
here with the answer Telegram gave. Over a limit is an error: Telegram refuses the post, or (the length in bytes)
cuts it without a word. A warning only where the studio cannot count exactly: the text of html/markdown before
Telegram parses it, html tags no measurement had, posts of many paragraphs near the cut."""

import json
import time
from pathlib import Path

import pytest

import spec
from spec.validator import MAX_TEXT_BYTES, source_size, table_columns

CELL = {"text": "x", "align": "left", "valign": "top"}
CAPTURED = json.loads((Path(__file__).parent / "telegram_parse" / "captured.json").read_text("utf-8"))


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
    assert r["stats"]["chars"] == 100 and r["stats"]["bytes"] == 400, r["stats"]


# ---------- the silent cut by bytes ----------
CUT = "без ошибки обрежет"
ROOM = "ж" * (MAX_TEXT_BYTES // 2)  # 34 986 bytes of Cyrillic, two a letter


def test_the_cut_is_the_least_telegram_kept_in_the_measurements():
    """One paragraph kept 34 993-34 996 bytes, two and three paragraphs 34 986: the least of them."""
    assert MAX_TEXT_BYTES == 34986 and len(ROOM.encode()) == MAX_TEXT_BYTES


def test_cyrillic_over_the_cut_is_an_error_in_blocks(current):
    """Measured: a paragraph of 32 769…100 000 «ж» went out as 17 497 of them, without an error."""
    r = check(current, [para(ROOM)])
    assert r["ok"] and r["stats"]["bytes"] == MAX_TEXT_BYTES and r["stats"]["chars"] == len(ROOM), r["errors"]
    r = check(current, [para(ROOM + "a")])
    assert not r["ok"] and has(
        r["errors"], f"текст 34987 байт в UTF-8 с alternative_text эмодзи и исходником формул: Telegram {CUT}"
    ), r


def test_the_cut_is_for_the_whole_post_not_a_paragraph(current):
    """Measured: two paragraphs of 10 000 «ж» went out as 10 000 + 7 493."""
    r = check(current, [para("ж" * 10000), para("ж" * 10000)])
    assert not r["ok"] and has(r["errors"], "текст 40000 байт"), r


def test_emoji_and_mixed_text_are_counted_in_bytes(current):
    """Measured: 20 000 emoji went out as 8 749 (34 996 bytes); 20 000 «a» + 10 000 «ж» as 27 497 characters."""
    r = check(current, [para("😀" * 8746 + "aa")])
    assert r["ok"] and r["stats"]["bytes"] == MAX_TEXT_BYTES, r["errors"]
    assert not check(current, [para("😀" * 8746 + "aaa")])["ok"]
    r = check(current, [para("a" * 20000 + "ж" * 10000)])
    assert not r["ok"] and has(r["errors"], CUT), r


def test_alternative_text_and_formulas_count_in_bytes_too(current):
    emoji = {"type": "custom_emoji", "custom_emoji_id": "5368324170671202286", "alternative_text": "👍"}
    assert check(current, [para([ROOM[:-2], emoji])])["ok"]
    r = check(current, [para([ROOM[:-2], emoji, emoji])])
    assert not r["ok"] and has(r["errors"], CUT), r


def test_latin_text_over_both_limits_is_refused_not_cut(current):
    """Measured: 40 000 «a» are cut to ~35 000 and then refused as more than 32 768 characters."""
    r = check(current, [para("a" * 40000)])
    assert has(r["errors"], "лимит 32768") and not has(r["errors"], CUT), r["errors"]


def test_the_cut_in_html_and_markdown(current):
    """Measured: the same cut in html, markdown and blocks (markdown of 20 000 «ж» went out as 17 498)."""
    for rm in ({"html": "<p>" + ROOM + "</p>"}, {"markdown": ROOM}):
        r = check(current, **rm)
        assert r["ok"] and r["stats"]["bytes"] == MAX_TEXT_BYTES, (rm.keys(), r["errors"])
    for rm in ({"html": "<p>" + ROOM + "a</p>"}, {"markdown": ROOM + "a"}):
        r = check(current, **rm)
        assert not r["ok"] and has(r["errors"], f"текст не меньше 34987 байт в UTF-8: Telegram {CUT}"), r


def test_html_layout_and_markup_over_the_cut_only_warn(current):
    """Spaces between tags and Markdown markup may not be text: over the cut only with them is a warning."""
    r = check(current, html="<p>" + ROOM[:-500] + "</p>" + "\n      <p>я</p>" * 100)
    assert r["ok"] and has(r["warnings"], "текст около"), r
    r = check(current, markdown="**" + ROOM[:-10] + "**\n" + "- [x] **я**\n" * 10)
    assert r["ok"] and has(r["warnings"], "текст около"), r


def test_near_the_cut_a_warning_to_check_the_end(current):
    """Two paragraphs already lost a few bytes more than one, many paragraphs were not measured."""
    r = check(current, [para("ж" * 16384)])  # 32 768 bytes
    assert r["ok"] and not has(r["warnings"], "проверьте конец поста"), r["warnings"]
    r = check(current, [para("ж" * 8000)] * 2 + [para("ж" * 385)])
    assert r["ok"] and has(r["warnings"], "текст 32770 байт в UTF-8, а после ~34986 Telegram"), r["warnings"]
    r = check(current, html="<p>" + "ж" * 16385 + "</p>")
    assert r["ok"] and has(r["warnings"], "текст около 32770 байт"), r["warnings"]


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
    ],
    ids=[
        "paragraph+fmt15",
        "paragraph+fmt16",
        "blockquote>paragraph+fmt14",
        "blockquote>paragraph+fmt15",
        "15 blockquotes>paragraph",
        "16 blockquotes>paragraph",
    ],
)
def test_nesting_in_blocks_as_measured(current, blocks, passes):
    """Measured: the blocks on the way to the text, the paragraph included, plus the levels of formatting, at most
    16; over it is RICH_MESSAGE_DEPTH_INVALID."""
    r = check(current, blocks)
    errors = [e for e in r["errors"] if "вложенности" in e]
    assert (not errors) == passes and not has(r["warnings"], "вложенности"), r
    assert r["stats"]["depth"] == (16 if passes else 17)


def test_the_nesting_error_names_the_deepest_block(current):
    r = check(current, [para(), quotes(16)])
    (err,) = [e for e in r["errors"] if "вложенности" in e]
    assert err.startswith("rich_message.blocks[1]" + ".blocks[0]" * 16 + ": 17 уровней вложенности, лимит 16"), err


def test_list_items_and_table_rows_are_not_levels(current):
    """In html a list (ul with li) and a table (table, tr, td) are one level each (measured)."""
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


HTML_DEPTH = {  # every html measurement of issue #170: name -> (source, Telegram took it)
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
    or right in <details> gets a paragraph of its own, text in blockquote, aside, li, td and summary does not."""
    source, passes = HTML_DEPTH[name]
    r = check(current, html=source)
    errors = [e for e in r["errors"] if "вложенности" in e]
    assert (not errors) == passes and not has(r["warnings"], "вложенности"), r
    assert r["stats"]["depth"] == (10 if name == "details x9" else 16 if passes else 17)


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
    """<h1>, <a>, <code>… were not measured: by the same reading over the limit is only a warning."""
    for html in ("<h1>" + bius(16) + "</h1>", "<p>" + bius(15, '<a href="https://t.me">a</a>') + "</p>"):
        r = check(current, html=html)
        assert r["ok"] and has(r["warnings"], "около 17 уровней вложенности, лимит 16"), (html, r)


def test_deep_html_is_checked_in_linear_time(current):
    started = time.perf_counter()
    r = check(current, html="<b>" * 50000 + "a" + "</i>" * 50000 + "<p>x" * 50000)
    assert not r["ok"] and r["stats"]["depth"] == 50001
    assert time.perf_counter() - started < 2


def test_markdown_nesting_is_left_to_telegram(current):
    """No measurement for markdown: its nesting is not checked."""
    r = check(current, markdown="> " * 20 + "**_глубоко_**")
    assert r["ok"] and not has(r["warnings"], "вложенности") and "depth" not in r["stats"], r


# ---------- links in html ----------
@pytest.mark.parametrize(
    "href",
    ["https://example.com/?q=&quot;x&quot;", "https://example.com/?q=&#34;x", "https://example.com/?q=&#x22;x"],
)
def test_a_quote_in_a_link_address_is_an_error(current, href):
    """Measured: with &quot; in href Telegram drops the link and keeps its text (html-href-quot in captured.json)."""
    r = check(current, html=f'<p>до <a href="{href}">ссылка <b>жирная</b></a> после</p>')
    assert not r["ok"], r
    (err,) = r["errors"]
    assert err.startswith('ссылка «ссылка жирная»: в адресе https://example.com/?q="x') and "%22" in err, err


def test_a_quote_in_a_single_quoted_address_is_the_same_quote(current):
    assert not check(current, html="<a href='https://example.com/?q=\"x\"'>ссылка</a>")["ok"]


@pytest.mark.parametrize("href", ["https://example.com/?q=&#x27;x&#x27;", "https://example.com/?q=%22x%22"])
def test_an_apostrophe_or_percent_22_in_a_link_address_is_fine(current, href):
    """Measured: with &#x27; or %22 in href the link stays (html-href-apos, html-href-pct22)."""
    r = check(current, html=f'<p><a href="{href}">ссылка</a></p>')
    assert r["ok"] and not r["warnings"], r


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
    assert r["stats"]["chars"] == 4 and r["stats"]["bytes"] == 7


def test_spaces_of_html_layout_make_only_a_warning(current):
    """Indentation between tags may not be text for Telegram: over the limit only with it, the post is not refused."""
    html = "<p>" + "a" * 30000 + "</p>" + "\n    <p>а</p>" * 600
    r = check(current, html=html)
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "лимит 32768"), r["warnings"]


def test_markdown_link_addresses_are_not_text(current):
    link = "[link](https://example.com/a/very/long/address/that/is/not/text)"
    r = check(current, markdown=f"{'a' * 31000} {link * 100}")  # the source is over 37 000
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
    r = check(current, markdown="| a | b |\n|---|---|\n| x | y | " + "я" * 32800 + " |")
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "текст около"), r["warnings"]


@pytest.mark.parametrize("name", [k for k, v in CAPTURED.items() if v.get("blocks") and v.get("source")])
def test_the_at_least_count_is_never_over_telegrams_own(current, name):
    """The error for html/markdown rests on the lower estimate: on every real parse it is at most what Telegram made,
    in characters and in bytes."""
    case = CAPTURED[name]
    blocks = spec.to_input_blocks(current, case["blocks"])
    exact = check(current, blocks)["stats"]
    size = source_size(case["source"], markdown=case["format"] == "markdown")
    assert size.sure_chars <= exact["chars"] and size.sure_utf8 <= exact["bytes"], (size, exact)


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
