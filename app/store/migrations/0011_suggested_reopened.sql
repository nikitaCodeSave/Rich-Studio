-- «Решить снова» on a suggestion whose decision got no answer from Telegram: when it was reopened, so that an agent's
-- request key made from the request itself differs from the decision that got no answer
alter table suggested_posts add column reopened_at integer;
