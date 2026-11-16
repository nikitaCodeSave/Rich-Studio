"""The buttons whose press the studio answers from `callbacks` (services/callbacks.buttons): under the post and in its
text. An answer that no button has stays in the post with a warning, and replacing the buttons under a sent post keeps
the answers of the buttons in its text (#196)."""

import asyncio
import time

import pytest

import spec
from services import callbacks, updates

LIKE = {"inline_keyboard": [[{"text": "👍", "callback_data": "like"}]]}
DISLIKE = {"inline_keyboard": [[{"text": "👎", "callback_data": "dislike"}]]}
HTML_VOTE = {
    "html": '<p>Голосуем</p><tg-button-row><tg-button type="callback_data" data="vote">Да</tg-button></tg-button-row>'
}
BLOCKS_VOTE = {
    "blocks": [
        {"type": "paragraph", "text": "Голосуем"},
        {"type": "buttons", "buttons": [{"text": "Да", "callback_data": "vote"}]},
    ]
}


def _press(mid: int, data: str, cq: str) -> None:
    upd = {
        "update_id": 1,
        "callback_query": {
            "id": cq,
            "data": data,
            "from": {"id": 5},
            "message": {"message_id": mid, "chat": {"id": -1001}},
        },
    }
    assert asyncio.run(updates.handle_update(upd)) == "callback"


def test_the_buttons_the_studio_answers_are_under_the_post_and_in_its_text():
    html = (
        '<p>Голосуем</p><tg-button-row><tg-button data="a">Без типа</tg-button>'
        '<tg-button type="callback_data" data="b"><b>Жирная</b> &amp; кнопка</tg-button>'
        '<TG-BUTTON DATA="c">Заглавными</TG-BUTTON>'
        '<tg-button url="https://example.com" data="link">Ссылка</tg-button>'
        '<tg-button type="copy_text" text="x" data="copy">Копия</tg-button></tg-button-row>'
    )
    assert callbacks.buttons({"rich_message": {"html": html}}) == {
        "a": "Без типа",
        "b": "Жирная & кнопка",
        "c": "Заглавными",
    }
    # raw HTML in markdown is HTML, as in the preview
    md = {"markdown": 'Голосуем\n\n<tg-button data="m">Да</tg-button>'}
    assert callbacks.buttons({"rich_message": md}) == {"m": "Да"}
    # any button of the blocks, by the field's name: a «Кнопки» block, and one inside another block
    nested = {
        "type": "details",
        "summary": "Ещё",
        "blocks": [
            {"type": "buttons", "buttons": [{"text": [{"type": "bold", "text": "Ж"}], "callback_data": "deep"}]}
        ],
    }
    blocks = {"blocks": [*BLOCKS_VOTE["blocks"], nested]}
    assert callbacks.buttons({"rich_message": blocks}) == {"vote": "Да", "deep": "deep"}
    # the keyboard under the post names a shared key first, as the editor's button card does
    both = {"rich_message": HTML_VOTE, "reply_markup": {"inline_keyboard": [[{"text": "За", "callback_data": "vote"}]]}}
    assert callbacks.buttons(both) == {"vote": "За"}
    # other messages: a rich reply, the first comment and the poll after the post; their presses get no answer
    elsewhere = {
        "rich_message": {"html": "<p>Пост</p>"},
        "comment": {"html": '<tg-button data="c1">В комментарии</tg-button>'},
        "callbacks": {"x": {"reply": {"rich_message": {"html": "<p>Ответ</p>"}, "reply_markup": LIKE}}},
        "poll_after": {"question": "?", "options": ["a", "b"], "reply_markup": DISLIKE},
    }
    assert callbacks.buttons(elsewhere) == {}
    # whatever the post holds, the search does not break
    for odd in ({"rich_message": "html"}, {"rich_message": {"blocks": "x", "html": 5}}, {"reply_markup": [1]}, {}):
        assert callbacks.buttons(odd) == {}


@pytest.mark.parametrize("rm", [HTML_VOTE, BLOCKS_VOTE], ids=["html", "blocks"])
def test_an_answer_of_a_button_in_the_text_is_named_by_it(client, rm):
    bad = {"vote": {"reply": {"rich_message": {"html": ""}}}}
    r = client.post("/api/validate", json={"rich_message": rm, "callbacks": bad}).json()
    assert r["ok"] is False and r["errors"][0].startswith("ответ на кнопку «Да»:")
    good = client.post("/api/validate", json={"rich_message": rm, "callbacks": {"vote": "Спасибо"}}).json()
    assert good["ok"] is True and not [w for w in good["warnings"] if "ответ" in w], good["warnings"]


def test_an_answer_without_a_button_stays_with_a_warning(client):
    post = {"rich_message": HTML_VOTE, "callbacks": {"vote": "Спасибо", "ghost": "Никто не нажмёт"}}
    r = client.post("/api/validate", json=post).json()
    assert r["ok"] is True
    [w] = [w for w in r["warnings"] if "ответ без кнопки" in w]
    assert w.startswith("ответ на кнопку «ghost»: ответ без кнопки"), w
    d = client.post("/api/drafts", json={"title": "Ответ без кнопки", "post": post}).json()
    assert client.get(f"/api/drafts/{d['id']}").json()["payload"]["callbacks"] == post["callbacks"]


@pytest.mark.parametrize("rm", [HTML_VOTE, BLOCKS_VOTE], ids=["html", "blocks"])
def test_buttons_replaced_keep_the_answers_of_the_buttons_in_the_text(client, tg, rm):
    post = {"rich_message": rm, "reply_markup": LIKE, "callbacks": {"vote": "Голос учтён", "like": "Спасибо"}}
    sent = client.post("/api/send", json={"post": post}).json()
    pid, mid = sent["post"]["id"], sent["message"]["message_id"]
    # new buttons under the post with their answers: the old button's answer goes, the text's stays
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": DISLIKE, "callbacks": {"dislike": "Жаль"}})
    assert r.status_code == 200, r.text
    assert r.json()["payload"]["callbacks"] == {"vote": "Голос учтён", "dislike": "Жаль"}
    _press(mid, "vote", "cq-vote")
    assert tg.last("answerCallbackQuery")["body"] == {"callback_query_id": "cq-vote", "text": "Голос учтён"}
    # no buttons under the post at all: the button in the text still answers
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": None, "callbacks": {}})
    assert r.json()["payload"]["callbacks"] == {"vote": "Голос учтён"}
    # an answer given for the button in the text is the new one
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": None, "callbacks": {"vote": "Новый ответ"}})
    assert r.json()["payload"]["callbacks"] == {"vote": "Новый ответ"}
    # without callbacks the answers stay as they were
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": LIKE})
    assert r.json()["payload"]["callbacks"] == {"vote": "Новый ответ"}


def test_buttons_replaced_keep_every_answer_but_those_of_the_keyboard_before(client, tg):
    # answers the search in the text does not find: one without a button, a <tg-button> with the synonym
    # callback_data="…" render.js reads, a data with ">" in it; only the old keyboard's answer is replaced
    html = '<p>Голосуем</p><tg-button callback_data="syn">Да</tg-button><tg-button data="a>b">Б</tg-button>'
    answers = {"like": "Спасибо", "syn": "Синоним", "a>b": "Скобка", "ghost": "Без кнопки"}
    post = {"rich_message": {"html": html}, "reply_markup": LIKE, "callbacks": answers}
    sent = client.post("/api/send", json={"post": post})
    assert sent.status_code == 200, sent.text
    pid = sent.json()["post"]["id"]
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": DISLIKE, "callbacks": {"dislike": "Жаль"}})
    assert r.status_code == 200, r.text
    want = {"syn": "Синоним", "a>b": "Скобка", "ghost": "Без кнопки", "dislike": "Жаль"}
    assert r.json()["payload"]["callbacks"] == want


def test_buttons_replaced_check_their_answers_against_the_text_of_the_post(client, tg):
    sent = client.post("/api/send", json={"post": {"rich_message": HTML_VOTE, "callbacks": {"vote": "Голос учтён"}}})
    pid = sent.json()["post"]["id"]
    bad = {"vote": {"reply": {"rich_message": {"html": ""}}}}
    r = client.post(f"/api/posts/{pid}/buttons", json={"reply_markup": LIKE, "callbacks": bad})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["errors"][0].startswith("ответ на кнопку «Да»:"), detail
    assert not [w for w in detail["warnings"] if "ответ без кнопки" in w], detail


HOSTILE = {
    "<a without >": lambda n: "<a" * n,
    "a button around it": lambda n: '<tg-button data="x">' + "<a" * n + "</tg-button>",
    "buttons with one end": lambda n: '<tg-button data="x">' * (n // 10) + "</tg-button>",
    "labels of unclosed tags": lambda n: ('<tg-button data="x">' + "<" * 500) * (n // 260) + "</tg-button>",
}


@pytest.mark.parametrize("make", HOSTILE.values(), ids=HOSTILE.keys())
def test_the_buttons_of_hostile_html_are_found_in_linear_time(make):
    """By how much the time grows with the input, not by a fixed limit (as test_rich_limits does): four times the
    input takes about four times as long when linear and about sixteen when quadratic, on any machine. The check
    runs in the event loop of the studio, and a request body has no limit."""
    spec.current.load_initial()

    def seconds(n: int) -> float:
        src = make(n)
        started = time.perf_counter()
        for key in ("html", "markdown"):
            callbacks.text_buttons({key: src})
        spec.chats.warnings(spec.current, {"rich_message": {"html": src}}, [])
        return time.perf_counter() - started

    small = min(seconds(12500) for _ in range(2))
    big = min(seconds(50000) for _ in range(2))
    assert big < 8 * small + 0.2, (small, big)
