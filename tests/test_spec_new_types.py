"""What a Bot API release adds works without studio code: the check, describe_type and the send read api.json.

Checked on Bot API 10.4: the inline type RichTextTonAddress and ReplyParameters.quote_rich_texts.
"""

TON = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"


def post_with(inline) -> dict:
    return {"rich_message": {"blocks": [{"type": "paragraph", "text": ["Адрес ", inline]}]}}


def test_a_new_inline_type_is_checked_by_the_spec(client):
    r = client.post("/api/validate", json=post_with({"type": "ton_address", "text": TON, "address": TON})).json()
    assert r["ok"], r
    no_address = {"rich_message": {"blocks": [{"type": "paragraph", "text": {"type": "ton_address", "text": TON}}]}}
    r = client.post("/api/validate", json=no_address).json()
    assert r["errors"] == ["rich_message.blocks[0].text: нет обязательного поля address (RichTextTonAddress)"], r


def test_describe_type_knows_a_new_type(client):
    t = client.get("/api/spec/type/RichTextTonAddress").json()
    assert [f["name"] for f in t["fields"]] == ["type", "text", "address"]


def test_a_new_field_in_options_goes_to_telegram_as_is(client, tg):
    reply = {"message_id": 5, "quote_rich_texts": [{"type": "bold", "text": "Адрес"}, " TON"]}
    post = {**post_with({"type": "ton_address", "text": TON, "address": TON}), "options": {"reply_parameters": reply}}
    r = client.post("/api/send", json={"post": post, "chat_id": "@chan"})
    assert r.status_code == 200, r.text
    body = tg.last("sendRichMessage")["body"]
    assert body["reply_parameters"] == reply
    assert body["rich_message"] == post["rich_message"]
