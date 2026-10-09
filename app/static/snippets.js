/* HTML and Markdown snippets for the code modes.
 * The HTML grammar of rich messages is not in the machine-readable spec, so
 * this list is maintained by hand (see README "Обновление"). Anything you
 * type that is not here is still sent to Telegram unchanged.
 * $SEL$ is replaced by the selected text, $ID$ by a fresh media id.
 * verified: false marks syntax taken from third-party sources.
 */
window.SNIPPETS = {
  html: [
    { group: 'Текст', items: [
      { label: 'Ж', title: 'Жирный', snip: '<b>$SEL$</b>' },
      { label: 'К', title: 'Курсив', snip: '<i>$SEL$</i>' },
      { label: 'Ч', title: 'Подчёркнутый', snip: '<u>$SEL$</u>' },
      { label: 'З', title: 'Зачёркнутый', snip: '<s>$SEL$</s>' },
      { label: 'Спойлер', snip: '<tg-spoiler>$SEL$</tg-spoiler>' },
      { label: 'Маркер', snip: '<mark>$SEL$</mark>' },
      { label: 'Код', snip: '<code>$SEL$</code>' },
      { label: 'x₂', title: 'Нижний индекс', snip: '<sub>$SEL$</sub>' },
      { label: 'x²', title: 'Верхний индекс', snip: '<sup>$SEL$</sup>' },
      { label: 'Ссылка', snip: '<a href="https://">$SEL$</a>' },
      { label: 'Упоминание', snip: '<a href="tg://user?id=123456">$SEL$</a>' },
      { label: 'Эмодзи', title: 'Кастомный эмодзи', snip: '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>' },
      { label: 'Формула', snip: '<tg-math>$SEL$</tg-math>' },
      { label: 'Дата', title: 'Дата/время в часовом поясе читателя', snip: '<tg-time unix="1798761600" format="wDT">$SEL$</tg-time>', verified: false },
      { label: 'Сноска', snip: '<a href="#n1"><sup>1</sup></a>', verified: false },
    ] },
    { group: 'Блоки', items: [
      { label: 'H1', snip: '<h1>$SEL$</h1>' }, { label: 'H2', snip: '<h2>$SEL$</h2>' }, { label: 'H3', snip: '<h3>$SEL$</h3>' },
      { label: 'Абзац', snip: '<p>$SEL$</p>' },
      { label: 'Список', snip: '<ul>\n  <li>$SEL$</li>\n  <li></li>\n</ul>' },
      { label: 'Нумерованный', snip: '<ol>\n  <li>$SEL$</li>\n  <li></li>\n</ol>' },
      { label: 'Чек-лист', snip: '<ul>\n  <li><input type="checkbox" checked> $SEL$</li>\n  <li><input type="checkbox"> </li>\n</ul>' },
      { label: 'Цитата', snip: '<blockquote>\n  <p>$SEL$</p>\n  <cite>Автор</cite>\n</blockquote>' },
      { label: 'Свёрнутая цитата', snip: '<blockquote collapsed>\n  $SEL$\n</blockquote>', verified: false },
      { label: 'Акцент', title: 'Pull quote', snip: '<aside>$SEL$<cite>Автор</cite></aside>' },
      { label: 'Раскрывашка', snip: '<details>\n  <summary>Заголовок</summary>\n  <p>$SEL$</p>\n</details>' },
      { label: 'Таблица', snip: '<table bordered striped>\n  <caption>Подпись</caption>\n  <tr><th>Колонка 1</th><th>Колонка 2</th></tr>\n  <tr><td>$SEL$</td><td></td></tr>\n</table>' },
      { label: 'Код-блок', snip: '<pre><code class="language-python">$SEL$</code></pre>' },
      { label: 'Формула-блок', snip: '<tg-math-block>$SEL$</tg-math-block>' },
      { label: 'Разделитель', snip: '<hr/>' },
      { label: 'Подвал', snip: '<footer>$SEL$</footer>' },
      { label: 'Якорь', snip: '<a name="part1"></a>' },
    ] },
    { group: 'Медиа', items: [
      { label: 'Фото', snip: '<figure>\n  <img src="tg://photo?id=$ID$">\n  <figcaption>$SEL$</figcaption>\n</figure>', media: 'photo' },
      { label: 'Видео', snip: '<video src="tg://video?id=$ID$"></video>', media: 'video' },
      { label: 'Аудио', snip: '<audio src="tg://audio?id=$ID$"></audio>', media: 'audio' },
      { label: 'Файл', snip: '<tg-document src="tg://document?id=$ID$"></tg-document>', media: 'document' },
      { label: 'Коллаж', snip: '<tg-collage>\n  <img src="tg://photo?id=pic1">\n  <img src="tg://photo?id=pic2">\n</tg-collage>' },
      { label: 'Слайдшоу', snip: '<tg-slideshow>\n  <img src="tg://photo?id=pic1">\n  <img src="tg://photo?id=pic2">\n</tg-slideshow>' },
      { label: 'Карта', snip: '<tg-map lat="55.7522" long="37.6156" zoom="15"/>', verified: false },
    ] },
    { group: 'Кнопки', items: [
      { label: 'Ряд кнопок', snip: '<tg-button-row align="center">\n  <tg-button type="url" url="https://t.me/telegram" style="primary">$SEL$</tg-button>\n</tg-button-row>', verified: false },
      { label: 'Копировать', snip: '<tg-button type="copy_text" text="PROMO">Скопировать промокод</tg-button>', verified: false },
      { label: 'Callback', snip: '<tg-button type="callback_data" data="like">👍</tg-button>', verified: false },
    ] },
  ],
  markdown: [
    { group: 'Markdown', items: [
      { label: 'Ж', snip: '**$SEL$**' }, { label: 'К', snip: '_$SEL$_' }, { label: 'З', snip: '~~$SEL$~~' },
      { label: 'Код', snip: '`$SEL$`' }, { label: 'Ссылка', snip: '[$SEL$](https://)' },
      { label: 'H1', snip: '# $SEL$' }, { label: 'H2', snip: '## $SEL$' },
      { label: 'Список', snip: '- $SEL$\n- ' }, { label: 'Чек-лист', snip: '- [x] $SEL$\n- [ ] ', verified: false },
      { label: 'Цитата', snip: '> $SEL$' },
      { label: 'Таблица', snip: '| Колонка 1 | Колонка 2 |\n|---|---|\n| $SEL$ |  |' },
      { label: 'Код-блок', snip: '```python\n$SEL$\n```' },
      { label: 'Разделитель', snip: '\n---\n' },
      { label: 'Фото', snip: '![]( tg://photo?id=$ID$ )'.replace(/ /g, ''), media: 'photo' },
    ] },
    { group: 'HTML внутри Markdown', items: [
      { label: 'Спойлер', snip: '<tg-spoiler>$SEL$</tg-spoiler>' },
      { label: 'Раскрывашка', snip: '<details><summary>Заголовок</summary>\n\n$SEL$\n\n</details>' },
      { label: 'Формула', snip: '<tg-math>$SEL$</tg-math>' },
      { label: 'Подвал', snip: '<footer>$SEL$</footer>' },
    ] },
  ],
};
