-- what a published post looked like before each replacement; the draft that edits a post
create table if not exists post_versions(
  id integer primary key autoincrement, post_id text not null, payload text not null,
  replaced_at integer not null, actor text);
create index if not exists post_versions_by_post on post_versions(post_id);
alter table drafts add column post_id text;
