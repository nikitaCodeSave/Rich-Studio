# Rich Studio: правила для агентов

Локальный сервис для rich-постов Telegram-канала: визуальный редактор, предпросмотр как в Telegram, REST API для агентов, skill для Claude Code. Личный инструмент одного человека на одной машине, не продукт.

Устройство кода, слои и «куда добавлять новое»: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Прочитай его перед первым изменением.

## Проверка

```
pip install -r requirements-dev.txt && npm ci   # один раз
make check                                      # всё: ruff, format, mypy, слои, pytest, eslint, tsc, санитайзер, сверка с Telegram, скриншоты предпросмотра
make fmt                                        # автоисправление формата и линтеров
```

Изменение готово, только когда `make check` зелёный. Изменил маршруты, модели API или инструменты MCP намеренно — `make docs` и закоммить `docs/openapi.json` и `docs/reference/` (тесты `test_openapi_contract.py` и `test_reference_docs.py` сверяют их). Браузерные проверки: `make mock`, `make dev`, затем `make e2e` (нужен Chromium для Playwright).

## Главные правила

1. **Payload уходит в Bot API как есть.** Не переименовывай и не «чини» поля поста. `telegram/` меняет только `upload:<имя>` → `attach://`. Незнакомые поля дают предупреждение, а не ошибку: так новые возможности Bot API работают в день релиза.
2. **Редактор генерируется из спецификации** (`app/bundled/api.json`, кэш в `DATA_DIR`). Не заводи жёстких списков блоков и полей. Руками поддерживаются только русские подписи (`labels.js`), сниппеты (`snippets.js`) и отрисовка новых блоков в `render.js`. Чего из этого не хватает для текущей `api.json`, перечисляет `tests/test_spec_coverage.py`: после обновления спецификации сделай его зелёным.
3. **Слои** `api | mcp_tools → services → telegram | store | spec`. Сервисы и ниже не импортируют FastAPI. Проверяет `lint-imports` (контракты в `pyproject.toml`), нарушение роняет `make check`.
4. **Политика ошибок отправки** (ARCHITECTURE.md): ушедший пост всегда записан как отправленный; при сетевой ошибке во время отправки повтора нет (`MayHaveGone`, и `Idempotency-Key` тоже не даёт повторить); предпросмотр никогда не уходит в канал по умолчанию. Новый маршрут, который публикует в канал, оборачивай в `services.idempotency.run` и передавай `actor` (см. `api/posts.py`).
5. **Безопасность.** Пользовательский HTML в DOM только через `h()`/`textContent` или `RichRender` (санитайзер). `innerHTML` запрещён ESLint. CSP без inline-скриптов. Новая дыра в санитайзере: сначала пример в `tests/render_unit.mjs`.
6. **Секреты.** Токен бота только в `.env` (в git не попадает). Не логируй URL запросов к Telegram целиком: в них токен.

## Несколько агентов параллельно

Над репозиторием работают несколько агентов сразу. Чтобы их PR не конфликтовали:

- **Одна задача = один PR = заранее объявленные пути** (шаблон `.github/ISSUE_TEMPLATE/agent-task.md`). Задачи с пересекающимися путями не идут параллельно: либо по очереди, либо одна интеграционная ветка.
- **Вертикальный срез делает один агент**: сервис + маршрут + инструмент MCP + тест + документация. Не делите задачу по слоям между агентами.
- **Области**, задача берёт одну:

| Область | Пути |
|---|---|
| backend | `app/services/`, `app/store/`, `app/telegram/`, `app/spec/` |
| api+mcp | `app/api/`, `app/mcp_tools/`, `app/models.py`, `docs/openapi.json`, `docs/reference/` |
| editor | `app/static/`, `types/`, `tests/e2e/`, `tests/*.mjs` |
| platform | `Dockerfile`, `docker-compose.yml`, `deploy/`, `requirements.lock`, `.github/`, `Makefile`, `scripts/`, `.claude/hooks/` |
| docs | `README.md`, `.claude/skills/`, `docs/` |

- Общие списки разнесены по файлам, чтобы параллельные PR не правили одну строку: шаг схемы — новый файл `app/store/migrations/NNNN_имя.sql`, инструмент MCP — функция в `app/mcp_tools/tools/<область>.py` (новая область — новый файл), браузерная проверка — новый файл `tests/e2e/NN-имя.mjs`. Списки маршрутов и инструментов в `docs/reference/` собирает `make docs`; при конфликте в них не сливайте руками, а пересоберите. README и `SKILL.md` правьте точечно, без переформатирования соседних строк.
- Перед PR: rebase на свежий `main`, `make docs`, если менялись маршруты или инструменты, затем `make check`. Номер нового файла миграции, занятый другим PR, роняет `test_migrations.py`: после rebase возьмите следующий свободный.
- Ревью делает не автор (см. раздел 9 в `docs/REVIEW_CHECKLIST.md`). Approve и merge только у человека.
- Job `supply-chain` и Trivy в `docker` падают и от свежей CVE в зависимости, которой нет в диффе. Сначала попробуйте обновить зависимость отдельным PR. Если исправления нет, временно игнорируйте её: `pip-audit --ignore-vuln <ID>` в `ci.yml` или строка в `.trivyignore`, всегда с комментарием «почему, ссылка, до какой даты». Отключать job нельзя.
- В облачной сессии зависимости ставит `.claude/hooks/session-start.sh` (в `.venv` и `node_modules`). Для промежуточных шагов есть `make check-fast` (без браузера), перед PR нужен полный `make check`.

## Документация в том же PR

- Новая возможность или изменённое поведение: в том же PR поправь README (описание возможности, «Что обновляется вручную») и `.claude/skills/telegram-rich-posts/SKILL.md`, если это касается агентов. Описание маршрута — его `summary=` и docstring, инструмента — его docstring: оттуда `make docs` собирает `docs/reference/`, руками эти списки не правятся.
- Изменил слои, политику ошибок или «куда добавлять новое»: поправь `docs/ARCHITECTURE.md`.
- Принял решение, которое не видно из кода (выбор библиотеки, отказ от подхода): запись в [docs/decisions/](docs/decisions/README.md). Перед тем как предлагать сборщик, фреймворк или другой источник спецификации, прочитай, почему от них уже отказались.
- Изменил вид редактора: `make e2e` сохраняет скриншоты в `test-results/ui/` (1400 и 390 px). Посмотри их и покажи человеку вместе с PR. В CI они лежат в артефакте `editor-screenshots`, скриншоты предпросмотра — в `preview-screenshots`.

## Не делать

- Не править `app/bundled/api.json` руками: это копия спецификации с https://github.com/PaulSonOfLars/telegram-bot-api-spec. Обновляет её `make spec-update` (код выхода 10, если версия новая); после него сделай зелёным `tests/test_spec_coverage.py`.
- Не подменять `asyncio.sleep` глобально в тестах: это останавливает подставной Telegram в соседнем потоке. Подменяй `_sleep` модуля.
- Не менять старые файлы в `app/store/migrations/`, только добавлять новые.
- Не править `requirements.lock` руками: после изменения `requirements.txt` (и в PR Dependabot) `make lock`, `tests/test_lock.py` сверяет их.
- Не добавлять сборщик, фреймворк или новую рантайм-зависимость без обсуждения: редактор специально работает без сборки.
- Не отправлять ничего в настоящий Telegram из тестов. Для всего есть `tests/mock_telegram.py`.

## Где что

| Нужно | Смотри |
|---|---|
| новый метод Bot API для постов | `app/services/posts.py` + `app/api/posts.py` + тест |
| новое событие из getUpdates | `app/services/updates.py` (`handle_update`, `ALLOWED_UPDATES`) |
| новая настройка | `app/settings.py` + `.env.example` |
| сервер: образ, Caddy, Litestream, восстановление | `deploy/` (там README), `Dockerfile`, `scripts/restore_check.sh` |
| инструмент MCP для агентов | `app/mcp_tools/tools/<область>.py` + тест в `tests/test_mcp.py` + `make docs` |
| редактор | `app/static/js/` (см. AGENTS.md там) |
| предпросмотр | `app/static/render.js`, стили `tg.css`; в чате Claude: `app/static/preview-app.js` + `app/mcp_tools/preview_app.py` |
| журнал, `/api/health`, уборка | `app/logs.py`, `app/metrics.py`, `app/services/health.py`, `housekeeping.py`; устройство в `docs/OBSERVABILITY.md` |
| тесты | `tests/` (см. AGENTS.md там) |
| skill для пользователя | `.claude/skills/telegram-rich-posts/SKILL.md` |
| здоровье работающей студии (skill и субагент) | `.claude/skills/studio-health/SKILL.md`, `.claude/agents/studio-monitor.md` |

Сообщения коммитов и описания PR пиши по-русски или по-английски, коротко: что видно снаружи и зачем.
