# 0024. HTML-виджетов (`sendWidget`) в редакторе нет

2026-11-16, issue #169. Bot API 10.4 (9 ноября 2026) добавил `sendWidget`: сообщение с одним HTML-документом, в котором встроены CSS и JavaScript. Документ занимает 1–32 768 байт в UTF-8 и не больше 4096 байт после zlib уровня 9 (`methods.sendWidget.fields.html` в `app/bundled/api.json`, раздел [What a widget is](https://core.telegram.org/bots/widgets#what-a-widget-is)).

**Решение.** В редакторе нет ни типа сообщения «виджет», ни вкладки для него, ни предпросмотра. В `services/kinds.py` ([0013](0013-message-kinds-registry.md)) его тоже нет. Методы виджетов, как и любые другие методы Bot API, доступны через `/api/raw`.

**Почему.** Студия делает посты канала, а в канал виджет не попадает. «Widgets cannot be posted in channels, directly or via the bot» ([Where widgets live](https://core.telegram.org/bots/widgets#where-widgets-live)). В рабочей среде виджет работает только в личке с ботом, и переслать его нельзя: у отправленного виджета защищённый контент, `copyMessage` его не копирует (описание `sendWidget` в `api.json`, раздел [Availability](https://core.telegram.org/bots/widgets#availability)). Группы, inline-режим и пересылка в каналы пока есть только на тестовом сервере.

**Чего бы это стоило.** Предпросмотр «как в Telegram» сделать нельзя, поэтому пришлось бы строить его отдельно, а он всё равно расходился бы с клиентом. JavaScript виджета исполняется в QuickJS, «never in a WebView or browser context». Страницу рисует сам клиент: он проверяет каждое изменение по профилю виджета и удаляет то, чего в профиле нет (формы, `vh`, `overflow: auto`, сеть, клавиатура). Сама Telegram называет браузер только «quick first look», который не соблюдает ни профиль, ни лимиты ([A first look in a browser](https://core.telegram.org/bots/widgets#a-first-look-in-a-browser)). Кроме того, редактору пришлось бы исполнять чужой JavaScript, в том числе написанный агентом, а это против CSP без inline-скриптов (п. 5 `AGENTS.md`). Понадобились бы отдельный маршрут со своим CSP, `<iframe sandbox="allow-scripts">` без `allow-same-origin`, новый тип в `services/kinds.py` и рукописный профиль проверки, повторяющий профиль клиента. Всё это ради сообщения, которое в канал не отправить.

**Отказались.** От редактора виджетов без предпросмотра. Он дал бы только поле для HTML и кнопку «Отправить в личку», а это и так делает `/api/raw` (`POST /api/raw/sendWidget`).

**Когда пересмотреть.** Когда в рабочей среде Telegram разрешит виджеты в каналах или пересылку виджета в канал. Сейчас и то и другое есть только на тестовом сервере.
