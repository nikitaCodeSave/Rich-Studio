"""Post templates: a post with {{key}} fields that fills into a new draft. A value is always text, never markup."""

import time
from html.parser import HTMLParser

import pytest

import store

TRICKY = "<b>жирный</b> & \"кавычки\" 'апостроф'"

HTML_TEMPLATE = {
    "title": "Анонс эфира",
    "post": {"rich_message": {"html": '<p>Гость: {{guest}}</p><p><a href="{{link}}" title="{{guest}}">Эфир</a></p>'}},
    "fields": [
        {"key": "guest", "label": "Гость"},
        {"key": "link", "label": "Ссылка на эфир", "type": "url"},
    ],
}

BLOCKS_TEMPLATE = {
    "title": "Дайджест",
    "post": {
        "rich_message": {
            "blocks": [
                {"type": "heading", "size": 1, "text": "Дайджест: {{topic}}"},
                {"type": "paragraph", "text": ["Главное: ", {"type": "bold", "text": "{{lead}}"}]},
            ]
        },
        "reply_markup": {"inline_keyboard": [[{"text": "Читать", "url": "{{link}}"}]]},
    },
    "fields": [
        {"key": "topic", "label": "Тема"},
        {"key": "lead", "label": "Главное", "type": "multiline"},
        {"key": "link", "label": "Ссылка", "type": "url", "default": "https://example.com/digest"},
    ],
}


def make(client, body) -> dict:
    r = client.post("/api/templates", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def fill(client, t, **values):
    return client.post(f"/api/templates/{t['id']}/draft", json={"version": t["version"], "values": values})


def html_post(src: str, *fields: dict) -> dict:
    return {"title": "t", "post": {"rich_message": {"html": src}}, "fields": list(fields)}


class Parsed(HTMLParser):
    """What a browser (or Telegram) makes of the source: the tags and the text the person reads."""

    def __init__(self, src: str):
        super().__init__(convert_charrefs=True)
        self.tags: list[str] = []
        self.attrs: dict[str, dict] = {}
        self.text: list[str] = []
        self.feed(src)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs[tag] = dict(attrs)

    def handle_data(self, data):
        self.text.append(data)


def test_html_values_stay_text_and_quoted_attributes_stay_closed(client):
    t = make(client, HTML_TEMPLATE)
    assert t["version"] == 1 and [f["key"] for f in t["fields"]] == ["guest", "link"]
    r = fill(client, t, guest=TRICKY, link="https://example.com/live?a=1&b=2")
    assert r.status_code == 200, r.text
    src = store.get_draft(r.json()["id"])["payload"]["rich_message"]["html"]
    seen = Parsed(src)
    assert seen.tags == ["p", "p", "a"]  # no <b> came from the value
    assert seen.text == ["Гость: " + TRICKY, "Эфир"]
    assert seen.attrs["a"] == {"href": "https://example.com/live?a=1&b=2", "title": TRICKY}


def test_blocks_values_are_plain_strings_and_the_draft_has_no_channels(client):
    t = make(client, BLOCKS_TEMPLATE)
    r = fill(client, t, topic=TRICKY, lead="строка 1\nстрока 2")
    assert r.status_code == 200, r.text
    d = store.get_draft(r.json()["id"])
    blocks = d["payload"]["rich_message"]["blocks"]
    assert blocks[0] == {"type": "heading", "size": 1, "text": "Дайджест: " + TRICKY}
    assert blocks[1]["text"] == ["Главное: ", {"type": "bold", "text": "строка 1\nстрока 2"}]
    # a field left out takes its default
    assert d["payload"]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://example.com/digest"
    assert d["title"] == "Дайджест" and d["chat_ids"] is None and d["post_id"] is None
    # the template itself is not touched by filling
    assert client.get(f"/api/templates/{t['id']}").json()["payload"] == t["payload"]


def test_the_draft_can_have_its_own_title_and_the_rest_of_the_post_stays_as_it_was(client):
    body = {
        **BLOCKS_TEMPLATE,
        "post": {**BLOCKS_TEMPLATE["post"], "options": {"disable_notification": True, "message_effect_id": "1"}},
    }
    t = make(client, body)
    r = client.post(
        f"/api/templates/{t['id']}/draft",
        json={"version": t["version"], "values": {"topic": "ИИ", "lead": "Новости"}, "title": "Дайджест №7"},
    )
    d = store.get_draft(r.json()["id"])
    assert d["title"] == "Дайджест №7"
    assert d["payload"]["options"] == {"disable_notification": True, "message_effect_id": "1"}


@pytest.mark.parametrize(
    "body, why",
    [
        (html_post("<{{tag}}>x</{{tag}}>", {"key": "tag"}), "внутри тега"),
        (html_post("<p>x</{{tag}}>", {"key": "tag"}), "внутри тега"),
        (html_post('<p {{attr}}="1">x</p>', {"key": "attr"}), "внутри тега"),
        (html_post("<p dir={{dir}}>x</p>", {"key": "dir"}), "внутри тега"),
        (html_post("<a href={{link}}>x</a>", {"key": "link", "type": "url"}), "внутри тега"),
        (html_post("<!-- {{note}} --><p>x</p>", {"key": "note"}), "внутри тега"),
        # a quote that does not follow "=" opens no value: the variable would add attributes (href=…) to the tag
        (html_post('<a " {{attrs}}>x</a>', {"key": "attrs"}), "внутри тега"),
        (html_post('<p dir=ltr "{{attrs}}">x</p>', {"key": "attrs"}), "внутри тега"),
        (html_post('<p title="ok" {{attrs}}>x</p>', {"key": "attrs"}), "внутри тега"),
        (html_post('<a href="{{link}}">x</a>', {"key": "link"}), "href"),
        (html_post('<a href="https://example.com/{{slug}}">x</a>', {"key": "slug", "type": "url"}), "href"),
        (html_post('<img src="{{pic}}">', {"key": "pic"}), "src"),
        (html_post('<p onclick="{{js}}">x</p>', {"key": "js"}), "onclick"),
        (
            {
                "title": "t",
                "post": {"rich_message": {"blocks": [{"type": "paragraph", "text": "x"}]}, "options": {"{{k}}": 1}},
                "fields": [{"key": "k"}],
            },
            "ключ",
        ),
        (
            {"title": "t", "post": {"rich_message": {"markdown": "**{{name}}**"}}, "fields": [{"key": "name"}]},
            "Markdown",
        ),
        (html_post("<p>{{nobody}}</p>"), "nobody"),
        (html_post("<p>x</p>", {"key": "unused"}), "unused"),
        (html_post("<p>{{a}}</p>", {"key": "a"}, {"key": "a"}), "дважды"),
        (html_post("<p>{{a}}</p>", {"key": "1a"}), "латинская"),
        (
            {
                "title": "t",
                "post": {
                    "rich_message": {
                        "blocks": [{"type": "photo", "photo": {"type": "photo", "media": "upload:{{name}}.png"}}]
                    }
                },
                "fields": [{"key": "name"}],
            },
            "upload:",
        ),
        (
            {
                "title": "t",
                "post": {
                    "rich_message": {
                        "html": '<p><img src="tg://photo?id=p1"></p>',
                        "media": [
                            {
                                "type": "photo",
                                "id": "p1",
                                "media": "upload:a.png",
                                "caption": "{{c}}",
                                "parse_mode": "HTML",
                            }
                        ],
                    }
                },
                "fields": [{"key": "c"}],
            },
            "parse_mode",
        ),
        ({**html_post("<p>{{a}}</p>", {"key": "a"}), "title": "  "}, "назван"),
    ],
)
def test_a_template_where_a_value_could_make_markup_is_refused(client, body, why):
    r = client.post("/api/templates", json=body)
    assert r.status_code == 422, r.text
    assert why in r.json()["detail"], r.json()
    assert client.get("/api/templates").json() == []


@pytest.mark.parametrize(
    "src",
    [
        # HTML5 skips only space, \t, \n, \r, \f before a value: after another space the value is unquoted, and
        # «z href=https://evil.example onclick=alert(1)» would add attributes
        '<span title=\v"{{x}}">x</span>',
        '<span title=\u00a0"{{x}}">x</span>',
        '<span title=\u3000"{{x}}">x</span>',
        # "=" without an attribute name and a quote in a tag name: a browser reads no quoted value there, so the second
        # tag gets the variable among its attributes
        '<a ="x>T<a title=">" {{x}}>link</a>',
        '<i=" x>T<b title=">" {{x}}>y</b>',
        '<p title="a"dir="{{x}}">x</p>',
        '<p title=a"{{x}}">x</p>',
        "<p/title='{{x}}'>x</p>",
        '<p title="{{x}}">x</p><p',
        '<p title="{{x}}>x</p>',
        "<!-->{{x}}<p>x</p>",
        "<!-- a --!>{{x}}",
        '<xmp><p title="</xmp><b {{x}}>">y</p>',
        "<textarea>{{x}}</textarea>",
        "<![CDATA[{{x}}]]>",
        "<p>a < {{x}}</p>",
        "<p>a <{{x}}</p>",
        "<p>x</p {{x}}>",
    ],
)
def test_html_a_browser_reads_otherwise_is_refused(client, src):
    r = client.post("/api/templates", json=html_post(src, {"key": "x"}))
    assert r.status_code == 422, (src, r.text)
    assert client.get("/api/templates").json() == []


def test_quoted_attributes_and_whole_link_fields_are_allowed(client):
    t = make(
        client,
        html_post(
            '<p title = \'{{a}}\' dir="ltr">{{a}} &amp; <code>{{b}}</code></p><a href="{{u}}">ссылка</a>',
            {"key": "a"},
            {"key": "b", "type": "multiline"},
            {"key": "u", "type": "url"},
        ),
    )
    for hostile in ("'\"", '"><b>x</b>', "' onclick='x", "</p><i>", "&lt;b&gt;", "a=b c"):
        r = fill(client, t, a=hostile, b="x < y", u="tg://resolve?domain=durov")
        seen = Parsed(store.get_draft(r.json()["id"])["payload"]["rich_message"]["html"])
        assert seen.tags == ["p", "code", "a"], hostile
        assert seen.attrs["p"] == {"title": hostile, "dir": "ltr"}, hostile
        assert seen.attrs["a"] == {"href": "tg://resolve?domain=durov"}
        assert "".join(seen.text) == hostile + " & x < yссылка"


@pytest.mark.parametrize(
    "src",
    [
        '<p title\n=\r\n"{{x}}">x</p>',
        "<p\ttitle='{{x}}'/>",
        "<!---->{{x}}<!-- <b> -- > --><br/>",
        '<a href="#top" title="{{x}}">x</a> &amp; {{x}} > 1',
    ],
)
def test_html_written_as_browsers_read_it_is_accepted(client, src):
    assert client.post("/api/templates", json=html_post(src, {"key": "x"})).status_code == 200, src


@pytest.mark.parametrize(
    "attr",
    ["href", "src", "srcset", "xlink:href", "cite", "data", "action", "formaction", "poster", "background", "ping"],
)
def test_every_attribute_that_holds_a_link_takes_only_a_whole_link_field(client, attr):
    """A text field would put javascript: or any other scheme there as it is; a url field is checked like safeUrl."""
    r = client.post("/api/templates", json=html_post(f'<p {attr}="{{{{x}}}}">a</p>', {"key": "x"}))
    assert r.status_code == 422 and attr in r.json()["detail"], r.text
    r = client.post(
        "/api/templates", json=html_post(f'<p {attr}="https://e.example/{{{{x}}}}">a</p>', {"key": "x", "type": "url"})
    )
    assert r.status_code == 422, r.text
    t = make(client, html_post(f'<p {attr}="{{{{x}}}}">a</p>', {"key": "x", "type": "url", "default": "#a"}))
    assert fill(client, t, x="javascript:alert(1)").status_code == 422
    assert fill(client, t, x="https://example.com/a.png").status_code == 200


def text_link(url: str, text: str = "здесь") -> dict:
    """A blocks post with a link in its text (RichTextUrl)."""
    return {
        "rich_message": {
            "blocks": [{"type": "paragraph", "text": ["Читать ", {"type": "url", "text": text, "url": url}]}]
        }
    }


def button_link(url: str, web_app: bool = False) -> dict:
    button = {"text": "Читать", **({"web_app": {"url": url}} if web_app else {"url": url})}
    return {
        "rich_message": {"blocks": [{"type": "paragraph", "text": "x"}]},
        "reply_markup": {"inline_keyboard": [[button]]},
    }


@pytest.mark.parametrize(
    "post, fields",
    [
        (text_link("https://site/{{slug}}"), [{"key": "slug"}]),
        (text_link("https://site/{{slug}}"), [{"key": "slug", "type": "url"}]),
        (text_link("{{slug}}"), [{"key": "slug"}]),
        (text_link("{{slug}}"), [{"key": "slug", "type": "multiline"}]),
        (button_link("https://site/{{slug}}"), [{"key": "slug"}]),
        (button_link("{{link}}"), [{"key": "link"}]),
        (button_link("{{a}}{{b}}"), [{"key": "a", "type": "url"}, {"key": "b", "type": "url"}]),
        (button_link("https://{{host}}/app", web_app=True), [{"key": "host"}]),
    ],
)
def test_a_json_link_takes_only_a_whole_link_field(client, post, fields):
    """As href in HTML (ADR 0016): a link glued from pieces or a text field there would skip the scheme check."""
    r = client.post("/api/templates", json={"title": "t", "post": post, "fields": fields})
    assert r.status_code == 422, r.text
    assert "url" in r.json()["detail"] and "целиком" in r.json()["detail"], r.json()
    assert client.get("/api/templates").json() == []


def test_a_whole_link_field_in_a_json_link_is_checked_when_filled(client):
    t = make(
        client,
        {
            "title": "t",
            "post": text_link("{{link}}", "{{label}}"),
            "fields": [{"key": "link", "type": "url"}, {"key": "label"}],
        },
    )
    assert fill(client, t, link="javascript:alert(1)", label="x").status_code == 422
    r = fill(client, t, link="https://site/a", label='"Читать"')
    assert r.status_code == 200, r.text
    text = store.get_draft(r.json()["id"])["payload"]["rich_message"]["blocks"][0]["text"]
    assert text[1] == {"type": "url", "text": '"Читать"', "url": "https://site/a"}


def test_a_file_never_goes_into_a_json_link(client):
    """As in an HTML link attribute: telegram/uploads makes a file of upload: only in a media field, under url it
    would go to Telegram as the text upload:a.png."""
    link = {"key": "u", "type": "url", "default": "upload:a.png"}
    r = client.post("/api/templates", json={"title": "t", "post": button_link("{{u}}"), "fields": [link]})
    assert r.status_code == 422 and "upload:" in r.json()["detail"] and "JSON" in r.json()["detail"], r.text
    t = make(client, {"title": "t", "post": button_link("{{u}}"), "fields": [{**link, "default": "https://site/a"}]})
    r = fill(client, t, u="upload:a.png")
    assert r.status_code == 422 and "upload:" in r.text and "JSON" in r.text, r.text
    # one field in a media field and in a link: the file would reach the link too
    photo = {"type": "photo", "photo": {"type": "photo", "media": "{{u}}"}}
    both = {**button_link("{{u}}"), "rich_message": {"blocks": [photo]}}
    t = make(client, {"title": "t2", "post": both, "fields": [{**link, "default": "https://site/a.png"}]})
    assert fill(client, t, u="upload:a.png").status_code == 422
    assert store.list_drafts() == []


def test_the_keys_of_callbacks_are_button_data_not_fields(client):
    """callbacks[callback_data] is the answer to a button: a string answer to the button "url" is text, not a link, and
    to the button "html" is not HTML. A url inside an answer object (answerCallbackQuery.url) is a link."""
    buttons = [[{"text": "a", "callback_data": "url"}, {"text": "b", "callback_data": "html"}]]
    post = {
        "rich_message": {"blocks": [{"type": "paragraph", "text": "x"}]},
        "reply_markup": {"inline_keyboard": buttons},
        "callbacks": {"url": "Спасибо, {{name}}", "html": "Ещё раз, {{name}}"},
    }
    t = make(client, {"title": "t", "post": post, "fields": [{"key": "name"}]})
    r = fill(client, t, name="<b>Аня</b>")
    assert r.status_code == 200, r.text
    answers = store.get_draft(r.json()["id"])["payload"]["callbacks"]
    assert answers == {"url": "Спасибо, <b>Аня</b>", "html": "Ещё раз, <b>Аня</b>"}
    post["callbacks"] = {"url": {"text": "{{name}}", "url": "https://t.me/bot?start={{name}}"}}
    r = client.post("/api/templates", json={"title": "t", "post": post, "fields": [{"key": "name"}]})
    assert r.status_code == 422 and "целиком" in r.json()["detail"], r.text


def answered(button: dict, reply_button: dict | None = None) -> dict:
    """A post with `button` under it and the answer to the press "vote" (with `reply_button` in its rich reply)."""
    answer: dict = {"text": "Спасибо"}
    if reply_button:
        answer["reply"] = {
            "rich_message": {"html": "<p>Голос учтён</p>"},
            "reply_markup": {"inline_keyboard": [[reply_button]]},
        }
    return {
        "rich_message": {"blocks": [{"type": "paragraph", "text": "Голосуем"}]},
        "reply_markup": {"inline_keyboard": [[button]]},
        "callbacks": {"vote": answer},
    }


@pytest.mark.parametrize(
    "post, default",
    [
        (answered({"text": "Да", "callback_data": "{{d}}"}), "vote"),
        (answered({"text": "Да", "callback_data": "vo{{d}}"}), "te"),
        (
            {
                "rich_message": {
                    "blocks": [{"type": "buttons", "buttons": [{"text": "Да", "callback_data": "{{d}}"}]}]
                },
                "callbacks": {"vote": "Спасибо"},
            },
            "vote",
        ),
    ],
)
def test_a_button_with_an_answer_keeps_its_callback_data(client, post, default):
    """The answer to a press is found by the button's callback_data among the keys of callbacks, and a key of JSON is
    never a field: filled with another value, the button would lose its answer. A post button (reply_markup or a
    block of buttons) whose callback_data with the defaults put in has an answer is refused."""
    r = client.post("/api/templates", json={"title": "t", "post": post, "fields": [{"key": "d", "default": default}]})
    assert r.status_code == 422, r.text
    assert "callback_data" in r.json()["detail"] and "callbacks" in r.json()["detail"], r.json()
    assert client.get("/api/templates").json() == []


def test_a_button_without_an_answer_and_a_button_inside_an_answer_take_a_field(client):
    """No answer to lose: a button without a key in callbacks, and a button of the rich reply (the studio does not
    answer presses on it, services/callbacks.py)."""
    for post, default in (
        (answered({"text": "Да", "callback_data": "{{d}}"}), "other"),
        (answered({"text": "Да", "callback_data": "vote"}, {"text": "Ещё", "callback_data": "{{d}}"}), "vote"),
    ):
        make(client, {"title": "t", "post": post, "fields": [{"key": "d", "default": default}]})


def test_the_buttons_of_the_first_comment_and_of_the_poll_after_the_post_take_a_field(client):
    """A press is looked up by the channel message of the post (updates.handle_update): the first comment and the poll
    after the post are other messages, so the studio answers no press on their buttons and there is no answer to lose,
    whatever the key of callbacks."""
    vote = [{"text": "Да", "callback_data": "{{d}}"}]
    for extra in (
        {"comment": {"blocks": [{"type": "buttons", "buttons": vote}]}},
        {
            "comment": {
                "html": '<tg-button-row><tg-button type="callback_data" data="{{d}}">Да</tg-button></tg-button-row>'
            }
        },
        {"poll_after": {"question": "Как вам?", "options": ["Да", "Нет"], "reply_markup": {"inline_keyboard": [vote]}}},
    ):
        post = {**answered({"text": "Голос", "callback_data": "vote"}), **extra}
        make(client, {"title": "t", "post": post, "fields": [{"key": "d", "default": "vote"}]})


BUTTON = "<tg-button-row><tg-button {}>Да</tg-button></tg-button-row>"


@pytest.mark.parametrize(
    "attrs", ['type="callback_data" data="{{x}}"', 'data="{{x}}"', "data='{{x}}' style=\"primary\""]
)
def test_the_data_of_a_tg_button_is_its_callback_data_not_a_link(client, attrs):
    """In rich HTML data="…" of a <tg-button> is the button's callback_data (html-export.js writes it, render.js reads
    it), a plain string: a text field goes there and stays inside the attribute. data is a link only elsewhere."""
    t = make(client, html_post(BUTTON.format(attrs), {"key": "x", "default": "like"}))
    for hostile in ('"><b>x</b>', "' onclick='x", "javascript:alert(1)", "a&b"):
        r = fill(client, t, x=hostile)
        assert r.status_code == 200, r.text
        seen = Parsed(store.get_draft(r.json()["id"])["payload"]["rich_message"]["html"])
        assert seen.tags == ["tg-button-row", "tg-button"] and seen.attrs["tg-button"]["data"] == hostile, hostile
    r = client.post("/api/templates", json=html_post('<object data="{{x}}"></object>', {"key": "x"}))
    assert r.status_code == 422 and "data" in r.json()["detail"], r.text


@pytest.mark.parametrize(
    "attrs, default, answers",
    [
        ('type="callback_data" data="{{d}}"', "vote", {"vote": "Спасибо"}),
        ('data="{{d}}"', "vote", {"vote": "Спасибо"}),
        # the synonym render.js also reads: whether Telegram takes it is not checked, so it is guarded like data
        ('callback_data="{{d}}"', "vote", {"vote": "Спасибо"}),
        ('type="callback_data" data="vo{{d}}"', "te", {"vote": "Спасибо"}),
        # Telegram decodes the entities of an attribute, and the value goes in escaped
        ('data="a&amp;{{d}}"', "<b>", {"a&<b>": "Спасибо"}),
    ],
)
def test_a_tg_button_with_an_answer_keeps_its_data(client, attrs, default, answers):
    """The rule of a JSON callback_data (#188) holds for <tg-button data> of the post: its value with the defaults put
    in is a key of callbacks, so the button would lose its answer once filled."""
    post = {"rich_message": {"html": BUTTON.format(attrs)}, "callbacks": answers}
    r = client.post("/api/templates", json={"title": "t", "post": post, "fields": [{"key": "d", "default": default}]})
    assert r.status_code == 422, r.text
    assert "callback_data" in r.json()["detail"] and "callbacks" in r.json()["detail"], r.json()
    assert client.get("/api/templates").json() == []


def test_a_tg_button_without_an_answer_takes_a_field_in_data(client):
    """No answer to lose: data with another value, data of a button whose action is not callback_data (a link, a text
    to copy), and a button in the rich reply to a press."""
    reply = {"text": "Спасибо", "reply": {"rich_message": {"html": BUTTON.format('data="{{d}}"')}}}
    for attrs, default, answers in (
        ('type="callback_data" data="{{d}}"', "other", {"vote": "Спасибо"}),
        ('type="url" url="https://e.example/" data="{{d}}"', "vote", {"vote": "Спасибо"}),
        ('url="https://e.example/" data="{{d}}"', "vote", {"vote": "Спасибо"}),
        ('type="copy_text" text="x" data="{{d}}"', "vote", {"vote": "Спасибо"}),
        ('data="vote"', "vote", {"vote": reply}),
    ):
        post = {"rich_message": {"html": BUTTON.format(attrs)}, "callbacks": answers}
        make(client, {"title": "t", "post": post, "fields": [{"key": "d", "default": default}]})


def test_a_link_field_takes_an_anchor_or_a_path_and_a_file_only_where_it_is_sent(client):
    """As the preview's safeUrl: #anchor and /path are links too. upload:name works only in the post's JSON (media):
    in an HTML attribute it would stay upload:name, the HTML way to a file is tg://photo?id= and rich_message.media."""
    t = make(
        client,
        html_post('<a href="{{u}}">к разделу</a>', {"key": "u", "type": "url", "default": "#details"}),
    )
    for ok in ("#details", "/media/a.png", "https://example.com"):
        r = fill(client, t, u=ok)
        assert r.status_code == 200, (ok, r.text)
    for bad in ("upload:a.png", "//evil.example/x"):
        r = fill(client, t, u=bad)
        assert r.status_code == 422, (bad, r.text)
    body = html_post('<img src="{{u}}">', {"key": "u", "type": "url", "default": "upload:a.png"})
    r = client.post("/api/templates", json=body)
    assert r.status_code == 422 and "upload:" in r.json()["detail"]


def test_values_are_checked_before_the_draft_is_made(client):
    t = make(client, BLOCKS_TEMPLATE)
    r = fill(client, t, lead="без темы")
    assert r.status_code == 422 and "Тема" in r.text  # required, no default
    r = fill(client, t, topic="   ", lead="x")
    assert r.status_code == 422 and "Тема" in r.text
    for bad in ("javascript:alert(1)", "https://", "ftp://example.com/x", "https://exa mple.com"):
        r = fill(client, t, topic="x", lead="y", link=bad)
        assert r.status_code == 422 and "Ссылка" in r.text, bad
    r = fill(client, t, topic="x", lead="y", extra="?")
    assert r.status_code == 422 and "extra" in r.text
    assert store.list_drafts() == []
    r = client.post(
        "/api/templates",
        json={
            **BLOCKS_TEMPLATE,
            "fields": [*BLOCKS_TEMPLATE["fields"][:2], {"key": "link", "type": "url", "default": "javascript:x"}],
        },
    )
    assert r.status_code == 422


def test_an_old_version_is_refused_for_edit_delete_and_fill(client):
    t = make(client, BLOCKS_TEMPLATE)
    r = client.put(f"/api/templates/{t['id']}", json={**BLOCKS_TEMPLATE, "title": "Дайджест недели", "version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["title"] == "Дайджест недели"
    r = client.put(f"/api/templates/{t['id']}", json={**BLOCKS_TEMPLATE, "title": "Чужая правка", "version": 1})
    assert r.status_code == 409 and r.json()["detail"]["current"]["title"] == "Дайджест недели"
    assert fill(client, t, topic="x", lead="y").status_code == 409
    assert client.delete(f"/api/templates/{t['id']}?version=1").status_code == 409
    assert store.list_drafts() == []
    assert client.get("/api/templates").json()[0]["title"] == "Дайджест недели"
    assert client.delete(f"/api/templates/{t['id']}?version=2").status_code == 200
    assert client.get("/api/templates").json() == []
    assert client.get(f"/api/templates/{t['id']}").status_code == 404
    assert client.put(f"/api/templates/{t['id']}", json={**BLOCKS_TEMPLATE, "version": 2}).status_code == 404


def test_a_result_over_the_limit_is_refused_before_it_is_built(client):
    t = make(
        client,
        {
            "title": "Много",
            "post": {"rich_message": {"blocks": [{"type": "paragraph", "text": "{{x}}" * 2000}]}},
            "fields": [{"key": "x"}],
        },
    )
    started = time.monotonic()
    r = fill(client, t, x="я" * 300_000)  # 2000 × 600 KB: 1.2 GB if the post were built first
    assert r.status_code == 413 and "МБ" in r.text
    assert time.monotonic() - started < 5
    assert store.list_drafts() == []
    big = {"title": "t", "post": {"rich_message": {"blocks": [{"type": "paragraph", "text": "я" * 600_000}]}}}
    assert client.post("/api/templates", json={**big, "fields": []}).status_code == 413


def test_a_file_a_template_needs_is_kept(client):
    for name in ("cover.png", "default.png", "lonely.png"):
        client.post("/api/media", files={"file": (name, b"\x89PNG", "image/png")})
    t = make(
        client,
        {
            "title": "С обложкой",
            "post": {
                "rich_message": {
                    "blocks": [
                        {"type": "photo", "photo": {"type": "photo", "media": "upload:cover.png"}},
                        {"type": "photo", "photo": {"type": "photo", "media": "{{pic}}"}},
                    ]
                }
            },
            "fields": [{"key": "pic", "label": "Картинка", "type": "url", "default": "upload:default.png"}],
        },
    )
    r = client.delete("/api/media/cover.png")
    assert r.status_code == 409 and r.json()["detail"]["used_by"] == ["шаблон «С обложкой»"]
    assert client.delete("/api/media/default.png").status_code == 409
    used = {f["name"]: f["used"] for f in client.get("/api/media").json()}
    assert used == {"cover.png": True, "default.png": True, "lonely.png": False}
    assert client.post("/api/media/cleanup").json()["deleted"] == ["lonely.png"]
    # a link field takes an upload as a whole value
    d = store.get_draft(fill(client, t, pic="upload:cover.png").json()["id"])
    assert d["payload"]["rich_message"]["blocks"][1]["photo"]["media"] == "upload:cover.png"


def test_an_upload_reference_is_never_glued_from_parts(client):
    t = make(
        client,
        {
            "title": "t",
            "post": {
                "rich_message": {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": "{{a}}{{b}}"}}]}
            },
            "fields": [{"key": "a"}, {"key": "b"}],
        },
    )
    r = fill(client, t, a="upload:", b="secret.png")
    assert r.status_code == 422 and "upload:" in r.text
    assert store.list_drafts() == []
