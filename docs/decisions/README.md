# Решения

Короткие записи о решениях, которые не видны из кода: что выбрали, почему и от чего отказались. Нужны, чтобы агент или человек не предлагал заново то, что уже взвесили.

- Одна запись на решение, файл `NNNN-коротко.md`, номер по порядку.
- Записи не переписывают задним числом. Передумали — новая запись, в старой строка «Заменено: NNNN».
- Пишут только о новых решениях. Правила, которые уже проверяет код или тест, сюда не дублируются.

| № | Решение |
|---|---|
| [0001](0001-no-bundler.md) | Редактор без сборщика и фреймворка |
| [0002](0002-lit-and-signals.md) | Lit и signals вендорены в `app/static/vendor/` |
| [0003](0003-spec-source.md) | Спецификация Bot API из telegram-bot-api-spec, а не со страницы Telegram |
| [0004](0004-no-retry-after-send.md) | Нет автоповтора, если пост мог уйти |
| [0005](0005-missed-scheduled-posts.md) | Опоздавший отложенный пост не отправляется сам |
| [0006](0006-observability-in-process.md) | Наблюдаемость внутри процесса: журнал с ротацией и `/api/health`, без Prometheus и Grafana |
| [0007](0007-one-process-lease.md) | Один процесс с lease в SQLite, а не несколько воркеров и не Postgres |
| [0008](0008-shared-lists-as-files.md) | Общие списки как отдельные файлы, справочники маршрутов и инструментов собираются из кода |
| [0009](0009-named-tokens.md) | Именные токены с уровнями и cookie-сессия редактора, а не пользователи и OAuth |
| [0010](0010-server-image-and-pinned-spec.md) | Сервер: образ из GHCR, закреплённая спецификация, Caddy и Litestream |
| [0011](0011-dead-mans-switch.md) | Внешний сигнал «жива»: dead man's switch и `/readyz` снаружи |
| [0012](0012-media-fetch-proxy.md) | Скачивание по ссылке через системный прокси, как запросы к Telegram |
| [0013](0013-message-kinds-registry.md) | Типы сообщений (пост, опрос, Live Photo, платный контент) в одном реестре `services/kinds.py` |
| [0014](0014-vendored-api-docs.md) | Swagger UI для `/docs` вендорен в `app/static/vendor/`, без CDN и ReDoc |
| [0015](0015-forward-import.md) | Пост из Telegram в черновик пересылкой боту, а не через MTProto или веб-виджет |
| [0016](0016-post-templates.md) | Шаблоны постов: поля `{{key}}` только там, где значение остаётся текстом; Markdown пока не заполняется |
| [0017](0017-codemirror-vendored-modules.md) | CodeMirror 6 для вкладок исходника: файлы из npm с переписанными импортами (`make vendor`), без сборщика |
| [0018](0018-html-export-on-request.md) | Визуальный пост в HTML по кнопке «В HTML», а не синхронизация вкладок |
| [0019](0019-unrecorded-send.md) | Пост ушёл, а запись не удалась: 502 «не публикуйте повторно» с `message_id`, дозапись из памяти, шаги после записи — предупреждения |
| [0020](0020-dependabot-self-contained-prs.md) | Dependabot только для PR, готовых как пришли: npm-инструменты, digest образа, `deploy/`, actions; Python-пакеты и CodeMirror обновляются намеренно |
| [0021](0021-ephemeral-callback-reply.md) | Rich-ответ на callback-кнопку: поле `reply` с параметрами `sendRichMessage`, ответ до `answerCallbackQuery`, окно 15 секунд от получения, без повторов |
| [0023](0023-chat-type-rules-from-descriptions.md) | Что не работает в чате назначения: правила из описаний спецификации, замеры отдельным списком; `ephemeral_message_parameters` в `options` запрещён |
