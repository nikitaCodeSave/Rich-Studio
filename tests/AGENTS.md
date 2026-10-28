# Тесты

- `pytest`: API на подставном Telegram (`mock_telegram.py`), у каждого теста своя пустая папка данных, фоновые циклы выключены (`STUDIO_BACKGROUND=off`). Нужен цикл — вызывай `scheduler.run_due_posts()` или `updates.handle_update()` сам.
- Подменять Telegram: `monkeypatch.setattr(telegram, "call", fake)`. Настройки: `monkeypatch.setattr(settings.get(), "preview_chat", "")`.
- Ожидание подменяй через `_sleep` модуля (`telegram.client`, `services.updates`), никогда не `asyncio.sleep` глобально.
- Найденный баг — сначала падающий тест (`test_regressions.py`), потом исправление.
- `render_unit.mjs`: враждебные примеры для санитайзера, запуск `npm run test:render`, студия не нужна.
- `preview_shots.mjs`: каждый пример из `examples/` в светлой и тёмной теме, проверка вёрстки (нет ошибок, у блоков есть размер, ничего не вылезает из пузыря) и скриншоты в `test-results/preview/` (в CI это артефакт). Пиксели не сравниваются: шрифты на разных машинах разные. Новый блок в спецификации: добавь пример в `examples/`, и он попадёт сюда сам.
- `e2e_ui.mjs`: проверки в браузере против `make mock` + `make dev`. Каждая проверка — отдельный файл `e2e/NN-имя.mjs` (`export default async function (ctx)`, помощники в `e2e/harness.mjs`). Файлы идут по порядку имён на одной странице. Новая проверка — новый файл со следующим номером, общие файлы не трогаются. Одна проверка: `E2E_ONLY=poll node tests/e2e_ui.mjs`. Ждите событие (`waitForFunction`, опрос лога mock), а не фиксированную паузу.
- `test_spec_coverage.py`: подписи и предпросмотр покрывают все блоки и inline-типы из `app/bundled/api.json`; сообщение об ошибке перечисляет, что добавить.
- `test_openapi_contract.py`: схема API совпадает с `docs/openapi.json`; после намеренного изменения `make openapi`.
