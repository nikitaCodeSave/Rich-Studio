# Swagger UI для `/docs`

Сторонний код, файлы не правятся руками. Страницу и настройки собирают `app/api/docs.py` и `app/static/api-docs.js`, решение: [docs/decisions/0014](../../../../docs/decisions/0014-vendored-api-docs.md).

- Версия: `swagger-ui-dist` **5.35.0**, лицензия Apache-2.0 (`LICENSE`, `NOTICE`; лицензии того, что внутри бандла, в `swagger-ui-bundle.js.LICENSE.txt`).
- Источник: `npm pack swagger-ui-dist@5.35.0`, integrity архива `sha512-U06rpMtW9V3XPGOBeamTUSr0Z09jMX4IJuANpE/EZbAhw0G8CgwrzU4YmXkn/h0wCDNsXdGarTNNHC3sU8yVUQ==`.
- Взяты без изменений: `swagger-ui-bundle.js` (sha256 `e6a898692f2d768e807c3ba4626068a69a2c5dd960dd6c76f3e4d3a3de180af2`), `swagger-ui.css` (sha256 `5ed08757e53da3e026d6cbecc61fe5d43585c9560b302d534ad56692519a273d`), `LICENSE`, `NOTICE`, `swagger-ui-bundle.js.LICENSE.txt`. Остальное из пакета (standalone preset, ES-сборки, source maps, примеры) студии не нужно.

## Как обновить

1. Последняя версия: `npm view swagger-ui-dist version`. Мажорная версия меняется только отдельным решением.
2. Во временной папке вне репозитория: `npm pack swagger-ui-dist@<версия> && tar -xzf swagger-ui-dist-<версия>.tgz`. Пакет не ставится (`npm install` запустил бы его postinstall), в `package.json` он не попадает.
3. Скопировать пять файлов выше из `package/` сюда, поправить в этой заметке версию, integrity (из вывода `npm pack`) и sha256 (`sha256sum swagger-ui-bundle.js swagger-ui.css`).
4. `make check` и `make e2e-local ONLY=api-docs`: страница рисуется, нарушений CSP нет, ни один запрос не уходит за пределы студии, «Try it out» с ключом отвечает 200. Посмотреть `test-results/ui/docs-1400.png`.
