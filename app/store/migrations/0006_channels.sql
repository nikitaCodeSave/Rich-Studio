-- channels with their titles and forum topics; the channels a draft is meant for
create table if not exists channels(
  chat_id text primary key, chat_num integer, title text, username text, type text,
  is_forum integer not null default 0, topics text, can_post integer, rights text,
  checked_at integer, created_at integer not null);
alter table drafts add column chat_ids text;
