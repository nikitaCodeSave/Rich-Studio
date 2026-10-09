// Browser checks for the editor. Needs a running studio (BASE, default http://localhost:8080)
// pointed at the fake Bot API from tests/mock_telegram.py (MOCK, default http://localhost:8081),
// and Playwright: `npm i playwright && npx playwright install chromium`.
// Run: node tests/e2e_ui.mjs
import { mkdirSync, readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const BASE = process.env.BASE || 'http://localhost:8080';
const MOCK = process.env.MOCK || 'http://localhost:8081';
const mockLog = async () => (await fetch(MOCK + '/log')).json();
const push = (u) => fetch(MOCK + '/push', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(u) }).then((r) => r.json());
const results = [];
const check = (name, ok, info = '') => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${info ? '  ' + info : ''}`); };

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
const csp = [];
let alerts = 0;
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => {
  const t = m.text();
  if (/Content Security Policy/i.test(t)) csp.push(t);
  else if (m.type() === 'error' && !/Failed to load resource|tile\.openstreetmap/.test(t)) errors.push(t);
});
page.on('dialog', (d) => { if (d.type() === 'alert') alerts++; d.accept(); });
await page.goto(BASE);
await page.waitForSelector('.block');
const state = () => page.evaluate(() => JSON.parse(document.querySelector('#src-json').value));
const showJson = () => page.click('#mode-tabs [data-mode=json]');
// the draft's channels live in a hidden field; the header button and the «Каналы» dialog edit it
const setChat = (v) => page.evaluate((x) => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = x; i.dispatchEvent(new Event('input')); }, v);
const applyJson = async (post) => { await showJson(); await page.fill('#src-json', JSON.stringify(post)); await page.click('#json-apply'); await page.waitForTimeout(400); };

// 1. hostile HTML does not execute and is cleaned up in the preview
const evil = [
  '<a id="x1" href="java&#9;script:alert(1)">tab</a>',
  '<svg><a xlink:href="javascript:alert(2)"><text>svg</text></a><animate attributeName="href" to="javascript:alert(3)"/></svg>',
  '<img src=x onerror="alert(4)">', '<p style="position:fixed;inset:0;background:red">style</p>',
  '<map><area href="javascript:alert(5)"></map>', '<iframe srcdoc="<script>alert(6)</script>"></iframe>',
  '<svg><style>body{display:none}</style></svg>', '<tg-button type="url" url="javascript:alert(7)">b</tg-button>',
  '<form action="javascript:alert(8)"><button>f</button></form>', '<a href="data:text/html,<script>alert(9)</script>">d</a>',
].join('\n');
await page.click('#mode-tabs [data-mode=html]');
await page.fill('#src-html', evil);
await page.waitForTimeout(700);
await page.evaluate(() => document.querySelector('#preview').shadowRoot.querySelectorAll('a, .tg-btn').forEach((a) => a.click()));
await page.waitForTimeout(500);
const leftovers = await page.evaluate(() => {
  const p = document.querySelector('#preview').shadowRoot;
  const bad = [];
  p.querySelectorAll('*').forEach((n) => {
    if (n.namespaceURI !== 'http://www.w3.org/1999/xhtml') bad.push('svg:' + n.localName);
    for (const a of n.attributes) {
      if (/^on/i.test(a.name) || a.name === 'style' && !n.closest('.katex') && !n.matches('td,th,.tg-map,.tg-map-tiles') || /javascript:|data:text/i.test(a.value)) bad.push(n.localName + '[' + a.name + ']');
    }
    if (['iframe', 'form', 'area', 'style', 'script'].includes(n.localName)) bad.push(n.localName);
  });
  if (!p.querySelector('.tg-bubble')) bad.push('no preview rendered');
  return bad;
});
check('XSS: hostile HTML is neutralised in the preview', alerts === 0 && leftovers.length === 0, leftovers.join(', '));
const rtUrl = { rich_message: { blocks: [{ type: 'paragraph', text: { type: 'url', text: 'клик', url: 'javascript:alert(10)' } }] } };
await applyJson(rtUrl);
await page.click('#mode-tabs [data-mode=blocks]');
const href = await page.evaluate(() => document.querySelector('#preview').shadowRoot.querySelector('a')?.getAttribute('href'));
check('XSS: javascript: URL in a blocks link is dropped', !href, String(href));

// 2. clicking another mode tab does not lose the blocks after autosave + reload
await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Сохранить меня' }] } });
await page.click('#mode-tabs [data-mode=blocks]');
await page.click('#btn-save');
await page.waitForTimeout(500);
await page.click('#mode-tabs [data-mode=html]');
await page.waitForTimeout(3200); // autosave
await page.reload();
await page.waitForSelector('#mode-tabs');
await page.waitForTimeout(800);
await page.click('#mode-tabs [data-mode=blocks]');
await page.waitForTimeout(300);
const kept = await page.evaluate(() => document.querySelector('#pane-blocks .rte') && document.querySelector('#pane-blocks .rte').textContent);
check('Drafts keep the text of every mode', kept === 'Сохранить меня', String(kept));

// 3. table with colspan is not padded just by opening it
const span = { rich_message: { blocks: [{ type: 'table', cells: [[{ text: 'Шапка', colspan: 2, is_header: true, align: 'center', valign: 'top' }], [{ text: 'a', align: 'left', valign: 'top' }, { text: 'b', align: 'left', valign: 'top' }]] }] } };
await applyJson(span);
await page.click('#mode-tabs [data-mode=blocks]');
await page.waitForTimeout(300);
await showJson();
const t = (await state()).rich_message.blocks[0].cells;
check('Table with colspan keeps its shape', t[0].length === 1 && t[1].length === 2, JSON.stringify(t.map((r) => r.length)));

// 4. rich text round trip: trailing newline and unknown fields survive an edit
await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: ['строка\n', { type: 'bold', text: 'жирный', future: 1 }, '\n'] }] } });
await page.click('#mode-tabs [data-mode=blocks]');
const ed = page.locator('#pane-blocks .rte').first();
await ed.click();
await page.evaluate(() => { const e = document.querySelector('#pane-blocks .rte'); const r = document.createRange(); r.setStart(e.firstChild, 0); r.collapse(true); getSelection().removeAllRanges(); getSelection().addRange(r); });
await page.keyboard.type('Новая ');
await page.waitForTimeout(400);
await showJson();
const txt = (await state()).rich_message.blocks[0].text;
check('Rich text keeps newlines and unknown fields', JSON.stringify(txt) === JSON.stringify(['Новая строка\n', { type: 'bold', text: 'жирный', future: 1 }, '\n']), JSON.stringify(txt));

// 5. dialogs: Enter in a field does not discard, nested dialogs do not clobber each other
await page.click('#mode-tabs [data-mode=blocks]');
await page.locator('#pane-blocks .rte').first().click();
await page.selectOption('.toolbar select', { label: 'Кнопка в тексте' });
await page.waitForSelector('dialog[open]');
await page.locator('dialog[open] select.add-field').first().selectOption('url');
const urlInput = page.locator('dialog[open] input[type=text]').first();
await urlInput.click();
await page.keyboard.press('Enter');
const stillOpen = await page.locator('dialog[open]').count();
await page.locator('dialog[open] .rte').first().click();
await page.selectOption('.toolbar select', { label: 'Дата и время' });
await page.waitForTimeout(300);
const stacked = await page.locator('dialog[open]').count();
await page.locator('dialog[open]').last().locator('button:has-text("Готово")').click();
await page.waitForTimeout(200);
const afterInner = await page.locator('dialog[open]').count();
await page.locator('dialog[open]').last().locator('button:has-text("Готово")').click();
await page.waitForTimeout(400);
await showJson();
const para = JSON.stringify((await state()).rich_message.blocks[0].text);
check('Dialogs: Enter keeps it open, nested dialog stacks and both save', stillOpen === 1 && stacked === 2 && afterInner === 1 && /"type":"button"/.test(para) && /date_time/.test(para), `${stillOpen}/${stacked}/${afterInner}`);

// 6. formatting toolbar stays available when moving between fields
await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'один' }, { type: 'paragraph', text: 'два' }] } });
await page.click('#mode-tabs [data-mode=blocks]');
await page.locator('#pane-blocks .rte').nth(0).click();
await page.locator('#pane-blocks .rte').nth(1).click();
await page.waitForTimeout(400);
check('Toolbar stays visible when switching fields', await page.locator('.toolbar.on').count() === 1);

// 7. options cannot redirect a post to another chat
const r = await page.evaluate(async () => (await fetch('/api/send', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ post: { rich_message: { html: '<p>x</p>' }, options: { chat_id: '@other' } } }) })).status);
check('options.chat_id is refused', r === 400, String(r));

// 8. the standalone render page works under its CSP
const d = await page.evaluate(async () => (await fetch('/api/drafts', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'r', post: { rich_message: { markdown: '# Заголовок\n\n<tg-math>e^{i\\\\pi}+1=0</tg-math>' } } }) })).json());
const rp = await browser.newPage();
const rpErrors = [];
rp.on('console', (m) => { if (m.type() === 'error') rpErrors.push(m.text()); });
rp.on('pageerror', (e) => rpErrors.push(e.message));
await rp.goto(`${BASE}/render/draft/${d.id}?theme=dark`);
await rp.waitForSelector('.tg-bubble h1');
check('Render page draws under CSP', rpErrors.length === 0 && await rp.locator('.katex').count() === 1, rpErrors.join(' | '));


// 9. several channels from the header field
await applyJson({ rich_message: { html: '<p>Кросспост</p>' } });
await setChat('@chan_a, @chan_b');
await page.click('#btn-publish');
await page.waitForTimeout(800);
const sentTo = (await mockLog()).filter((c) => c.method === 'sendRichMessage').slice(-2).map((c) => c.body.chat_id);
check('Publishing to two channels from one draft', JSON.stringify(sentTo) === '["@chan_a","@chan_b"]', JSON.stringify(sentTo));
await setChat('@my_channel');

// 10. pin + first comment + callback answer, then a click arrives through getUpdates
await applyJson({ rich_message: { html: '<p>Голосуем</p>' }, reply_markup: { inline_keyboard: [[{ text: '👍', callback_data: 'like' }]] } });
await page.click('#panel-kb summary');
await page.fill('.cb-answer input[type=text]', 'Спасибо, учтено');
await page.click('#panel-after summary');
await page.check('#after input[type=checkbox]');
await page.fill('#comment-src', '<p>Обсуждение тут 👇</p>');
await page.waitForTimeout(400);
const commentShown = await page.locator('#preview .tg-comment-label').count();
await page.click('#btn-publish');
await page.waitForTimeout(800);
let log = await mockLog();
const sentRich = log.filter((c) => c.method === 'sendRichMessage').pop();
const pinned = log.filter((c) => c.method === 'pinChatMessage').pop();
await push({ callback_query: { id: 'cq-e2e', data: 'like', message: { message_id: 1, chat: { id: -1001 } } } }); // unknown message: answered, not counted
const posts = await page.evaluate(() => fetch('/api/posts').then((r) => r.json()));
const mine = posts.find((p) => p.comment_status === 'waiting');
await push({ callback_query: { id: 'cq-e2e2', data: 'like', message: { message_id: mine && (await page.evaluate((id) => fetch('/api/posts/' + id).then((r) => r.json()), mine.id)).message_id, chat: { id: -1001 } } } });
await page.waitForTimeout(3500);
log = await mockLog();
const answer = log.filter((c) => c.method === 'answerCallbackQuery').pop();
await page.click('#side-tabs [data-tab=posts]');
await page.waitForTimeout(500);
const statsText = await page.locator('#posts li').first().textContent();
check('Pin, first comment and callback answer work end to end',
  commentShown === 1 && !sentRich.body.pin && !sentRich.body.comment && !!pinned && answer && answer.body.text === 'Спасибо, учтено' && /нажатия: like 1/.test(statsText) && /ждёт пересылки/.test(statsText),
  JSON.stringify({ commentShown, pinned: !!pinned, answer: answer && answer.body, statsText }));

// 11. poll dialog
await page.click('#btn-poll');
await page.waitForSelector('dialog[open]');
const dlg = page.locator('dialog[open]');
await dlg.locator('input[type=text]').nth(0).fill('Когда эфир?');
await dlg.locator('input[type=text]').nth(1).fill('Утром');
await dlg.locator('input[type=text]').nth(2).fill('Вечером');
await dlg.locator('label:has-text("старше 24 часов") input').check();
await dlg.locator('input[placeholder="пусто = все"]').fill('ru, kz');
await dlg.locator('button:has-text("Отправить")').click();
await page.waitForTimeout(800);
const pollCall = (await mockLog()).filter((c) => c.method === 'sendPoll').pop();
const pb = pollCall && pollCall.body;
const pollRow = await page.locator('#posts li').first().textContent();
check('Poll dialog sends a channel poll', pb && pb.question === 'Когда эфир?' && pb.members_only === true && JSON.stringify(pb.country_codes) === '["RU","KZ"]' && pb.options.length === 2 && /опрос/.test(pollRow) && await page.locator('dialog[open]').count() === 0,
  JSON.stringify(pb) + ' | ' + pollRow);

// 12a. undo/redo for the whole post: buttons and Ctrl+Z outside text fields
const previewText = () => page.locator('#preview .tg-preview').textContent();
await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Версия один' }] } });
await page.waitForTimeout(500);
await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Версия два' }] } });
await page.waitForTimeout(700);
await page.evaluate(() => /** @type {HTMLElement} */ (document.activeElement).blur());
const undoEnabled = await page.locator('#btn-undo').isEnabled();
await page.keyboard.press('Control+z');
await page.waitForTimeout(400);
const afterUndo = await previewText();
const redoEnabled = await page.locator('#btn-redo').isEnabled();
await page.click('#btn-redo');
await page.waitForTimeout(400);
const afterRedo = await previewText();
await page.click('#btn-undo');
await page.waitForTimeout(400);
const afterButtonUndo = await previewText();
check('Undo and redo restore the whole post', undoEnabled && redoEnabled && /Версия один/.test(afterUndo) && /Версия два/.test(afterRedo) && /Версия один/.test(afterButtonUndo) && await page.locator('#save-state').textContent() === 'есть изменения',
  JSON.stringify({ undoEnabled, redoEnabled, afterUndo, afterRedo, afterButtonUndo }));

// 12b. <rs-preview> keeps the post in a shadow root; in-post anchor links still scroll to their target
const filler = Array.from({ length: 40 }, (_, i) => ({ type: 'paragraph', text: 'Строка ' + i }));
await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: { type: 'anchor_link', anchor_name: 'end', text: 'вниз' } }, ...filler, { type: 'anchor', name: 'end' }, { type: 'paragraph', text: 'Конец' }] } });
await page.waitForTimeout(300);
const scrollBefore = await page.evaluate(() => document.querySelector('.preview-col').scrollTop);
await page.locator('#preview a.tg-link').click();
await page.waitForTimeout(800);
const scrollAfter = await page.evaluate(() => document.querySelector('.preview-col').scrollTop);
const isolated = await page.evaluate(() => !!document.querySelector('#preview').shadowRoot && !document.querySelector('.tg-bubble') && !location.hash);
check('Preview is isolated in a shadow root and anchor links scroll inside it', isolated && scrollAfter > scrollBefore + 100, JSON.stringify({ isolated, scrollBefore, scrollAfter }));

// 12c. the spec can be refreshed on demand; a failed download is reported, not hidden (the dev studio has no network spec)
await page.click('#spec-refresh');
await page.waitForSelector('.toast.err');
const refreshToast = await page.locator('.toast.err').last().textContent();
const badgeWarn = await page.locator('#spec-badge.warn').count();
await page.click('#spec-badge');
await page.waitForSelector('dialog[open]');
const specDialog = await page.locator('dialog[open]').textContent();
await page.locator('dialog[open] button:has-text("Закрыть")').click();
check('Spec refresh button reports the result and what the editor lacks',
  /Не удалось скачать спецификацию/.test(refreshToast) && badgeWarn === 1 && /Последняя проверка: .*скачать не удалось/.test(specDialog) && /знают все блоки/.test(specDialog),
  JSON.stringify({ refreshToast, badgeWarn, specDialog: specDialog.slice(0, 300) }));

// 12. MCP App: the ui:// preview page renders a preview_post result inside a sandboxed host frame
const mcp = async (method, params) => (await (await fetch(BASE + '/mcp', {
  method: 'POST',
  headers: { 'content-type': 'application/json', accept: 'application/json, text/event-stream', 'mcp-protocol-version': '2025-06-18' },
  body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
})).json()).result;
const appPost = { rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Из чата' }, { type: 'paragraph', text: ['Привет, ', { type: 'bold', text: 'мир' }] }] } };
const toolResult = await mcp('tools/call', { name: 'preview_post', arguments: { post: appPost } });
const appHtml = (await mcp('resources/read', { uri: 'ui://rich-studio/preview' })).contents[0].text;
// the sandbox a host gives the view: inline code only, no network
const hostCsp = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'";
const host = await browser.newPage();
const appErrors = [];
host.on('pageerror', (e) => appErrors.push(e.message));
host.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|font/i.test(m.text())) appErrors.push(m.text()); });
await host.setContent('<iframe id="app" sandbox="allow-scripts" style="width:420px;height:300px;border:0"></iframe>');
const hostLog = await host.evaluate(({ html, csp, result }) => new Promise((resolve) => {
  const frame = document.getElementById('app');
  const log = [];
  window.addEventListener('message', (e) => {
    if (e.source !== frame.contentWindow) return;
    const m = e.data;
    log.push(m.method || 'response');
    const reply = (msg) => frame.contentWindow.postMessage({ jsonrpc: '2.0', ...msg }, '*');
    if (m.method === 'ui/initialize') {
      reply({ id: m.id, result: { protocolVersion: '2025-06-18', hostInfo: { name: 'e2e', version: '1' }, hostCapabilities: {}, hostContext: { theme: 'dark' } } });
    } else if (m.method === 'ui/notifications/initialized') {
      reply({ method: 'ui/notifications/tool-result', params: result });
    } else if (m.method === 'ui/notifications/size-changed' && log.includes('ui/notifications/initialized') && m.params.height > 0) {
      resolve({ log, size: m.params });
    }
  });
  frame.srcdoc = html.replace('<head>', `<head><meta http-equiv="Content-Security-Policy" content="${csp}">`);
  setTimeout(() => resolve({ log, timeout: true }), 5000);
}), { html: appHtml, csp: hostCsp, result: toolResult });
const appFrame = host.frames().find((f) => f !== host.mainFrame());
const appView = await appFrame.evaluate(() => ({
  dark: document.getElementById('root').classList.contains('tg-dark'),
  bubble: document.querySelectorAll('.tg-bubble').length,
  text: document.getElementById('root').textContent,
  notes: document.getElementById('notes').textContent,
}));
check('MCP App preview renders the tool result in a sandboxed frame',
  !hostLog.timeout && hostLog.log[0] === 'ui/initialize' && hostLog.size.height < 300 && appView.dark && appView.bubble > 0 && /Из чата/.test(appView.text) && /мир/.test(appView.text) && !appView.notes && appErrors.length === 0,
  JSON.stringify({ hostLog, appView, appErrors }));
await host.close();

// 13. the header stays one row on a wide screen: «Опубликовать» is not pushed to a second line
await page.setViewportSize({ width: 1440, height: 900 });
await page.fill('#title', 'Довольно длинное название черновика');
const header = await page.evaluate(() => {
  const at = (sel) => document.querySelector(sel).getBoundingClientRect();
  return { pub: Math.round(at('#btn-publish').top), brand: Math.round(at('.brand').top), right: Math.round(at('#btn-publish').right), width: innerWidth };
});
check('Header fits one row at 1440px', Math.abs(header.pub - header.brand) < 12 && header.right <= header.width, JSON.stringify(header));

// 13b. the health badge in the header follows /api/health and opens the list of checks
{
  await page.waitForFunction(() => { const b = document.querySelector('#health')?.shadowRoot?.querySelector('button'); return b && !b.classList.contains('unknown'); }, null, { timeout: 8000 }).catch(() => {});
  const want = await page.evaluate(async () => (await fetch('/api/health')).json());
  const badge = await page.evaluate(() => { const b = document.querySelector('#health').shadowRoot.querySelector('button'); return { cls: b.className, text: b.textContent.trim(), title: b.title }; });
  await page.click('#health');
  const dlg = await page.waitForSelector('dialog.dlg', { timeout: 3000 }).then((d) => d.textContent()).catch(() => '');
  await page.keyboard.press('Escape');
  check('Health badge shows /api/health and lists the checks',
    badge.cls === want.status && badge.text === (want.problems.length ? String(want.problems.length) : '') && want.problems.every((p) => badge.title.includes(p))
      && want.checks.every((c) => dlg.includes(c.name)), JSON.stringify({ badge, status: want.status, dlg: dlg.slice(0, 200) }));
}

// 13c. the badge with problems, a studio that does not answer, and one that wants the key (no prompt from polling)
{
  const read = () => page.evaluate(() => { const b = document.querySelector('#health').shadowRoot.querySelector('button'); return { cls: b.className, text: b.textContent.trim() }; });
  const poll = () => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  const settle = (cls) => page.waitForFunction((c) => document.querySelector('#health').shadowRoot.querySelector('button').className === c, cls, { timeout: 5000 }).catch(() => {});
  let prompts = 0;
  const onDialog = (d) => { prompts++; d.dismiss(); };
  page.on('dialog', onDialog);
  await page.route('**/api/health', async (route) => {
    const r = await (await route.fetch()).json();
    r.status = 'warn'; r.problems = ['posts: 1 постов требуют внимания', 'backup: свежей копии нет'];
    route.fulfill({ json: r });
  });
  await poll(); await settle('warn');
  const warn = await read();
  await page.unroute('**/api/health');
  await page.route('**/api/health', (route) => route.abort());
  await poll(); await settle('down');
  const down = await read();
  await page.click('#health');
  const downDlg = await page.waitForSelector('dialog.dlg', { timeout: 3000 }).then((d) => d.textContent()).catch(() => '');
  await page.keyboard.press('Escape');
  await page.unroute('**/api/health');
  await page.route('**/api/health', (route) => route.fulfill({ status: 401, json: { detail: 'нужен ключ' } }));
  await poll(); await settle('locked');
  const locked = await read();
  await page.unroute('**/api/health');
  page.off('dialog', onDialog);
  await poll(); await settle('ok');
  check('Health badge: problems counted, down shows no stale report, 401 never prompts',
    warn.cls === 'warn' && warn.text === '2' && down.cls === 'down' && down.text === '' && /не ответила/.test(downDlg) && !/в порядке/.test(downDlg)
      && locked.cls === 'locked' && prompts === 0, JSON.stringify({ warn, down, downDlg: downDlg.slice(0, 120), locked, prompts }));
}

// 14. a new post becomes a draft without Ctrl+S; a draft saved elsewhere meanwhile is not overwritten
{
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await applyJson({ rich_message: { markdown: 'Сохранюсь сам' } });
  await page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 8000 }).catch(() => {});
  const autoId = await page.evaluate(() => JSON.parse(localStorage.getItem('rs.lastDraft') || 'null'));
  const autoDraft = autoId ? await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId) : null;
  check('A new post is saved as a draft on its own', !!autoDraft && autoDraft.payload.rich_message.markdown === 'Сохранюсь сам', String(autoId));
  // "another tab" saves the same draft, then this tab keeps typing
  await page.evaluate(async (id) => fetch('/api/drafts/' + id, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'из другой вкладки', post: { rich_message: { markdown: 'Чужая правка' } } }) }), autoId);
  await applyJson({ rich_message: { markdown: 'Моя правка' } });
  await page.waitForSelector('dialog[open] >> text=Черновик изменился', { timeout: 8000 }).catch(() => {});
  const conflictShown = await page.locator('dialog[open] >> text=Черновик изменился').count();
  const untouched = await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId);
  await page.click('dialog[open] >> text=Сохранить мою поверх').catch(() => {});
  await page.waitForTimeout(500);
  const mine = await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId);
  check('A draft changed in another tab is not overwritten silently',
    conflictShown === 1 && untouched.payload.rich_message.markdown === 'Чужая правка' && mine.payload.rich_message.markdown === 'Моя правка',
    JSON.stringify({ conflictShown, untouched: untouched.payload.rich_message, mine: mine.payload.rich_message }));
}

// 15. «Править» opens a published post with a bar over the editor; «Обновить пост» replaces it and keeps history
{
  const sent = await page.evaluate(async () => (await fetch('/api/send', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: '@edit_me', post: { rich_message: { markdown: 'Первая версия' } } }) })).json());
  const mid = sent.message.message_id;
  await page.click('#side-tabs [data-tab=drafts]');
  await page.click('#side-tabs [data-tab=posts]');
  await page.locator('#posts li', { hasText: '#' + mid }).getByRole('button', { name: 'Править' }).click();
  await page.waitForSelector('#edit-bar >> text=Правите пост', { timeout: 5000 }).catch(() => {});
  const barText = await page.locator('#edit-bar').textContent().catch(() => '') || await page.evaluate(() => document.querySelector('#edit-bar').shadowRoot.textContent);
  await applyJson({ rich_message: { markdown: 'Вторая версия' } });
  await page.click('#edit-bar >> text=Обновить пост');
  await page.waitForTimeout(600);
  const edits = (await mockLog()).filter((c) => c.method === 'editMessageText' && c.body.message_id === mid);
  await page.click('#edit-bar >> text=История');
  await page.waitForSelector('dialog[open] >> text=История поста');
  const history = await page.locator('dialog[open] li').allTextContents();
  await page.keyboard.press('Escape');
  check('Editing a published post: bar, update in place, history',
    /#\d+ в @edit_me/.test(barText) && edits.length === 1 && edits[0].body.rich_message.markdown === 'Вторая версия' && history.length === 2 && /До замены/.test(history[1]),
    JSON.stringify({ barText: barText.replace(/\s+/g, ' ').trim(), edits: edits.length, history }));
}

// 15b. while a post is open for editing, «Опубликовать» posts a new copy on purpose (as_new), never a 409
{
  const before = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  await page.click('#btn-publish');
  await page.waitForTimeout(1200);
  const after = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  const errs = await page.locator('.toast.err').allTextContents();
  check('Publishing while editing posts a new copy on purpose', after === before + 1 && !errs.length, JSON.stringify({ before, after, errs }));
}

// 16. the Files tab downloads a backup zip
{
  await page.click('#side-tabs [data-tab=uploads]');
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }), page.click('#export')]);
  const name = dl.suggestedFilename();
  check('Backup downloads as a zip', /^rich-studio-\d{8}-\d{4}\.zip$/.test(name), name);
}

// 17. after «Мне в личку» the preview is checked against the blocks Telegram made of the post
{
  const captured = JSON.parse(readFileSync(new URL('telegram_parse/captured.json', import.meta.url), 'utf8'));
  const barText = () => page.evaluate(() => document.querySelector('#parse-check').shadowRoot.textContent.replace(/\s+/g, ' ').trim());
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  // the mock answers with the blocks the real Telegram made of this source
  await page.click('#mode-tabs [data-mode=markdown]');
  await page.fill('#src-markdown', captured['md-math'].source);
  await page.waitForTimeout(400);
  await page.click('#btn-me');
  await page.waitForSelector('#parse-check >> text=так же', { timeout: 5000 }).catch(() => {});
  const same = await barText();
  await page.click('#parse-check >> text=Как разобрал Telegram');
  await page.waitForTimeout(300);
  const showsTelegram = await page.locator('#parse-check button[aria-pressed=true]').count();
  await page.screenshot({ path: 'test-results/ui/parse-check-1400.png' });
  await page.click('#parse-check >> text=В визуальный');
  await page.waitForTimeout(500);
  const visual = await page.locator('#mode-tabs [data-mode=blocks].on').count();
  await page.screenshot({ path: 'test-results/ui/parse-to-blocks-1400.png' });
  await showJson();
  const blocks = (await state()).rich_message.blocks;
  await page.click('#mode-tabs [data-mode=markdown]');
  const sourceKept = await page.inputValue('#src-markdown');
  // a source the mock does not know comes back as one plain paragraph: Telegram "parsed it otherwise"
  await page.fill('#src-markdown', '**жирный** текст');
  await page.waitForTimeout(400);
  const stale = await barText();
  await page.click('#btn-me');
  await page.waitForSelector('#parse-check >> text=иначе', { timeout: 5000 }).catch(() => {});
  const diff = await barText();
  await page.screenshot({ path: 'test-results/ui/parse-check-diff-1400.png' });
  check('«Мне в личку» checks the preview against Telegram and turns Markdown into visual blocks',
    /так же/.test(same) && showsTelegram === 1 && visual === 1 && Array.isArray(blocks) && blocks.some((b) => b.type === 'mathematical_expression') && sourceKept === captured['md-math'].source
      && /изменился/.test(stale) && /иначе/.test(diff) && /Telegram:/.test(diff),
    JSON.stringify({ same, showsTelegram, visual, blocks, sourceKept, stale, diff }).slice(0, 900));
}

// 8. channels: a checked channel shows its title, the draft remembers its channel
{
  await page.click('#btn-new');
  await page.waitForTimeout(400);
  await page.fill('#title', 'Канал черновика');
  await page.click('#btn-check');
  await page.locator('dialog[open] input[placeholder="@канал, @другой"]').fill('@kept_chan');
  await page.keyboard.press('Tab');
  await page.waitForTimeout(500);
  await page.locator('dialog[open] .channel', { hasText: '@kept_chan' }).locator('button', { hasText: 'Проверить' }).click();
  await page.waitForTimeout(600);
  const dialogText = await page.locator('dialog[open] .channels').textContent();
  await page.keyboard.press('Escape');
  await page.click('#btn-save');
  await page.waitForTimeout(500);
  const keptId = await page.evaluate(() => localStorage.getItem('rs.lastDraft'));
  const header = await page.evaluate(() => document.querySelector('#preview').shadowRoot.textContent);
  const label = await page.locator('#btn-check').textContent();
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  const fresh = await page.inputValue('#chat');
  await page.click('#side-tabs [data-tab=drafts]');
  await page.locator('#drafts li', { hasText: 'Канал черновика' }).first().click();
  await page.waitForTimeout(500);
  const reopened = await page.inputValue('#chat');
  const stored = await page.evaluate((id) => fetch('/api/drafts/' + JSON.parse(id)).then((r) => r.json()), keptId);
  check('A checked channel shows its title and the draft keeps its channel',
    /Тест/.test(dialogText) && /публикация/.test(dialogText) && /Тест/.test(header) && label === 'Тест ▾' && fresh === '@test' && reopened === '@kept_chan' && JSON.stringify(stored.chat_ids) === '["@kept_chan"]',
    JSON.stringify({ dialogText: dialogText.slice(0, 120), label, fresh, reopened, stored: stored.chat_ids }));
}

check('No CSP violations or JS errors in the editor', errors.length === 0 && csp.length === 0, [...errors, ...csp].join(' | '));
// screenshots of the editor for a person to look at (CI keeps them as an artifact): a PR that changes the UI shows them
mkdirSync('test-results/ui', { recursive: true });
const shot = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await shot.goto(BASE);
await shot.waitForSelector('.block');
await shot.screenshot({ path: 'test-results/ui/editor-1400.png' });
await shot.setViewportSize({ width: 390, height: 844 });
await shot.screenshot({ path: 'test-results/ui/editor-390.png', fullPage: true });

await browser.close();
const failed = results.filter((x) => !x.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
