// Browser checks for the editor. Needs a running studio (BASE, default http://localhost:8080)
// pointed at the fake Bot API from tests/mock_telegram.py (MOCK, default http://localhost:8081),
// and Playwright: `npm i playwright && npx playwright install chromium`.
// Run: node tests/e2e_ui.mjs
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
await page.evaluate(() => document.querySelectorAll('#preview a, #preview .tg-btn').forEach((a) => a.click()));
await page.waitForTimeout(500);
const leftovers = await page.evaluate(() => {
  const p = document.querySelector('#preview');
  const bad = [];
  p.querySelectorAll('*').forEach((n) => {
    if (n.namespaceURI !== 'http://www.w3.org/1999/xhtml') bad.push('svg:' + n.localName);
    for (const a of n.attributes) {
      if (/^on/i.test(a.name) || a.name === 'style' && !n.closest('.katex') && !n.matches('td,th,.tg-map,.tg-map-tiles') || /javascript:|data:text/i.test(a.value)) bad.push(n.localName + '[' + a.name + ']');
    }
    if (['iframe', 'form', 'area', 'style', 'script'].includes(n.localName)) bad.push(n.localName);
  });
  return bad;
});
check('XSS: hostile HTML is neutralised in the preview', alerts === 0 && leftovers.length === 0, leftovers.join(', '));
const rtUrl = { rich_message: { blocks: [{ type: 'paragraph', text: { type: 'url', text: 'клик', url: 'javascript:alert(10)' } }] } };
await applyJson(rtUrl);
await page.click('#mode-tabs [data-mode=blocks]');
const href = await page.evaluate(() => document.querySelector('#preview a') && document.querySelector('#preview a').getAttribute('href'));
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
await page.fill('#chat', '@chan_a, @chan_b');
await page.click('#btn-publish');
await page.waitForTimeout(800);
const sentTo = (await mockLog()).filter((c) => c.method === 'sendRichMessage').slice(-2).map((c) => c.body.chat_id);
check('Publishing to two channels from one draft', JSON.stringify(sentTo) === '["@chan_a","@chan_b"]', JSON.stringify(sentTo));
await page.fill('#chat', '@my_channel');

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
const previewText = () => page.locator('#preview').textContent();
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

check('No CSP violations or JS errors in the editor', errors.length === 0 && csp.length === 0, [...errors, ...csp].join(' | '));
await browser.close();
const failed = results.filter((x) => !x.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
