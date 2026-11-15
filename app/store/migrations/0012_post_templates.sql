-- post templates: a post with {{key}} fields that fills into a new draft (services/templates.py); version grows on
-- every save, so an edit, a delete or a fill based on an older one is refused
create table if not exists post_templates(
  id text primary key, title text not null, payload text not null, fields text not null,
  version integer not null default 1, created_at integer not null, updated_at integer not null);
create index if not exists post_templates_by_updated on post_templates(updated_at);
