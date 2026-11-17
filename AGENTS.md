# Rich Studio: правила для агентов

Сервис для rich-постов Telegram-канала: визуальный редактор, предпросмотр как в Telegram, REST API и MCP для агентов, skill для Claude Code. Личный инструмент одного человека (на своём компьютере или сервере, `deploy/`), не продукт.

Устройство кода, слои и «куда добавлять новое»: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Прочитай его перед первым изменением.

## Проверка

```
pip install -r requirements-dev.txt && npm ci   # один раз; в облачной сессии это делает .claude/hooks/session-start.sh
make check-fast                                 # между шагами и перед PR
make fmt                                        # автоисправление формата и линтеров
```

Изменение готово к ревью, когда зелёный CI в PR: он гоняет те же шаги, что `make check` (это `check-fast` плюс JS-тесты `npm run test:…`), и все браузерные проверки. Локально, кроме `make check-fast`, запускай тесты своей области: `pytest tests/<файл> -k …`, нужные `npm run test:…` (`render`, `parity`, `shots`, `export`, `completion`, `codemirror`), `make e2e-local ONLY=<часть имени проверки>` (свой стенд: подставной Telegram и студия с пустым `DATA_DIR` на портах 7499 и 8099; нужен Chromium для Playwright). Изменил маршруты, модели API или инструменты MCP намеренно: `make docs` и закоммить `docs/openapi.json` и `docs/reference/` (их сверяют `test_openapi_contract.py` и `test_reference_docs.py`).

## Главные правила

1. **Payload уходит в Bot API как есть.** Не переименовывай и не «чини» поля поста: `telegram/` меняет только `upload:<имя>` на `attach://`. Незнакомое поле даёт предупреждение, а не ошибку: так новые возможности Bot API работают в день релиза.
2. **Редактор генерируется из спецификации** (`app/bundled/api.json`, кэш в `DATA_DIR`). Не заводи жёстких списков блоков и полей. Руками пишутся русские подписи (`labels.js`), сниппеты (`snippets.js`), отрисовка новых блоков (`render.js`), таблица «В HTML» (`html-export.js`) и замеры Telegram, которых нет в спецификации (README, «Что обновляется вручную»). Чего не хватает для текущей `api.json`, перечисляет `tests/test_spec_coverage.py`.
3. **Слои** `api | mcp_tools → services → telegram | store | spec`: сервисы и ниже не импортируют FastAPI. Контракты `lint-imports` в `pyproject.toml`.
4. **Политика ошибок отправки** (`docs/ARCHITECTURE.md`): ушедший пост всегда записан как отправленный; при сетевой ошибке во время отправки повтора нет (`MayHaveGone`, и `Idempotency-Key` тоже не даёт повторить); предпросмотр никогда не уходит в канал по умолчанию. Маршрут, который публикует в канал, оборачивай в `services.idempotency.run` и передавай `actor` (как в `app/api/posts.py`).
5. **Безопасность.** Пользовательский HTML в DOM только через `h()` (`dom.js`), `textContent` или `RichRender` (санитайзер); `innerHTML`, `outerHTML` и `insertAdjacentHTML` запрещает ESLint. CSP без inline-скриптов. Новая дыра в санитайзере: сначала пример в `tests/render_unit.mjs`.
6. **Секреты.** Токен бота только в `.env` (в git не попадает). Не логируй URL запросов к Telegram целиком: в них токен.

## Несколько агентов параллельно

- **Одна задача = один PR = заранее объявленные пути** (шаблон `.github/ISSUE_TEMPLATE/agent-task.md`). Задачи с пересекающимися путями идут по очереди или в одной интеграционной ветке.
- **Вертикальный срез делает один агент**: сервис, маршрут, инструмент MCP, тест и документация в одном PR.
- **Области**, задача берёт одну:

| Область | Пути |
|---|---|
| backend | `app/services/`, `app/store/`, `app/telegram/`, `app/spec/` |
| api+mcp | `app/api/`, `app/mcp_tools/`, `app/models.py`, `docs/openapi.json`, `docs/reference/` |
| editor | `app/static/`, `types/`, `tests/e2e/`, `tests/*.mjs` |
| platform | `Dockerfile`, `docker-compose.yml`, `deploy/`, `requirements.lock`, `.github/`, `Makefile`, `scripts/`, `.claude/hooks/` |
| docs | `README.md`, все `AGENTS.md` и `CLAUDE.md`, `.claude/skills/`, `.claude/agents/`, `docs/` |

- Общие списки разнесены по файлам, чтобы параллельные PR не правили одну строку: шаг схемы — новый файл `app/store/migrations/NNNN_имя.sql`, инструмент MCP — функция в `app/mcp_tools/tools/<область>.py` (новая область — новый файл), браузерная проверка — новый файл `tests/e2e/NN-имя.mjs`. Конфликт в `docs/reference/` не сливай руками: пересобери `make docs`. README и `SKILL.md` правь точечно, без переформатирования соседних строк.
- Перед PR: `git merge origin/main`, затем `make check-fast`. Номер миграции или браузерной проверки, занятый другим PR, замени следующим свободным (повтор номера миграции роняет `test_migrations.py`).
- Ревью делает не автор (`docs/REVIEW_CHECKLIST.md`, раздел 9). Approve и merge только у человека.

## Документация в том же PR

- Документация описывает студию такой, какая она сейчас: что и почему изменилось — в коммите и описании PR, решение для будущей работы — в `docs/decisions/`, отчёт об обновлении для поста — в `docs/releases/<дата>.md`, источник — путь к файлу. Следы истории, несуществующие файлы и документы сверх бюджета ловит `tests/test_docs_current.py`.
- Новая возможность или изменённое поведение: README (описание возможности, «Что обновляется вручную») и `.claude/skills/telegram-rich-posts/SKILL.md`, если это касается агентов. Маршрут описывают его `summary=` и docstring, инструмент — docstring: из них `make docs` собирает `docs/reference/`.
- Изменил слои, политику ошибок или «куда добавлять новое»: поправь `docs/ARCHITECTURE.md`.
- Принял решение, которого не видно из кода (выбор библиотеки, отказ от подхода): запись в [docs/decisions/](docs/decisions/README.md).
- Изменил вид редактора или предпросмотра: посмотри скриншоты (`test-results/ui/`, 1400 и 390 px, от `make e2e-local`; `test-results/preview/` от `npm run test:shots`) и покажи их человеку вместе с PR. В CI это артефакты `editor-screenshots` и `preview-screenshots`.

## Не делать

- Не править `app/bundled/api.json` руками: это копия спецификации с https://github.com/PaulSonOfLars/telegram-bot-api-spec. Обновляет её `make spec-update` (код выхода 10 значит «версия новая», а не сбой); после него сделай зелёными `tests/test_spec_coverage.py` и `test_the_rules_of_bot_api_10_4_are_read_from_its_descriptions` в `tests/test_chat_rules.py`. Второй покажет, какие фразы о чатах Telegram переписал, убрал или добавил: сверь новую формулировку с замером `REFUSED_IN` (`app/spec/validator.py`), прежде чем править `FROM_SPEC` и `MEASURED_ONLY` в тесте.
- Не подменять `asyncio.sleep` глобально в тестах: это останавливает подставной Telegram в соседнем потоке. Подменяй `_sleep` модуля (`telegram.client`, `services.updates`, …).
- Не менять старые файлы в `app/store/migrations/`, только добавлять новые.
- Не править `requirements.lock` руками: после изменения `requirements.txt` — `make lock`, `tests/test_lock.py` сверяет их.
- Не добавлять сборщик, фреймворк, новую рантайм-зависимость или другой источник спецификации без обсуждения: редактор специально работает без сборки, а почему от остального отказались, записано в `docs/decisions/`.
- Не отключать job CI, красный от свежей CVE в зависимости не из диффа: обновление или временное исключение со сроком — по `.claude/skills/ci-triage/SKILL.md`.
- Не отправлять ничего в настоящий Telegram из тестов. Для всего есть `tests/mock_telegram.py`.

## Где что

| Нужно | Смотри |
|---|---|
| новый метод Bot API для постов | `app/services/posts.py` + `app/api/posts.py` + тест |
| новое событие из getUpdates | `app/services/updates.py` (`handle_update`, `ALLOWED_UPDATES`) |
| новая настройка | `app/settings.py` + `.env.example` |
| сервер: образ, Caddy, Litestream, восстановление | `deploy/README.md`, `Dockerfile`, `scripts/restore_check.sh`, `scripts/image_smoke.sh` |
| вендор CodeMirror (редактор исходника) | `make vendor`, `make vendor-update`, `docs/decisions/0017-codemirror-vendored-modules.md` |
| инструмент MCP для агентов | `app/mcp_tools/tools/<область>.py` + тест в `tests/test_mcp.py` + `make docs` |
| редактор | `app/static/js/` (свой `AGENTS.md`) |
| предпросмотр | `app/static/render.js`, стили `tg.css`; в чате Claude: `app/static/preview-app.js` + `app/mcp_tools/preview_app.py` |
| журнал, `/api/health`, уборка | `app/logs.py`, `app/metrics.py`, `app/services/health.py`, `housekeeping.py`; устройство в `docs/OBSERVABILITY.md` |
| тесты | `tests/` (свой `AGENTS.md`) |
| skill для пользователя | `.claude/skills/telegram-rich-posts/SKILL.md` |
| здоровье работающей студии (skill и субагент) | `.claude/skills/studio-health/SKILL.md`, `.claude/agents/studio-monitor.md` |
| красный CI, PR Dependabot | `.claude/skills/ci-triage/SKILL.md` |
| документация устарела или разрослась | `.claude/skills/docs-tidy/SKILL.md` |
| отчёт и changelog последнего обновления, что дальше | `docs/releases/` (последний файл) |

Сообщения коммитов и описания PR пиши по-русски или по-английски, коротко: что видно снаружи и зачем.
