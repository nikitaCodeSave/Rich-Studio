-- named access tokens with a level (read < draft < publish < admin), and editor sessions behind an HttpOnly cookie;
-- only hashes are kept: the token itself is shown once, when it is made
create table if not exists tokens(
  name text primary key, hash text not null unique, level text not null,
  created_at integer not null, last_used_at integer, revoked_at integer);
create table if not exists sessions(
  hash text primary key, token_name text, created_at integer not null, expires_at integer not null);
