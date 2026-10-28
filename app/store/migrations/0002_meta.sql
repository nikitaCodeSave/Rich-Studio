-- small key-value state that must survive a restart (getUpdates offset)
create table if not exists meta(key text primary key, value text);
