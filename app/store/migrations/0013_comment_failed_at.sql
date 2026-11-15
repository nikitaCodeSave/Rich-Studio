-- when the first comment of a post failed: Telegram refused it, it may have gone (no answer, a stop or a crash
-- mid-request), or its forward never came. The health report shows the failure for a day from then, however long the
-- studio was off before it (store.stats.problem_posts). Rows written before the column get the end of their wait for
-- the forward (sent_at + COMMENT_WAIT, a day): the latest moment they could fail, the window the report used for them
alter table posts add column comment_failed_at integer;
update posts set comment_failed_at = coalesce(sent_at, created_at) + 86400 where comment_status = 'failed';
