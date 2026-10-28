-- named invite links the studio made, with who came through each: Telegram tells a bot about a join only in an
-- update (chat_member, chat_join_request), it never counts them for it
create table if not exists invite_links(
  invite_link text primary key, chat_id text not null, name text, kind text not null default 'invite',
  expire_date integer, member_limit integer, creates_join_request integer not null default 0,
  subscription_period integer, subscription_price integer, is_revoked integer not null default 0,
  joined integer not null default 0, requests integer not null default 0, last_join_at integer,
  actor text, created_at integer not null);
create index if not exists invite_links_by_chat on invite_links(chat_id, created_at);
