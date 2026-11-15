// «В HTML»: visual blocks become Telegram's rich HTML (app/static/js/html-export.js). No studio needed.
// 1. Telegram's own blocks: every case of tests/telegram_parse/captured.json, made sendable by the studio's converter
//    (spec/convert.py, the same as «В визуальный»), becomes HTML that the preview draws as the same post
//    (RichRender.outline) as the blocks the real Telegram made of it.
// 2. Every block and text element of the bundled spec in one post: the same check, so a type in the table works.
// 3. Refusals: an unknown block, field or element gives the list of what and where, and the input is not touched.
// Run: node tests/html_export.mjs (PYTHON=… with requirements.txt, else .venv or python3; CHROMIUM=… for a browser)
/* global RichRender -- defined by render.js inside the page */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = (f) => fileURLToPath(new URL(f, import.meta.url));
const captured = JSON.parse(readFileSync(here('telegram_parse/captured.json'), 'utf8'));
const spec = JSON.parse(readFileSync(here('../app/bundled/api.json'), 'utf8'));
const results = [];
const check = (name, ok, info = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !info ? '' : '\n      ' + info}`); };

// Telegram answers with RichBlock*; the editor gets them as InputRichBlock* through spec/convert.py
const python = process.env.PYTHON || (existsSync(here('../.venv/bin/python')) ? here('../.venv/bin/python') : 'python3');
const convert = `import json, sys
sys.path.insert(0, sys.argv[1])
import spec
s = spec.Spec()
s.data = json.loads(open(sys.argv[2], encoding="utf-8").read())
json.dump({k: spec.to_input_blocks(s, v) for k, v in json.load(sys.stdin).items()}, sys.stdout, ensure_ascii=False)`;
const withBlocks = Object.fromEntries(Object.entries(captured).filter(([, c]) => c.blocks).map(([k, c]) => [k, c.blocks]));
const inputBlocks = JSON.parse(execFileSync(python, ['-c', convert, here('../app'), here('../app/bundled/api.json')], { input: JSON.stringify(withBlocks), encoding: 'utf8' }));

// the editor's modules are ES modules with imports: the page is served from app/ under a made-up origin
const ORIGIN = 'http://studio.test';
const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
page.on('dialog', (d) => { pageErrors.push('dialog: ' + d.message()); d.dismiss(); });
await page.route(ORIGIN + '/**', (route) => {
  const path = new URL(route.request().url()).pathname;
  if (path === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><body><div id="toasts"></div></body>' });
  return route.fulfill({ path: here('../app' + path), contentType: path.endsWith('.js') ? 'text/javascript' : 'text/plain' });
});
await page.goto(ORIGIN + '/');
for (const f of ['vendor/markdown-it.min.js', 'vendor/katex/katex.min.js', 'render.js']) await page.addScriptTag({ url: '/static/' + f });
await page.addScriptTag({ type: 'module', content: "import * as m from '/static/js/html-export.js'; window.HtmlExport = m;" });
await page.waitForFunction(() => /** @type {any} */ (window).HtmlExport, null, { timeout: 5000 });

/** @returns {Promise<{ ok: boolean, html?: string, media?: any[], problems?: string[], diff?: any }>} */
const toHtml = (blocks, opts = {}) => page.evaluate(([b, o]) => /** @type {any} */ (window).HtmlExport.blocksToHtml(b, o), [blocks, opts]);
const look = (rm) => page.evaluate((x) => /** @type {any} */ (window).HtmlExport.lookOf(x), rm);
const rendersScript = (html) => page.evaluate((x) => RichRender.render({ rich_message: { html: x } }, {}).querySelectorAll('script, [onerror], [onclick]').length, html);

// ---------- 1. what the real Telegram parsed comes back as the same post ----------
// the HTML with line breaks between blocks is the same HTML: removing them gives the one the outline is read from
// (the outline counts a line break between two blocks as a space, Telegram and the preview do not)
const unlaid = (s) => s.replace(/\n */g, '');
/** blocks → HTML whose preview outlines as `expected`; the laid-out HTML differs from the checked one only by layout */
const exportsAs = async (blocks, expected) => {
  const r = await toHtml(blocks);
  const tight = await toHtml(blocks, { layout: false });
  const mine = tight.ok ? await look({ html: tight.html, media: tight.media }) : '';
  const ok = r.ok && tight.ok && mine === expected && unlaid(r.html || '') === unlaid(tight.html || '') && JSON.stringify(r.media) === JSON.stringify(tight.media);
  return { ok, r, info: r.ok ? `HTML:     ${mine}\n      expected: ${expected}\n      ${r.html}` : JSON.stringify(r.problems) + (r.diff ? ' ' + JSON.stringify(r.diff) : '') };
};
for (const [name, blocks] of Object.entries(inputBlocks)) {
  const { ok, info } = await exportsAs(blocks, await look({ blocks: captured[name].blocks }));
  check(`captured ${name}: Telegram's blocks → HTML → the same post`, ok, info);
}

// ---------- 2. every block and text element of the spec ----------
const consts = (base) => (spec.types[base].subtypes || []).filter((n) => spec.types[n]).map((n) => {
  const f = (spec.types[n].fields || []).find((x) => x.name === 'type');
  const m = f && /always\s+[“"]([^”"]+)[”"]/.exec(f.description || '');
  return m ? m[1] : null;
}).filter(Boolean);
const ph = (kind, id) => ({ type: kind, [kind]: { type: kind, media: 'file-' + id } });
const btn = (text, action) => ({ text, ...action });
const every = [
  { type: 'heading', size: 2, text: ['Все ', { type: 'bold', text: 'блоки' }] },
  { type: 'anchor', name: 'top' },
  {
    type: 'paragraph', text: [
      { type: 'italic', text: 'курсив' }, ' ', { type: 'underline', text: 'подчёркнутый' }, ' ', { type: 'strikethrough', text: 'зачёркнутый' }, ' ',
      { type: 'spoiler', text: 'спойлер' }, ' H', { type: 'subscript', text: '2' }, 'O x', { type: 'superscript', text: '2' }, ' ',
      { type: 'marked', text: 'маркер' }, ' ', { type: 'code', text: 'код' }, ' ', { type: 'url', text: 'ссылка', url: 'https://t.me/telegram' }, ' ',
      { type: 'text_mention', text: 'Павел', user: { id: 123456789, is_bot: false, first_name: 'Павел' } }, ' ',
      { type: 'email_address', text: 'a@t.me', email_address: 'a@t.me' }, ' ', { type: 'phone_number', text: '+123456789', phone_number: '+123456789' }, ' ',
      { type: 'mention', text: '@durov', username: 'durov' }, ' ', { type: 'hashtag', text: '#тег', hashtag: 'тег' }, ' ', { type: 'cashtag', text: '$USD', cashtag: 'USD' }, ' ',
      { type: 'bot_command', text: '/start', bot_command: 'start' }, ' ', { type: 'bank_card_number', text: '4242 4242 4242 4242', bank_card_number: '4242 4242 4242 4242' }, ' ',
      { type: 'ton_address', text: 'UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ', address: 'UQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAJKZ' }, ' ',
      { type: 'date_time', text: 'завтра', unix_time: 1798761600, date_time_format: 'wDT' }, ' ',
      { type: 'custom_emoji', custom_emoji_id: '5368324170671202286', alternative_text: '👍' }, ' ', { type: 'mathematical_expression', expression: 'a^2 < b & c' }, ' ',
      { type: 'anchor', name: 'mid' }, { type: 'anchor_link', text: 'к якорю', anchor_name: 'mid' }, ' ', { type: 'anchor_link', text: 'наверх', anchor_name: '' }, ' ',
      { type: 'reference_link', text: { type: 'superscript', text: '1' }, reference_name: 'n1' }, ' ',
      { type: 'button', button: btn('в тексте', { callback_data: 'cb1' }) },
      '\nвторая строка <не тег> & "кавычки"',
    ],
  },
  { type: 'pre', language: 'python', text: 'def f():\n    return 1' },
  { type: 'pre', text: 'без языка' },
  { type: 'footer', text: { type: 'reference', name: 'n1', text: '1. Сноска' } },
  { type: 'divider' },
  { type: 'mathematical_expression', expression: '\\int_0^1 x^2\\,dx' },
  {
    type: 'list', items: [
      { blocks: [{ type: 'paragraph', text: 'пункт' }] },
      { blocks: [{ type: 'paragraph', text: 'с вложенным' }, { type: 'list', items: [{ blocks: [{ type: 'paragraph', text: 'второй уровень' }] }] }] },
      { blocks: [{ type: 'paragraph', text: 'отмечено' }], has_checkbox: true, is_checked: true },
      { blocks: [{ type: 'paragraph', text: 'нет' }], has_checkbox: true },
    ],
  },
  { type: 'list', items: [{ blocks: [{ type: 'paragraph', text: 'три' }], type: 'a', value: 3 }, { blocks: [{ type: 'paragraph', text: 'четыре' }], type: 'a', value: 4 }] },
  { type: 'list', items: [{ blocks: [{ type: 'paragraph', text: 'один' }], type: '1', value: 1 }, { blocks: [{ type: 'paragraph', text: 'семь' }], type: 'i', value: 7 }] },
  { type: 'blockquote', blocks: [{ type: 'paragraph', text: 'цитата' }, { type: 'paragraph', text: 'второй абзац' }], credit: 'Автор' },
  { type: 'expandable_blockquote', text: 'длинная\nцитата', credit: { type: 'italic', text: 'Кто-то' } },
  { type: 'pullquote', text: 'акцент', credit: 'Кто' },
  { type: 'collage', blocks: [ph('photo', 1), ph('video', 2)], caption: { text: 'коллаж', credit: 'фото: редакция' } },
  { type: 'slideshow', blocks: [ph('photo', 3), { ...ph('photo', 4), photo: { type: 'photo', media: 'file-4', has_spoiler: true } }], caption: { text: 'слайдшоу' } },
  {
    type: 'table', is_bordered: true, is_striped: true, is_compact: true, caption: 'Подпись', cells: [
      [{ text: 'A', is_header: true, align: 'center', valign: 'middle' }, { text: 'B', is_header: true, align: 'left', valign: 'top' }, { text: 'C', is_header: true, align: 'right', valign: 'bottom' }],
      [{ text: { type: 'bold', text: 'двойная' }, colspan: 2, rowspan: 1, align: 'left', valign: 'middle' }, { align: 'center', valign: 'middle', rowspan: 2 }],
    ],
  },
  { type: 'details', is_open: true, summary: ['Раскрыть ', { type: 'bold', text: 'всё' }], blocks: [{ type: 'paragraph', text: 'внутри' }, { type: 'pre', text: 'a\nb' }] },
  { type: 'details', summary: 'Закрыто', blocks: [{ type: 'paragraph', text: 'скрыто' }] },
  { type: 'map', location: { latitude: 41.9, longitude: 12.5 }, zoom: 14, caption: { text: 'Рим' } },
  { type: 'map', location: { latitude: 55.75, longitude: 37.62 } },
  {
    type: 'buttons', align: 'left', buttons: [
      btn('ссылка', { url: 'https://t.me', style: 'success' }), btn('ответ', { callback_data: 'cb2', style: 'link' }),
      btn('приложение', { web_app: { url: 'https://telegram.org' } }), btn('вход', { login_url: { url: 'https://t.me', forward_text: 'текст', request_write_access: true } }),
      btn('inline', { switch_inline_query: 'q' }), btn('inline 2', { switch_inline_query_current_chat: 'q2' }),
      btn('inline 3', { switch_inline_query_chosen_chat: { query: 'q3', allow_user_chats: true, allow_channel_chats: true } }),
      btn('копия', { copy_text: { text: 'PROMO' } }), btn('нельзя', { disabled: {} }),
    ],
  },
  { type: 'buttons', buttons: [btn('одна', { url: 'https://t.me/telegram' })] },
  { ...ph('photo', 5), caption: { text: 'Фото', credit: 'Автор фото' } },
  ph('video', 6), ph('animation', 7), ph('audio', 8), ph('document', 9), ph('voice_note', 10),
  { type: 'thinking', text: 'Думаю' },
];
// blocks sit in `blocks` arrays and at the top; everything inside text fields is an inline element
const usedTypes = { blocks: new Set(), inline: new Set() };
const walk = (blocks) => (blocks || []).forEach((b) => {
  usedTypes.blocks.add(b.type);
  for (const [k, v] of Object.entries(b)) {
    if (k === 'blocks') walk(v);
    else if (k === 'items') v.forEach((it) => walk(it.blocks));
    else if (k === 'cells') v.flat().forEach((c) => inline(c.text));
    else if (['text', 'credit', 'summary'].includes(k) || (k === 'caption' && b.type === 'table')) inline(v);
    else if (k === 'caption') { inline(v.text); inline(v.credit); }
  }
});
const inline = (v) => {
  if (Array.isArray(v)) v.forEach(inline);
  else if (v && typeof v === 'object') { usedTypes.inline.add(v.type); inline(v.text); }
};
walk(every);
// what HTML cannot say at all is refused below, by name
const noHtml = await page.evaluate(() => Object.keys(/** @type {any} */ (window).HtmlExport.NO_HTML));
const missingBlocks = consts('InputRichBlock').filter((t) => !usedTypes.blocks.has(t) && !noHtml.includes(t));
const missingInline = consts('RichText').filter((t) => !usedTypes.inline.has(t) && !noHtml.includes(t));
check('The all-types post covers every block and text element of the bundled spec', !missingBlocks.length && !missingInline.length,
  `add to «every» in tests/html_export.mjs: блоки ${missingBlocks.join(', ') || '—'}; элементы ${missingInline.join(', ') || '—'}`);
const before = JSON.stringify(every);
const everyOne = await exportsAs(every, await look({ blocks: every }));
const all = everyOne.r;
check('Every block and text element exports, and the HTML looks the same as the blocks', everyOne.ok, everyOne.info);
check('The export works on copies: the blocks are not changed', JSON.stringify(every) === before);
const html = all.html || '';
check('Text is escaped and the preview of the HTML runs nothing',
  html.includes('&lt;не тег&gt; &amp; "кавычки"') && html.includes('<tg-math>a^2 &lt; b &amp; c</tg-math>') && (await rendersScript(html)) === 0, html.slice(0, 400));
check('A line break is <br/> in text and stays a line break in code',
  html.includes('<br/>вторая строка') && html.includes('<pre><code class="language-python">def f():\n    return 1</code></pre>') && html.includes('<pre>a\nb</pre>'));
check('Telegram\'s table defaults are not written, everything else is',
  html.includes('<th>A</th>') && html.includes('<th align="left" valign="top">B</th>') && html.includes('<th align="right" valign="bottom">C</th>')
    && html.includes('<td colspan="2"><b>двойная</b></td>') && html.includes('<table bordered striped compact>'), html.match(/<table[\s\S]*?<\/table>/)?.[0]);
check('Lists keep numbering, type and checkboxes',
  html.includes('<ol start="3" type="a">') && html.includes('<li value="7" type="i">') && html.includes('<li><input type="checkbox" checked>отмечено</li>') && html.includes('<li><input type="checkbox">нет</li>'),
  html.match(/<ol[\s\S]*?<\/ol>/g)?.join(' | '));
check('Buttons carry their action as <tg-button> attributes',
  ['<tg-button-row align="left">', '<tg-button type="url" style="success" url="https://t.me">ссылка</tg-button>', '<tg-button type="callback_data" style="link" data="cb2">',
    '<tg-button type="web_app" url="https://telegram.org">', '<tg-button type="login_url" url="https://t.me" forward-text="текст" request-write-access>',
    '<tg-button type="switch_inline_query" query="q">', '<tg-button type="switch_inline_query_current_chat" query="q2">',
    '<tg-button type="switch_inline_query_chosen_chat" query="q3" allow-user-chats allow-channel-chats>', '<tg-button type="copy_text" text="PROMO">',
    '<tg-button type="disabled">', '<tg-button type="callback_data" data="cb1">в тексте</tg-button>'].every((s) => html.includes(s)), html.match(/<tg-button-row[\s\S]*?<\/tg-button-row>/)?.[0]);
check('Links: e-mail, phone and mention by id are links Telegram reads as such',
  html.includes('<a href="mailto:a@t.me">a@t.me</a>') && html.includes('<a href="tel:+123456789">') && html.includes('<a href="tg://user?id=123456789">Павел</a>')
    && html.includes('<a href="#mid">к якорю</a>') && html.includes('<a href="#">наверх</a>') && html.includes('<a href="#n1"><sup>1</sup></a>') && html.includes('<tg-reference name="n1">'));

// media: one entry per block in rich_message.media, ids that collide with nothing already there
const media = all.media || [];
const ids = media.map((m) => m.id);
check('Every media block gets its own entry in media, the tag points to it',
  media.length === 10 && new Set(ids).size === 10 && html.includes('<img src="tg://photo?id=photo1"/>') && html.includes('<video src="tg://video?id=animation1"></video>')
    && html.includes('<audio src="tg://audio?id=voice1"></audio>') && !/tg:\/\/(animation|voice)/.test(html)
    && media.find((m) => m.id === 'animation1')?.media.type === 'animation' && media.find((m) => m.id === 'voice1')?.media.type === 'voice_note' && html.includes('<tg-document src="tg://document?id=document1"></tg-document>')
    && html.includes('<img src="tg://photo?id=photo3" tg-spoiler/>') && media.find((m) => m.id === 'photo3')?.media.has_spoiler === true
    && html.includes('<figure><img src="tg://photo?id=photo4"/><figcaption>Фото<cite>Автор фото</cite></figcaption></figure>'),
  JSON.stringify(ids) + ' ' + (html.match(/<tg-collage[\s\S]*?<\/tg-slideshow>/)?.[0] || ''));
const had = [{ id: 'photo1', media: { type: 'photo', media: 'другой файл' } }, { id: 'photo2', media: { type: 'photo', media: 'file-5' } }];
const hadBefore = JSON.stringify(had);
const again = await toHtml([ph('photo', 5), ph('photo', 6)], { media: had });
check('Ids already in media are not taken; an entry with the same file is used again; the list given is not changed',
  again.ok && JSON.stringify(had) === hadBefore && again.html === '<img src="tg://photo?id=photo2"/>\n<img src="tg://photo?id=photo3"/>'
    && JSON.stringify(again.media) === JSON.stringify([...had, { id: 'photo3', media: { type: 'photo', media: 'file-6' } }]),
  JSON.stringify(again));
// the outline compares structure and text, not the values of attributes: these are checked here, by the string
check('Attribute values the outline does not compare: link, rowspan, map, tg-time, emoji',
  ['<a href="https://t.me/telegram">ссылка</a>', '<td rowspan="2" align="center"></td>', '<tg-map lat="41.9" long="12.5" zoom="14"></tg-map>',
    '<tg-map lat="55.75" long="37.62"></tg-map>', '<tg-time unix="1798761600" format="wDT">завтра</tg-time>',
    '<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>', '<a href="tg://user?id=123456789">Павел</a>'].every((x) => html.includes(x)),
  html.match(/<tg-map[^>]*>|<tg-time[^>]*>|<tg-emoji[^>]*>|<td rowspan[^>]*>/g)?.join(' '));
check('A post without extra spaces exports without warnings', all.ok && all.warnings?.length === 0, JSON.stringify(all.warnings));
const spaced = await toHtml([{ type: 'heading', size: 3, text: 'ровно' }, { type: 'paragraph', text: ['a', { type: 'bold', text: 'b     c' }] }, { type: 'paragraph', text: 'строка\n  с отступом\tи табом' }, { type: 'pre', text: 'код  как есть' }]);
check('Spaces in a row, tabs and an indent after a line break: exported with a warning that names the place, code is left alone',
  spaced.ok && spaced.html.includes('<b>b     c</b>') && JSON.stringify(spaced.warnings) === JSON.stringify(['блок 2 («Абзац») › «Жирный»', 'блок 3 («Абзац»)']),
  JSON.stringify(spaced));
check('Each block starts on its own line', all.ok && html.startsWith('<h2>Все <b>блоки</b></h2>\n<a name="top"></a>\n<p>'), html.slice(0, 120));

// ---------- 3. what HTML cannot say refuses the whole export ----------
const refused = async (name, blocks, re, opts = {}) => {
  const copy = JSON.stringify(blocks);
  const r = await toHtml(blocks, opts);
  const text = (r.problems || []).join('\n');
  check(name, !r.ok && !r.html && re.every((x) => x.test(text)) && JSON.stringify(blocks) === copy, JSON.stringify(r));
  return r;
};
const many = await refused('Unknown block, field and element: one refusal that lists all of them, the blocks unchanged', [
  { type: 'paragraph', text: 'ок' },
  { type: 'hologram', depth: 3 },
  { type: 'paragraph', text: 'a', color: 'red' },
  { type: 'table', cells: [[{ text: 'a', align: 'left', valign: 'top' }, { text: { type: 'sparkle', text: 'x' }, align: 'left', valign: 'top' }]] },
  { type: 'list', items: [{ blocks: [{ type: 'paragraph', text: { type: 'bold', text: 'b', weight: 900 } }], bullet: '*' }] },
], [/блок 2.*hologram/, /блок 3.*поле color/, /блок 4.*ячейка 1:2.*sparkle/, /блок 5.*пункт 1.*поле bullet/, /блок 5.*пункт 1.*блок 1.*поле weight/]);
check('The refusal lists every problem, not only the first', (many.problems || []).length === 5, JSON.stringify(many.problems));
await refused('Names of Object\'s own properties are not types', [{ type: 'constructor' }, { type: 'paragraph', text: { type: '__proto__', text: 'x' } }, { type: 'paragraph', text: { text: 'без type' } }],
  [/блок 1.*«constructor» не переносится/, /блок 2.*«__proto__» не переносится/, /блок 3.*без type/]);
{
  // Telegram finds a TON address in HTML text by itself (captured html-detected-card-ton): it is written as its text
  const ton = 'UQBvW8Z5huBkMJYdnfAEM5JqTNkuWX3diqYENkWsIL0XggGG';
  const r = await toHtml([{ type: 'paragraph', text: ['Кошелёк ', { type: 'ton_address', text: ton, address: ton }] }]);
  check('A TON address is written as its text, Telegram finds it there', r.ok && r.html === `<p>Кошелёк ${ton}</p>`, JSON.stringify(r));
  await refused('A TON address shown as other text is refused: Telegram finds it only by the text', [{ type: 'paragraph', text: { type: 'ton_address', text: 'UQBv…ggGG', address: ton } }], [/«Адрес TON».*не совпадает/]);
}
check('Every type HTML cannot say is refused by name in this file (none now)', noHtml.length === 0, 'add a refusal check for: ' + noHtml.join(', '));
{
  // with &quot; in the address Telegram drops the link (captured html-href-quot); %22 keeps it (html-href-pct22)
  const quoted = [{ type: 'paragraph', text: ['Поиск ', { type: 'url', text: 'по фразе', url: 'https://example.com/?q="x y"' }] }];
  const { ok, r, info } = await exportsAs(quoted, await look({ blocks: quoted }));
  check('A " in a link address is written as %22, not &quot;', ok && r.html === '<p>Поиск <a href="https://example.com/?q=%22x y%22">по фразе</a></p>', info);
  // in a button Telegram refuses the whole post for it (BUTTON_URL_INVALID)
  const b = await toHtml([{ type: 'buttons', buttons: [btn('кнопка', { url: 'https://t.me/?q="x"' }), btn('приложение', { web_app: { url: 'https://telegram.org/?q="y"' } })] }]);
  check('A " in a button address is written as %22 too', b.ok && b.html.includes('url="https://t.me/?q=%22x%22"') && b.html.includes('url="https://telegram.org/?q=%22y%22"') && !b.html.includes('&quot;'), JSON.stringify(b));
}
{
  // Telegram finds a card number in HTML text only without spaces (4242424242424242 yes, 4242 4242 4242 4242 no):
  // the export keeps the author's text and says so, written without the spaces it would read otherwise than the blocks
  const card = (text) => toHtml([{ type: 'paragraph', text: ['Карта ', { type: 'bank_card_number', text, bank_card_number: '4242424242424242' }] }]);
  const spaced = await card('4242 4242 4242 4242');
  const bare = await card('4242424242424242');
  check('A card number with spaces exports as written, with a note that Telegram will not find it',
    spaced.ok && spaced.html === '<p>Карта 4242 4242 4242 4242</p>' && spaced.notes.length === 1 && /без пробелов/.test(spaced.notes[0]) && /блок 1/.test(spaced.notes[0])
      && bare.ok && bare.html === '<p>Карта 4242424242424242</p>' && bare.notes.length === 0, JSON.stringify({ spaced, bare }));
}
await refused('A map size has no HTML: refused, not dropped', [{ type: 'map', location: { latitude: 1, longitude: 2, heading: 90 }, width: 300 }], [/поле width/, /поле heading/]);
await refused('A login button with bot_username has no HTML attribute: refused', [{ type: 'buttons', buttons: [btn('вход', { login_url: { url: 'https://t.me', bot_username: 'other_bot' } })] }], [/кнопка 1.*поле bot_username/]);
await refused('A button with two actions is refused', [{ type: 'buttons', buttons: [btn('две', { url: 'https://t.me', callback_data: 'x' })] }], [/кнопка 1.*несколько действий/]);
await refused('A date without text is refused: Telegram drops an empty <tg-time>', [{ type: 'paragraph', text: { type: 'date_time', text: '', unix_time: 1, date_time_format: 'r' } }], [/без текста/]);
await refused('A heading size outside 1–6 is refused', [{ type: 'heading', size: 9, text: 'x' }], [/размер 9/]);
await refused('A mention whose text is not @username is refused: Telegram finds it in HTML only by the text', [{ type: 'paragraph', text: { type: 'mention', text: 'Павел', username: 'durov' } }], [/@durov/]);
await refused('Found-by-text elements are refused when entity detection is off', [{ type: 'paragraph', text: { type: 'hashtag', text: '#тег', hashtag: 'тег' } }], [/skip_entity_detection|Не распознавать/], { skipEntities: true });
await refused('A link Telegram would read as something else in HTML is refused', [{ type: 'paragraph', text: { type: 'url', text: 'почта', url: 'mailto:a@t.me' } }], [/mailto/]);
await refused('A caption that is not RichBlockCaption is refused', [{ type: 'photo', photo: { type: 'photo', media: 'x' }, caption: 'просто текст' }], [/подпись/]);
await refused('A pre language Telegram cannot take is refused', [{ type: 'pre', language: 'c" onclick="x', text: 'x' }], [/язык/]);
// two ways of writing the same footnote name for a reference and an anchor: the preview of the HTML shows the link
// otherwise, and the check against the blocks catches it
const clash = await toHtml([{ type: 'paragraph', text: [{ type: 'anchor_link', text: 'к якорю', anchor_name: 'x' }] }, { type: 'anchor', name: 'x' }, { type: 'paragraph', text: { type: 'reference', name: 'x', text: 'сноска' } }]);
check('HTML that the preview draws otherwise than the blocks is refused with both outlines', !clash.ok && !!clash.diff && /иначе/.test((clash.problems || []).join(' ')), JSON.stringify(clash));
const empty = await toHtml([]);
check('An empty post exports to empty HTML', empty.ok && empty.html === '' && JSON.stringify(empty.media) === '[]', JSON.stringify(empty));

check('No errors in the page', pageErrors.length === 0, pageErrors.join(' | '));
await browser.close();
const failed = results.filter((x) => !x).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
