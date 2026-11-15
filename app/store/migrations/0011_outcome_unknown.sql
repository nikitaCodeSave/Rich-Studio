-- a failed send whose outcome is unknown: Telegram did not answer, or the studio stopped or crashed mid-send, so the
-- post (or the poll after it) may be in the channel all the same. A successful resend of the draft never drops such a
-- row, and the health report shows it until a person removes it (store.drop_refused, store.stats.problem_posts).
-- Rows written before the flag carried it only in the text of their error: every such text says «мог уйти». A sent
-- post whose later step may have gone (its first comment, the poll after it) is not one of them: it is in the channel
alter table posts add column outcome_unknown integer not null default 0;
update posts set outcome_unknown = 1
  where status = 'failed' and (instr(error, 'мог уйти') > 0 or instr(error, 'могло уйти') > 0);
