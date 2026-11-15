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
