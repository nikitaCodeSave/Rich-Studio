-- Idempotency-Key: a retried send returns the first result instead of posting twice
create table if not exists idempotency(
  key text primary key, scope text not null, fingerprint text not null,
  status text not null, response text, created_at integer not null);
