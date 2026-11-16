# 0024. HTML-виджетов (`sendWidget`) в редакторе нет

2026-11-16, issue #169. Bot API 10.4 (9 ноября 2026) добавил `sendWidget`: сообщение с одним HTML-документом, в котором встроены CSS и JavaScript. Документ занимает 1–32 768 байт в UTF-8 и не больше 4096 байт после zlib уровня 9 (`methods.sendWidget.fields.html` в `app/bundled/api.json`, раздел [What a widget is](https://core.telegram.org/bots/widgets#what-a-widget-is)).

**Решение.** В редакторе нет ни типа сообщения «виджет», ни вкладки для него, ни предпросмотра. В `app/services/kinds.py` ([0013](0013-message-kinds-registry.md)) его тоже нет. Методы виджетов, как и любые другие методы Bot API, доступны через `/api/raw` (`POST /api/raw/sendWidget`). Но так работает только виджет без `allow_queries`. Запросы виджета (`widget_query`, `shared_widget_query`) студия не получает: их нет в `ALLOWED_UPDATES` (`app/services/updates.py`). А `getUpdates` через `/api/raw` забрал бы обновления у цикла студии. Ответить на запрос за 15 секунд (`answerWidgetQuery`) некому.

**Почему.** Студия делает посты канала, а опубликовать виджет в канале нельзя: «Widgets cannot be posted in channels, directly or via the bot, but users can forward widgets there» ([Where widgets live](https://core.telegram.org/bots/widgets#where-widgets-live)). В рабочей среде виджет работает только в личке с ботом, и переслать его нельзя: у отправленного виджета всегда защищённый контент (описание `sendWidget` в `api.json`, раздел [Availability](https://core.telegram.org/bots/widgets#availability)). Скопировать тоже нельзя: `copyMessage` и `copyMessages` виджеты не принимают ([Forwards](https://core.telegram.org/bots/widgets#forwards)). Группы, inline-режим и пересылка (в том числе в каналы) пока есть только на тестовом сервере.

**Чего бы это стоило.** Нынешний предпросмотр (`render.js`) виджет не нарисует, а отдельный всё равно расходился бы с клиентом. JavaScript виджета исполняется в QuickJS, «never in a WebView or browser context» ([What a widget is](https://core.telegram.org/bots/widgets#what-a-widget-is)). Страницу рисует сам клиент: каждое изменение он проверяет по профилю виджета. То, чего в профиле нет, клиент выбрасывает: поля форм, единицы `vh`, события клавиатуры. `overflow: auto` он заменяет на `hidden`, а сетевые API бросают `NetworkDisabledError` ([What widgets support](https://core.telegram.org/bots/widgets#what-widgets-support)). Документация Telegram отводит браузеру только «quick first look»: он рисует и то, что клиент удаляет, и не соблюдает лимиты ([A first look in a browser](https://core.telegram.org/bots/widgets#a-first-look-in-a-browser)). Кроме того, редактору пришлось бы исполнять чужой JavaScript, в том числе написанный агентом, а это против CSP без inline-скриптов (п. 5 `AGENTS.md`). Понадобились бы:
- отдельный маршрут со своим CSP;
- `<iframe sandbox="allow-scripts">` без `allow-same-origin`;
- новый тип в `app/services/kinds.py`;
- рукописный профиль проверки, повторяющий профиль клиента.

Всё это ради сообщения, которое нельзя опубликовать в канале.

**Отказались.** От редактора виджетов без предпросмотра. Он дал бы только поле для HTML и кнопку «Отправить в личку», а это и так делает `/api/raw`.

**Когда пересмотреть.** Когда в рабочей среде Telegram разрешит пересылать виджеты (тогда виджет из лички можно будет вручную переслать в канал) или публиковать их в каналах. Сейчас пересылка, в том числе в каналы, есть только на тестовом сервере, а опубликовать виджет в канале нельзя нигде.
