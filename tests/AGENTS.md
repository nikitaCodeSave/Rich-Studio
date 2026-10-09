# Тесты

- `pytest`: API на подставном Telegram (`mock_telegram.py`), у каждого теста своя пустая папка данных, фоновые циклы выключены (`STUDIO_BACKGROUND=off`). Нужен цикл — вызывай `scheduler.run_due_posts()` или `updates.handle_update()` сам.
- Подменять Telegram: `monkeypatch.setattr(telegram, "call", fake)`. Настройки: `monkeypatch.setattr(settings.get(), "preview_chat", "")`.
- Ожидание подменяй через `_sleep` модуля (`telegram.client`, `services.updates`), никогда не `asyncio.sleep` глобально.
- Найденный баг — сначала падающий тест (`test_regressions.py`), потом исправление.
- `render_unit.mjs`: враждебные примеры для санитайзера, запуск `npm run test:render`, студия не нужна.
- `e2e_ui.mjs`: проверки в браузере против `make mock` + `make dev`.
