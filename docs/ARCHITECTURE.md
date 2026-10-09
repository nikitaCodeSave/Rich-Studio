# Как устроен код Rich Studio

Правило слоёв: **api / mcp_tools → services → telegram / store / spec**. Стрелки идут только вниз.

| Папка / файл | Что делает | Чего не делает |
|---|---|---|
| `app/main.py` | `create_app()`: middleware, обработчики ошибок, роутеры, фоновые циклы | логики нет |
| `app/settings.py` | все переменные окружения в одном `Settings`; модули вызывают `settings.get()` в момент использования | не читает env при импорте других модулей |
| `app/security.py` | Host, CSRF, Content-Type, X-API-Key, CSP | |
| `app/errors.py` | `StudioError` (400), `NotFound`, `Invalid` (422), `Upstream` (502) | |
| `app/models.py` | тела запросов (pydantic), описания полей на русском для /docs | |
| `app/api/*.py` | роутеры: разобрать запрос → вызвать сервис → вернуть результат | не ходят в Telegram напрямую, кроме `raw` и `check` |
| `app/mcp_tools/` | MCP-сервер на `/mcp`: курированные инструменты для агентов поверх сервисов, тот же guard и ключ API | не дублирует логику сервисов; удаление постов и `/api/raw` сюда не выносятся; страница предпросмотра `ui://` собирается из `static/` (`preview_app.py`), а не пишется отдельно |
| `app/services/publisher.py` | отправка, шаги после отправки, рассылка в несколько каналов, валидация, время | не импортирует FastAPI |
| `app/services/posts.py` | что можно сделать с постом: опубликовать, предпросмотр, отложить, заменить, закрепить, кнопки, удалить, опросы | |
| `app/services/scheduler.py` | отложенные посты | |
| `app/services/updates.py` | getUpdates: нажатия, первый комментарий, реакции, опросы; `STATUS` | |
| `app/services/media.py` | имена загрузок, путь внутри папки media | |
| `app/telegram/` | транспорт к Bot API, `upload:` → `attach://`, проверка прав бота | не меняет payload, кроме `upload:` |
| `app/store/` | SQLite: соединения, миграции (`PRAGMA user_version`), черновики, посты | |
| `app/spec/` | загрузка и кэш api.json, сводка для редактора, diff версий, валидатор | |
| `app/static/js/` | редактор: ES-модули без сборки, вход `main.js`; общее состояние в `state.js` | не пишет пользовательский HTML в `innerHTML` (это запрещает ESLint) |
| `app/static/render.js` | предпросмотр поста и очистка HTML/Markdown (`sanitize`) | |

## Политика ошибок при отправке

1. Отправка прошла → пост записан как `sent`. Всё, что после (закреп, комментарий), только добавляет предупреждения.
2. Сетевая ошибка во время отправки, ответ 5xx или страница ошибки шлюза (`telegram.NoAnswer`) → «пост мог уйти, проверьте канал». Автоматического повтора нет. Повторять можно только то, что точно не ушло: `telegram.Unreachable` (соединение не установлено) и 429; планировщик возвращает такой пост в очередь (`retry_at`). Отложенный пост, опоздавший больше `SCHEDULE_LATE_MIN` (сервис был выключен), не отправляется, а получает статус `missed`.
3. Один канал упал → остальные всё равно отправляются.
4. Предпросмотр уходит только в `TG_PREVIEW_CHAT` или в явно указанный chat_id, никогда в канал по умолчанию.
5. `Idempotency-Key` (`services/idempotency.py`, таблица `idempotency`): повтор с тем же ключом возвращает первый результат; если первая попытка закончилась `MayHaveGone`, повтор получает ту же ошибку; если ничего не ушло, ключ освобождается.
6. Замена опубликованного поста (`posts.after_edit`) меняет в записи только текст и кнопки; ответы на кнопки и первый комментарий остаются, если новый пост не принёс свои. «message is not modified» от Telegram не ошибка.
7. Кто отправил пост, пишется в `posts.actor` из заголовка `X-Studio-Client` (`api/deps.py`); редактор шлёт `ui`, без заголовка `api`.

## Куда добавлять новое

- Новый метод Bot API для постов: функция в `services/posts.py` + маршрут в `api/posts.py` + тест в `tests/`.
- Новое событие от Telegram: ветка в `services/updates.handle_update` и тип в `ALLOWED_UPDATES`.
- Новая колонка в БД: `EXTRA_COLUMNS` в `store/db.py`, если это колонка; иначе новый шаг в `MIGRATIONS` (только добавлять в конец).
- Новая настройка: поле в `Settings` + `from_env` + строка в `.env.example`.

## Тесты

- `pytest` — API на фейковом Telegram (`tests/mock_telegram.py`, он же сервер для браузерных проверок). У каждого теста своя пустая папка данных, порядок тестов не важен. Фоновые циклы выключены (`STUDIO_BACKGROUND=off`); тест сам вызывает `scheduler.run_due_posts()` или `updates.handle_update()`.
- Ожидание в коде подменяется через `_sleep` модуля (`telegram.client`, `services.updates`), а не через `asyncio.sleep`: глобальная подмена останавливает фейковый сервер в соседнем потоке, и тесты зависают.
- Подменять настройки: `monkeypatch.setattr(settings.get(), "preview_chat", "")`.
- Подменять Telegram: `monkeypatch.setattr(telegram, "call", fake)`.
- `tests/e2e_ui.mjs` — 13 проверок в браузере против запущенной студии и `tests/mock_telegram.py`.
- `npm run test:render` — `tests/render_unit.mjs`: очистка HTML/Markdown в render.js на враждебных примерах (script, on*, javascript:, svg, mXSS), без студии. Новая дыра в санитайзере → сначала пример сюда.
- `npm run lint` — ESLint для `app/static` и тестов на JS: ловит неопределённые имена и забытые импорты, которые браузер покажет только при клике.
- Перед изменением: `ruff check . && ruff format --check . && mypy && pytest && npm run lint`.
