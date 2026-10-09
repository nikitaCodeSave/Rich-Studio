# Rich Studio: правила для агентов

Локальный сервис для rich-постов Telegram-канала: визуальный редактор, предпросмотр как в Telegram, REST API для агентов, skill для Claude Code. Личный инструмент одного человека на одной машине, не продукт.

Устройство кода, слои и «куда добавлять новое»: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Прочитай его перед первым изменением.

## Проверка

```
pip install -r requirements-dev.txt && npm ci   # один раз
make check                                      # всё: ruff, format, mypy, слои, pytest, eslint, tsc, санитайзер
make fmt                                        # автоисправление формата и линтеров
```

Изменение готово, только когда `make check` зелёный. Изменил маршруты или модели API намеренно — `make openapi` и закоммить `docs/openapi.json` (тест `test_openapi_contract.py` сверяет их). Браузерные проверки: `make mock`, `make dev`, затем `make e2e` (нужен Chromium для Playwright).

## Главные правила

1. **Payload уходит в Bot API как есть.** Не переименовывай и не «чини» поля поста. `telegram/` меняет только `upload:<имя>` → `attach://`. Незнакомые поля дают предупреждение, а не ошибку: так новые возможности Bot API работают в день релиза.
2. **Редактор генерируется из спецификации** (`app/bundled/api.json`, кэш в `DATA_DIR`). Не заводи жёстких списков блоков и полей. Руками поддерживаются только русские подписи (`labels.js`), сниппеты (`snippets.js`) и отрисовка новых блоков в `render.js`. Чего из этого не хватает для текущей `api.json`, перечисляет `tests/test_spec_coverage.py`: после обновления спецификации сделай его зелёным.
3. **Слои** `api | mcp_tools → services → telegram | store | spec`. Сервисы и ниже не импортируют FastAPI. Проверяет `lint-imports` (контракты в `pyproject.toml`), нарушение роняет `make check`.
4. **Политика ошибок отправки** (ARCHITECTURE.md): ушедший пост всегда записан как отправленный; при сетевой ошибке во время отправки повтора нет (`MayHaveGone`, и `Idempotency-Key` тоже не даёт повторить); предпросмотр никогда не уходит в канал по умолчанию. Новый маршрут, который публикует в канал, оборачивай в `services.idempotency.run` и передавай `actor` (см. `api/posts.py`).
5. **Безопасность.** Пользовательский HTML в DOM только через `h()`/`textContent` или `RichRender` (санитайзер). `innerHTML` запрещён ESLint. CSP без inline-скриптов. Новая дыра в санитайзере: сначала пример в `tests/render_unit.mjs`.
6. **Секреты.** Токен бота только в `.env` (в git не попадает). Не логируй URL запросов к Telegram целиком: в них токен.

## Не делать

- Не править `app/bundled/api.json` руками: это копия спецификации с https://github.com/PaulSonOfLars/telegram-bot-api-spec.
- Не подменять `asyncio.sleep` глобально в тестах: это останавливает подставной Telegram в соседнем потоке. Подменяй `_sleep` модуля.
- Не менять старые шаги `MIGRATIONS` в `app/store/db.py`, только добавлять в конец.
- Не добавлять сборщик, фреймворк или новую рантайм-зависимость без обсуждения: редактор специально работает без сборки.
- Не отправлять ничего в настоящий Telegram из тестов. Для всего есть `tests/mock_telegram.py`.

## Где что

| Нужно | Смотри |
|---|---|
| новый метод Bot API для постов | `app/services/posts.py` + `app/api/posts.py` + тест |
| новое событие из getUpdates | `app/services/updates.py` (`handle_update`, `ALLOWED_UPDATES`) |
| новая настройка | `app/settings.py` + `.env.example` |
| инструмент MCP для агентов | `app/mcp_tools/server.py` + тест в `tests/test_mcp.py` |
| редактор | `app/static/js/` (см. AGENTS.md там) |
| предпросмотр | `app/static/render.js`, стили `tg.css`; в чате Claude: `app/static/preview-app.js` + `app/mcp_tools/preview_app.py` |
| тесты | `tests/` (см. AGENTS.md там) |
| skill для пользователя | `skill/telegram-rich-posts/SKILL.md` |

Сообщения коммитов и описания PR пиши по-русски или по-английски, коротко: что видно снаружи и зачем.
