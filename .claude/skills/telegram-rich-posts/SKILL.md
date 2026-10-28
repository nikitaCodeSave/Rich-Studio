---
name: telegram-rich-posts
description: Write, check, preview and publish Telegram channel posts with Rich Messages (sendRichMessage) using the newest Bot API; use for any Telegram post formatting or Bot API formatting question.
---

# Telegram rich posts

Use this skill when the user wants to write, format, preview, publish, schedule or edit a Telegram post (usually in their channel), or asks what formatting the Bot API supports.

Baseline knowledge below is for **Bot API 10.3 (24.08.2026)**. Telegram ships new formatting often, so step 1 is mandatory.

## 1. Check freshness first (every session)

Run one of these before relying on anything below:

```bash
# Rich Studio running locally (preferred): version, date and recorded API changes
curl -s localhost:8080/api/spec | jq '{version, release_date, source, changes: .changes[0]}'

# Without the studio: machine-readable spec regenerated from core.telegram.org on every release
curl -s https://raw.githubusercontent.com/PaulSonOfLars/telegram-bot-api-spec/main/api.json | jq '{version, release_date}'
```

- With the `rich-studio` MCP tools: `bot_api_status`, and `refresh_bot_api` to download the newest spec right now (the studio otherwise checks at startup and every 12 hours). Both report `gaps`: blocks the editor has no Russian label or preview for yet. Such blocks still send fine; tell the user the preview may show them as a JSON stub.
- If the version is newer than 10.3, read what changed before writing the post: `POST localhost:8080/api/spec/refresh` returns the diff (new types, fields, methods), and https://core.telegram.org/bots/api-changelog has the human description. Prefer the new features when they fit the user's goal and say which ones you used.
- Exact fields of any type or method, from the current spec:
  `curl -s localhost:8080/api/spec/type/InputRichBlockTable | jq` (or `jq '.types.InputRichBlockTable' api.json`).
- The HTML/Markdown tag grammar is not in api.json. It lives in https://core.telegram.org/bots/api#rich-message-formatting-options. When a tag below is marked ⚠️ or the version is newer, check that section (WebFetch may truncate the page; per-type pages at https://docs.aiogram.dev mirror the spec).

## 2. Request shape

`sendRichMessage(chat_id, rich_message, reply_markup?, disable_notification?, protect_content?, ...)`.
`rich_message` (InputRichMessage) has **exactly one** of `html`, `markdown`, `blocks`, plus optional `media[]`, `is_rtl`, `skip_entity_detection`.
Edit a published post: `editMessageText(chat_id, message_id, rich_message)`.

Which format:
- **blocks** (JSON): strict, fully described by the spec, validated field by field. Best when you generate the post.
- **html**: covers everything, easiest to hand-edit.
- **markdown**: GFM plus `||spoiler||`, `==marker==`, `$formula$` (Pandoc rules: `$5 и $10` is NOT a formula only because of the space and digit rules, check it), `$$block$$` on its own line, `[^1]` footnotes, and allowed HTML tags. Tables are always bordered and striped; `__x__` is bold, not underline; `~sub~` and `^sup^` do not exist (use `<sub>`, `<sup>`). A single line break inside a paragraph becomes a space: end the line with two spaces or `\` for a real break.

## 3. Cheat sheet (10.3)

Blocks (`type`): `paragraph{text}`, `heading{text,size 1-6}`, `pre{text,language?}`, `footer`, `divider`, `mathematical_expression{expression}` (LaTeX), `anchor{name}`, `list{items[{blocks,has_checkbox?,is_checked?,value?,type?}]}`, `blockquote{blocks,credit?}`, `expandable_blockquote{text,credit?}`, `pullquote{text,credit?}`, `collage{blocks,caption?}`, `slideshow{blocks,caption?}`, `table{cells[[{text?,is_header?,colspan?,rowspan?,align,valign}]],is_bordered?,is_striped?,is_compact?,caption?}`, `details{summary,blocks,is_open?}`, `map{location{latitude,longitude},zoom?,width?,height?,caption?}`, `buttons{buttons[1-8],align?}`, `photo|video|animation|audio|document|voice_note{<kind>: InputMedia*, caption?}`, `thinking` (drafts in private chats only).
Captions are `{text, credit?}`. Table cell `align` (left/center/right) and `valign` (top/middle/bottom) are required.

List item = blocks, not text: `{"type":"list","items":[{"blocks":[{"type":"paragraph","text":"Пункт"}]}]}`.

Inline RichText: a string, an array, or `{type, text, ...}` with type `bold, italic, underline, strikethrough, spoiler, marked, code, subscript, superscript, url{url}, text_mention{user}, email_address, phone_number, bank_card_number, mention{username}, hashtag, cashtag, bot_command, date_time{unix_time,date_time_format}, custom_emoji{custom_emoji_id,alternative_text}, mathematical_expression{expression}, anchor{name}, anchor_link{anchor_name}, reference{name}, reference_link{reference_name}, button{button}`.

HTML tags: `p h1-h6 b i u s sub sup mark code pre>code.language-x a[href] hr footer blockquote>cite aside(pullquote) ul ol li input[type=checkbox] details>summary table[bordered striped compact] caption tr th td[colspan rowspan align valign] figure figcaption img video audio`, plus `tg-spoiler, tg-emoji[emoji-id], tg-math, tg-math-block, tg-collage, tg-slideshow, tg-document, tg-thinking, tg-button-row`.
Checked on the real Bot API (tests/telegram_parse/captured.json): `<blockquote expandable>` (`collapsed` gives a plain quote), `<tg-time unix format>text</tg-time>` (without text it is dropped), `<tg-map lat long zoom>`, `<tg-button-row align>` with `<tg-button type=url|copy_text|callback_data ...>`, footnote `<a href="#n1">` + `<tg-reference name="n1">` (without the reference it is a plain link), `<a href="#name">` to `<a name="name">`, `<a href="#">` = top of the post. `<span class="tg-spoiler">` is NOT a spoiler. A `<table>` without attributes has no borders; headers are centred by default.

The studio preview follows these rules. `send_preview_to_me` (and `preview-send`) answers with `parsed.input_blocks`: the post as Telegram parsed it, in `rich_message.blocks` form. Read it to confirm a formula, spoiler or table came out as meant; it can also be sent or saved as a visual draft. The editor shows the same check above its preview.

Media: put `tg://photo?id=X` (video/audio/document/animation/voice_note) in html/markdown and add `{"id":"X","media":{"type":"photo","media":"<URL|file_id|attach://name>"}}` to `media`. In blocks the media object sits in the block itself. Captions on InputMedia are ignored.

Buttons: `RichMessageButton{text, style?: primary|success|danger|link, exactly one of url|callback_data|login_url|switch_inline_query|switch_inline_query_chosen_chat|copy_text{text}|disabled{}}`. Keyboard under the post: normal `reply_markup.inline_keyboard`; a button `url` needs a host: Telegram rejects the whole post for `https://` alone, and validation reports it as an error.

Channels: no ephemeral messages, no `message_effect_id`, no `thinking`/drafts, no `web_app` buttons (use a t.me link), `callback_data` needs a running bot. Bot API has no scheduling: use the studio scheduler.

Limits: 32 768 characters (client folds after ~8 000), 1-8 buttons per row, callback_data 1-64 bytes, map zoom 0-24. Unofficial: 50 media, 500 blocks, nesting 16.

## 4. Workflow with Rich Studio (http://localhost:8080, API docs at /docs)

If the `rich-studio` MCP tools are available (`save_draft`, `preview_post`, `publish_post`…), use them instead of curl: same steps and rules. Call `list_channels` first and name the channel by its title when you ask the user to confirm; a channel not listed yet (or never checked) goes through `check_channel` before the first publication, which also says whether the bot may post there; pass `chat_ids` to `save_draft` so the draft (and the editor) knows where it goes, and `publish_post` without `chat_id` sends it there. `goes_to` in `save_draft`/`list_drafts`/`get_draft` names where it goes (`default: true` means the default channel); show it to the user by title and id. A forum topic is `options.message_thread_id` (topic ids in `list_channels`); it works only with one forum channel, so with several channels or a known non-forum one the send is refused. Pass the post JSON once: `save_draft` returns `id`, `version`, `chat_ids` and `validation` (fix every error and save again with `draft_id` and `version`). Then `preview_post(draft_id)` so the user sees the post rendered as in Telegram right in the chat; it returns the `version` it showed. After the user confirms, `publish_post(draft_id, version, idempotency_key)` (or `schedule_post`) with that version; `dry_run` shows the request first. A draft with `post_id` is a working copy of a published post: it goes out only as `edit_post(post_id, draft_id, version)`, never as a new post. If the draft changed after the preview (the user edited it in the editor), publishing by `draft_id` fails: show it again. `validate_post` checks without saving. Give the user `editor_url` from `save_draft`/`preview_post` (a clickable link to the draft in the editor; an open editor picks up your changes by itself). Files: `upload_media(url=…)` or `upload_media(data_base64=…, filename=…)` returns the `upload:<name>` ref. Polls: `send_poll` (a separate message, after the user confirms). `describe_type` gives the exact fields of a Bot API type, `list_media` the uploaded files, `cancel_scheduled_post` cancels a scheduled one. If `publish_post`, `schedule_post`, `send_poll`, `edit_post` and the other publishing tools are missing, the studio runs with `MCP_PUBLISH=off`: tell the user publishing from the chat is off and give them `editor_url` to publish from the editor; do not work around it through the HTTP API. Without the `rich-studio` MCP tools at all, use the HTTP API below.

When the user says "continue my draft" or "fix the post": `list_drafts` → `get_draft` → `save_draft` with that `draft_id` and `version` (no new copy); for a published post `list_posts` → `get_post` (read it first) → `edit_post` with `post_version` = the `version` from `get_post`, after the user confirms. If `edit_post` says the post was already replaced, someone (the user in the editor, another agent) changed it meanwhile: `get_post` again and redo the edit on top of it, never retry blindly. Button answers and the first comment stay unless you pass new ones; `post_history` shows earlier versions. `list_posts` gives each post's first line (`title`) and t.me `link`; a send Telegram refused is listed as `failed` with its `error` (filter with `status="failed"`). If a post failed, did not go out on time or the studio behaves oddly, use the `studio-health` skill (`studio_health`, `studio_logs`). Call `studio_status` before scheduling: if updates are off, button answers and first comments will not work, and a post that misses its time by more than `schedule_late_min` becomes `missed` (`reschedule_post` brings it back).

`post` = `{"rich_message": {...}, "reply_markup": {...}?, "options": {...}?}`. Uploaded files are referenced as `upload:<name>`. If `STUDIO_API_KEY` is set, add `-H "X-API-Key: $STUDIO_API_KEY"`.

```bash
S=localhost:8080
curl -s -F file=@cover.jpg $S/api/media                                   # -> {"ref":"upload:cover.jpg"}
curl -s -X POST $S/api/media/fetch -H 'content-type: application/json' -d '{"url":"https://…/cover.jpg"}'  # by link
curl -s -X POST $S/api/validate -H 'content-type: application/json' -d @post.json          # errors / warnings / stats
curl -s -X POST "$S/api/render?theme=dark" -H 'content-type: application/json' -d @post.json > preview.html  # standalone preview page
curl -s -X POST $S/api/drafts -H 'content-type: application/json' -d '{"title":"Анонс","post":'"$(cat post.json)"'}'  # open it in the web editor
curl -s -X POST $S/api/preview-send -H 'content-type: application/json' -d '{"post":'"$(cat post.json)"'}'  # to the user's DM
curl -s "$S/api/check?chat_id=@channel"                                   # bot is admin with can_post_messages?
curl -s -X POST $S/api/send -H 'content-type: application/json' -d '{"chat_id":"@channel","post":'"$(cat post.json)"',"dry_run":true}'  # exactly what would go to Telegram, nothing sent
K=$(uuidgen)   # one key per post; reuse it when retrying the same send
curl -s -X POST $S/api/send -H 'content-type: application/json' -H "Idempotency-Key: $K" -H 'X-Studio-Client: claude-code' -d '{"chat_id":"@channel","post":'"$(cat post.json)"'}'
curl -s -X POST $S/api/schedule -H 'content-type: application/json' -H "Idempotency-Key: $K" -d '{"chat_id":"@channel","at":"2026-10-10T09:00:00+03:00","post":'"$(cat post.json)"'}'
curl -s -X POST $S/api/posts/<id>/edit -H 'content-type: application/json' -d @post.json   # replace a published post
# a draft with post_id is a working copy of that post: replace the post as above, /api/send with its draft_id answers 409; "as_new": true posts a second copy, only when the user asked for one
curl -s -X POST $S/api/send -H 'content-type: application/json' -d '{"chat_ids":["@one","@two"],"post":'"$(cat post.json)"'}'  # several channels
curl -s -X POST $S/api/posts/<id>/pin -H 'content-type: application/json' -d '{"disable_notification":true}'   # DELETE to unpin
curl -s -X POST $S/api/posts/<id>/buttons -H 'content-type: application/json' -d '{"reply_markup":{"inline_keyboard":[[...]]}}'  # buttons only
curl -s -X POST $S/api/poll -H 'content-type: application/json' -d '{"chat_id":"@channel","poll":{"question":"?","options":["A","B"],"members_only":true}}'
curl -s $S/api/posts | jq '.[0] | {reactions, clicks, buttons, poll_results, comment_status}'   # feedback on posts; buttons: callback_data → button text
curl -s -X POST $S/api/raw/<anyMethod> -H 'content-type: application/json' -d '{...}'       # any Bot API method as is; delete*/set*/other non-read/send methods need -H 'X-Confirm: yes'

Studio-only fields in `post` (never sent to Telegram): `"pin": true` pins after publishing; `"comment": InputRichMessage` is posted as the first comment under the post in the linked discussion group (the bot must be an admin there); `"callbacks": {"<callback_data>": "answer" | {"text", "show_alert"}}` answers callback buttons; `"poll_after": {sendPoll fields: question, options, type…}` sends that poll to the same chat (and forum topic) right after the post, scheduled posts included. The studio checks `poll_after` against `sendPoll` before the post goes out (at most 12 options, a question up to 300 characters, a quiz needs its correct answer), so `validate_post` / `save_draft` report those errors; a poll Telegram still refuses is only a warning and a `failed` poll row, the post stays published. Button clicks, comments, reactions and poll results need the update loop (`GET $S/api/updates` → state "ok"). Polls are separate messages: they cannot be embedded in a rich post. The Bot API has no view counts and cannot post channel stories.
```

Order: write → validate (fix every error, mention warnings) → save as draft and/or render → preview-send → publish only after the user confirms.

Without the studio, call `https://api.telegram.org/bot$TG_BOT_TOKEN/sendRichMessage` with the same JSON.

## 5. Rules

- Never publish, edit or delete a channel post without the user's explicit go-ahead for that exact action; drafts, validation, render and preview-send are fine to do on your own.
- Every `send`, `schedule` and `poll` call carries an `Idempotency-Key` (one per post). After a timeout or an error, retry with the same key: the studio returns the first result instead of posting twice. A 422 is the post refused: by the studio's check (`detail.errors`) or by Telegram (`detail` text plus `error_code` 400); fix the post, nothing went out. After a successful resend from the same draft the refused row leaves `list_posts`; `DELETE /api/posts/{id}` drops a failed row by hand. A 502 saying the post "мог уйти" stays the same on retry: check the channel with the user before using a new key.
- Never print, echo or ask the user to paste the bot token into chat; it lives in `.env` / `TG_BOT_TOKEN`.
- Send markup as is. Do not invent tags or fields: confirm anything not in the cheat sheet against the current spec or the formatting section, and say when something is unverified.
- Write post text in the user's language and voice.
