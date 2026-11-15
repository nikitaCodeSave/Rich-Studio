"""Rich Message Limits (https://core.telegram.org/bots/api#rich-message-limits): each limit right at the edge and one over.

Over a limit the documentation states plainly is an error (Telegram refuses the post); where it leaves the reading
open (how levels of nesting are counted, rowspan, the text of html/markdown before Telegram parses it) a warning."""

import json
import time
from pathlib import Path

import pytest

import spec
from spec.validator import source_chars, table_columns

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
    r = check(current, [para("я" * 32768)])
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]
    r = check(current, [para("я" * 32769)])
    assert not r["ok"] and has(r["errors"], "лимит 32768"), r


def test_characters_count_emoji_alternative_text_and_formula_source(current):
    """ "including custom emoji alternative text and formula source": inline and block formulas alike."""
    extras = [
        {"type": "custom_emoji", "custom_emoji_id": "5368324170671202286", "alternative_text": "👍"},
        {"type": "mathematical_expression", "expression": "x^2"},
    ]
    formula = {"type": "mathematical_expression", "expression": "E=mc^2"}
    room = 32768 - 1 - 3 - 6
    r = check(current, [para(["я" * room, *extras]), formula])
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]
    r = check(current, [para(["я" * (room + 1), *extras]), formula])
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
    r = check(current, [para("😀" * 32768)])
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]


# ---------- blocks ----------
def test_500_blocks_pass_and_501_is_an_error(current):
    r = check(current, [para()] * 500)
    assert r["ok"] and r["stats"]["blocks"] == 500, r["errors"]
    r = check(current, [para()] * 501)
    assert not r["ok"] and has(r["errors"], "лимит 500"), r


def test_list_items_count_as_blocks(current):
    """A list of 249 items with a paragraph each is 1 + 249 + 249 blocks."""
    items = [{"blocks": [para()]} for _ in range(249)]
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


# ---------- nesting ----------
def quotes(levels):
    """Blockquotes nested `levels` deep, the innermost holding a paragraph: `levels` + 1 levels of blocks."""
    block = para()
    for _ in range(levels):
        block = {"type": "blockquote", "blocks": [block]}
    return block


def test_16_blocks_inside_one_another_pass_17_warn_and_18_are_an_error(current):
    """If the top block is not a level of nesting yet, Telegram takes 17 blocks inside one another: only from 18
    is the limit surely over."""
    r = check(current, [quotes(15)])
    assert r["ok"] and not r["warnings"] and r["stats"]["depth"] == 16, r
    r = check(current, [quotes(16)])
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "rich_message.blocks[0]" + ".blocks[0]" * 16 + ": 17 уровней вложенности"), r
    r = check(current, [quotes(17)])
    assert not r["ok"], r
    (err,) = [e for e in r["errors"] if "вложенности" in e]
    assert err.startswith("rich_message.blocks[0]" + ".blocks[0]" * 17 + ": 18 блоков друг в друге"), err


def test_nested_formatting_counts_as_levels_but_only_warns(current):
    """ "levels of nested formatting and blocks": how formatting adds to blocks is not spelled out, so a warning."""

    def bold(levels):
        text = "x"
        for _ in range(levels):
            text = {"type": "bold", "text": text}
        return text

    r = check(current, [para(bold(15))])  # the paragraph and 15 levels of bold
    assert r["ok"] and not r["warnings"] and r["stats"]["depth"] == 16, r
    r = check(current, [para(bold(16))])
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "rich_message.blocks[0]: 17 уровней вложенности"), r["warnings"]


def test_list_items_and_table_rows_are_levels_of_their_own(current):
    item = {"type": "list", "items": [{"blocks": [para()]}]}  # list, item, paragraph
    assert check(current, [item])["stats"]["depth"] == 3
    table = {"type": "table", "cells": [[{**CELL, "text": {"type": "bold", "text": "x"}}]]}  # table, row, bold
    assert check(current, [table])["stats"]["depth"] == 3


def test_arrays_of_rich_text_are_not_a_level(current):
    assert check(current, [para(["a", ["b", ["c"]]])])["stats"]["depth"] == 1


# ---------- table columns ----------
def test_table_of_20_columns_passes_and_21_is_an_error_with_the_table_path(current):
    r = check(current, [para(), {"type": "table", "cells": [[CELL] * 20, [CELL] * 3]}])
    assert r["ok"] and not r["warnings"], r
    r = check(current, [para(), {"type": "table", "cells": [[CELL] * 3, [CELL] * 21]}])
    assert not r["ok"] and "rich_message.blocks[1]: колонок в таблице 21, лимит 20" in r["errors"], r["errors"]


def test_colspan_counts_columns(current):
    wide = {**CELL, "colspan": 2}
    assert check(current, [{"type": "table", "cells": [[wide] + [CELL] * 18]}])["ok"]
    r = check(current, [{"type": "table", "cells": [[wide] + [CELL] * 19]}])
    assert has(r["errors"], "колонок в таблице 21"), r


def test_huge_colspan_is_an_error_not_a_hang(current):
    r = check(current, [{"type": "table", "cells": [[{**CELL, "colspan": 10**9, "rowspan": 2}], [CELL]]}])
    assert has(r["errors"], f"колонок в таблице {10**9}")


def test_rowspan_that_widens_a_row_below_only_warns(current):
    """In HTML a cell with rowspan also takes its column in the rows below; the documentation does not say so."""
    tall = {**CELL, "rowspan": 2}
    r = check(current, [{"type": "table", "cells": [[tall] + [CELL] * 19, [CELL] * 19]}])
    assert r["ok"] and not r["warnings"], r
    r = check(current, [{"type": "table", "cells": [[tall] + [CELL] * 19, [CELL] * 20]}])
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "rich_message.blocks[0]: колонок в таблице больше 20, если ячейка с rowspan"), r


def test_rowspan_width_is_followed_only_to_the_limit(current):
    """Before, every column of every tall cell was followed: 500 rows of 100 cells with rowspan took a second."""
    tall = {**CELL, "rowspan": 10**6}
    started = time.perf_counter()
    assert table_columns([[tall] * 100] * 1000) == (100, 21)
    assert time.perf_counter() - started < 0.5
    assert table_columns([[tall] * 3, [CELL] * 2, [{**CELL, "colspan": 30}]]) == (30, 21)


# ---------- html and markdown ----------
def test_html_text_over_the_limit_is_an_error(current):
    r = check(current, html="<p>" + "я" * 32768 + "</p>")
    assert r["ok"] and r["stats"]["chars"] == 32768, r["errors"]
    r = check(current, html="<p>" + "я" * 32769 + "</p>")
    assert not r["ok"] and has(r["errors"], "лимит 32768")


def test_html_entities_are_one_character(current):
    assert check(current, html="<p>a&amp;b&#x1F600;</p>")["stats"]["chars"] == 4


def test_spaces_of_html_layout_make_only_a_warning(current):
    """Indentation between tags may not be text for Telegram: over the limit only with it, the post is not refused."""
    html = "<p>" + "я" * 30000 + "</p>" + "\n    <p>а</p>" * 600
    r = check(current, html=html)
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "лимит 32768"), r["warnings"]


def test_markdown_link_addresses_are_not_text(current):
    link = "[ссылка](https://example.com/a/very/long/address/that/is/not/text)"
    r = check(current, markdown=f"{'я' * 31000} {link * 100}")  # the source is over 37 000
    assert r["ok"] and not has(r["warnings"], "лимит 32768"), r


def test_markdown_markup_is_not_counted_for_the_error(current):
    r = check(current, markdown="я" * 32769)
    assert not r["ok"] and has(r["errors"], "лимит 32768")
    # 32760 letters of text in bold: the asterisks push the source over, but the text may well be under
    r = check(current, markdown="**" + "я" * 32760 + "**\n" + "- [x] **a**\n" * 4)
    assert r["ok"], r["errors"]
    assert has(r["warnings"], "текст около"), r["warnings"]


def test_markdown_cells_past_the_header_are_not_an_error(current):
    """Telegram cuts a table row to the width of its header: the third cell here is dropped, not sent."""
    r = check(current, markdown="| a | b |\n|---|---|\n| x | y | " + "я" * 32800 + " |")
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
