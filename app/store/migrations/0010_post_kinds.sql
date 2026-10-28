-- the kind stored with a post is what sending, edits and the list read (services/kinds.py, ADR 0013); a row whose
-- kind does not match its content (written before every kind was stored) gets the kind of its content. A rich post
-- with a stray poll/live_photo field (unknown fields are only a warning) stays a rich post
update posts set kind = 'poll'
  where coalesce(kind, 'rich') <> 'poll'
    and (case when json_valid(payload) then json_type(payload, '$.poll') end) = 'object'
    and json_type(payload, '$.rich_message') is null;
update posts set kind = 'live_photo'
  where coalesce(kind, 'rich') <> 'live_photo'
    and (case when json_valid(payload) then json_type(payload, '$.live_photo') end) = 'object'
    and json_type(payload, '$.rich_message') is null;
update posts set kind = 'paid_media'
  where coalesce(kind, 'rich') <> 'paid_media'
    and (case when json_valid(payload) then json_type(payload, '$.paid_media') end) = 'object'
    and json_type(payload, '$.rich_message') is null;
update posts set kind = 'rich' where kind is null;
