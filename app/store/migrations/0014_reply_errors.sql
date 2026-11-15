-- rich replies to the callback buttons of a post (Bot API 10.4, ephemeral: only the person who pressed sees it) that
-- did not reach Telegram or that Telegram refused, by callback_data: {"<callback_data>": {"n": how many, "error": the
-- last one, "at": unix time of it}}. Shown next to the clicks (services/callbacks.py); a reply is never sent again
alter table posts add column reply_errors text;
