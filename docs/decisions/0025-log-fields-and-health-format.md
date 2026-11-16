# 0025. Журнал и отчёт о состоянии: свои имена полей, id по W3C Trace Context, формат отчёта без полного `application/health+json`

2026-10-09, сверка наблюдаемости с практиками; записано 2026-11-16, когда `docs/OBSERVABILITY.md` сократили до текущего состояния. Дополняет [0006](0006-observability-in-process.md).

**Решение.**
- Поля строк журнала свои и короткие: `method`, `route`, `status`, `ms`, `request_id`, а не семантические соглашения OpenTelemetry (`http.request.method`, `http.route`, `http.response.status_code`, `trace_id`).
- `request_id` совместим с W3C Trace Context: студия берёт trace-id входящего `traceparent`, а свой id делает в 32 hex, как trace-id. Заголовок `traceresponse` студия не отдаёт.
- Отчёт `/api/health` — свой формат: `ok | warn | fail`, `summary`, `action`, `details`, а рядом `/healthz` и `/readyz`, как liveness и readiness в Kubernetes и Spring Actuator. Из черновика IETF `application/health+json` взяты только `version` и `time`.
- Уведомлений MCP (`notifications/message`) студия не шлёт: агент сам читает `studio_health` и `studio_logs`.

**Почему.** Имена OpenTelemetry полезны только с экспортёром OTel, а его нет (0006). Переименование сломало бы skill `studio-health` и привычные запросы к `/api/logs`. Совместимость с `traceparent` даёт агенту или прокси связать свой запрос со строками журнала, а журнал можно отдать в инструмент трассировки как есть. `traceresponse` убран из черновика W3C. Черновик `application/health+json` истёк в 2022 году, полное соответствие ему ничего не даёт; `version` и `time` нужны агенту, который сравнивает два отчёта. Агент приходит к студии сам (skill, субагент `studio-monitor`, `/loop`, хук `SessionStart`), так что push-уведомления по MCP ничего не добавили бы.

**Отказались.** От переименования полей под OTel semconv, пока нет экспортёра. От `traceresponse`. От полного формата `application/health+json`. От уведомлений MCP.
