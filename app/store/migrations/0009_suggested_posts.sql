-- posts subscribers suggested in a channel's direct messages, as the bot saw them arrive, and what became of each
create table if not exists suggested_posts(
  id text primary key, chat_num integer not null, message_id integer not null, channel_title text,
  author text, topic_id integer, message text not null, state text not null default 'pending',
  price_amount integer, price_currency text, send_date integer, comment text, actor text,
  received_at integer not null, decided_at integer);
create index if not exists suggested_posts_by_state on suggested_posts(state, received_at);
