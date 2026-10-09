---
name: studio-health
description: Check whether the running Rich Studio is healthy, read its log and decide what to do; use when the user asks if the studio works, why a post did not go out, what is in the logs, or to watch the service.
---

# Rich Studio health

Use this skill when the user asks whether Rich Studio works, why a post failed or did not go out on time, what the log says, how much space it takes, or asks you to watch the service. For writing posts use `telegram-rich-posts`.

The studio watches itself: every request, every Bot API call and every background loop round is counted, a health report is computed on demand, and every change of a check (ok → warn → fail and back) is written to the log as event `health.change`. Your job is to read that, explain it to the user in their language and propose the fix. Design: `docs/OBSERVABILITY.md`.

## 1. Read the state

With the `rich-studio` MCP tools: `studio_health`, then `studio_logs` (default level WARNING). Otherwise HTTP (add `-H "X-API-Key: $STUDIO_API_KEY"` if the key is set):

```bash
S=localhost:8080
curl -sf $S/healthz || echo "studio is not running"                 # liveness, no key needed
curl -s $S/readyz                                                      # readiness: 503 + names of failing checks
curl -s $S/api/health | jq '{status, problems}'                        # the verdict: ok | warn | fail and why
curl -s $S/api/health | jq '.checks[] | select(.status!="ok")'         # each problem with `action` and details
curl -s "$S/api/logs?level=WARNING&limit=50" | jq -c '.lines[] | {ts, logger, msg, event}'
curl -s "$S/api/logs?level=INFO&q=health.change" | jq -c '.lines[] | {ts, check, status, msg}'   # timeline of incidents
curl -s "$S/api/logs?logger=rich-studio.telegram&level=WARNING" | jq -c '.lines[]'               # Bot API failures
curl -s "$S/api/logs?level=ERROR&limit=5" | jq -r '.lines[] | .request_id, .msg, .exc'            # tracebacks
curl -s "$S/api/logs?q=<request_id>" | jq -c '.lines[]'          # everything one request or loop round did
curl -s $S/api/health | jq '.metrics | {http_latency, telegram_latency}'   # p50/p95/max per endpoint and Bot API method
```

If the API does not answer but Docker is there: `docker compose ps` and `docker compose logs --tail 100 rich-studio`. The JSON log is also on disk: `./data/logs/studio.log` (rotated copies `.1`, `.2`, …).

Log lines are JSON: `ts` (UTC, ends with `Z`; convert for the user), `level`, `logger`, `msg`, plus the event fields. Events: `http` (method, path, status, ms, client), `telegram.call` / `telegram.error` (method, ms, code, chat_id), `health.change` (check, status, action), `cleanup`, `backup.daily`, `start`, `stop`. Every line logged during a request or a background round has `request_id` (also the `X-Request-ID` response header; `scheduler-…`, `spec-…`, `backup-…`, `housekeeping-…` for loop rounds, `update-<update_id>` for Telegram updates). Follow an error to everything around it with `q=<request_id>`. When you call the studio yourself, send your own `X-Request-ID` to find your calls later. The log never contains the bot token or post text.

## 2. What each check means

| Check | Looks at | When not ok |
|---|---|---|
| `database` | schema version, post counts | fail: the database is older than the code. Restart the studio (migrations run at start) |
| `disk` | free space in DATA_DIR | warn < 1 GB, fail < 200 MB: nothing new can be saved. Suggest `POST /api/media/cleanup`, old backups |
| `telegram` | token, getUpdates state, the newest Bot API call | fail `conflict`: another process or a webhook reads this bot; stop it or `TG_UPDATES=off`. warn: the newest call failed (network, rights): `GET /api/check?chat_id=…` |
| `loops` | heartbeats of scheduler, updates, spec, backup, housekeeping | fail: a loop stopped, scheduled posts will not go. Restart the studio and read ERROR lines |
| `scheduler` | scheduled posts past their time | warn: the queue is stuck or background loops are off; posts may go late or become `missed` |
| `posts` | failed, missed posts and failed first comments in the last 24 h | warn: read `error` of each; a missed post needs the user: reschedule or publish |
| `sends` | sends that ended "may have gone" in the last 24 h | warn: ask the user to look at the channel. If the post is not there, send again with a new Idempotency-Key |
| `spec` | Bot API spec version and the last download | warn: the newest spec cannot be downloaded; the editor works on the saved copy |
| `backup` | the newest daily database copy | warn: no copy for over a day or backups are off; suggest `GET /api/export` |
| `storage` | sizes of database, uploads, backups, log; number of post versions | warn: unused uploads are large (suggest cleanup or `MEDIA_UNUSED_DAYS`) or the log outgrew its cap |
| `log` | ERROR lines in the last hour (in memory, survives a broken log file) | warn: show the last error and its traceback from `/api/logs?level=ERROR` |

## 3. Decide

- `fail` anywhere: tell the user first, with the one action from the check. These stop publishing or lose data.
- `warn`: group by what the user has to do (look at the channel, reschedule, free space) and what can wait.
- `ok` with `problems: []`: say so in one line; do not dump the report.
- Look at the `health.change` timeline to say since when something is wrong and whether it already recovered.
- Counters in `metrics` (requests by 2xx/4xx/5xx, Bot API calls ok/error by method) are since the process start; compare with `uptime_s`. A `p95_ms` of a Bot API method far above its `p50_ms` means a slow or flaky network to Telegram, not a studio bug.

## 4. Rules

- Reading is always fine. Never change data on your own: deleting uploads (`POST /api/media/cleanup`), rescheduling, re-sending, restarting the container or changing `.env` only after the user says yes to that exact action.
- Never re-send a post the report marks as "may have gone" without the user checking the channel first: it may already be there.
- Never print the bot token or `.env` contents; the log has none, keep it that way.
- To watch the service over time in a local Claude Code session: `/loop 30m` with the `studio-monitor` agent; it reports only when something changed.
