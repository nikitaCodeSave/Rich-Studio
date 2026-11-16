"""The buttons whose press the studio answers from `callbacks` (services/callbacks.buttons): under the post and in its
text. An answer that no button has stays in the post with a warning, and replacing the buttons under a sent post keeps
the answers of the buttons in its text (#196)."""

import asyncio

import pytest

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
