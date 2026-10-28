-- a draft open in two tabs: a save based on an older version is refused instead of overwriting
alter table drafts add column version integer not null default 0;
