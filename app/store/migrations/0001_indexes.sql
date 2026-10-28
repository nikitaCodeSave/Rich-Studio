-- lookups by incoming updates and by the scheduler
create index if not exists posts_by_message on posts(chat_num, message_id);
create index if not exists posts_by_poll on posts(poll_id);
create index if not exists posts_due on posts(status, scheduled_at);
