// The source tabs as CodeMirror (code-editor.js): suggestions from snippets.js and from the spec down to block types,
// the textarea still the value (preview, autosave), Ctrl+Z inside the editor stays there, text from outside comes
// in, «Простой редактор» is remembered, the textarea works when the vendored files do not load, a phone's screen.
// Pages of its own: the shared page keeps the plain textareas (harness.mjs).
import { devices } from 'playwright';

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ browser, BASE, check, until }) {
  /** @param {import('playwright').BrowserContextOptions} [options] */
  const open = async (options = { viewport: { width: 1400, height: 900 } }) => {
    const context = await browser.newContext(options);
    const page = await context.newPage();
    const errors = [];
    page.on('dialog', (d) => d.accept()); // a reload with unsaved changes asks first
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
    return { context, page, errors };
  };
  const quick = { timeout: 5000 };
  const tab = (page, mode) => page.click(`#mode-tabs [data-mode=${mode}]`);
  // what CodeMirror shows (its lines; a short text has them all rendered), or null without it; an empty text shows
  // the textarea's placeholder
  const doc = (page, lang) => page.evaluate((l) => {
    const lines = [...document.querySelectorAll(`#pane-${l} .cm-content .cm-line`)];
    if (document.querySelector(`#pane-${l} .cm-placeholder`)) return '';
    return lines.length ? lines.map((x) => x.textContent).join('\n') : null;
  }, lang);
  const draft = (page) => page.evaluate(async () => {
    const id = new URLSearchParams(location.search).get('draft');
    return id ? (await fetch('/api/drafts/' + id)).json() : null;
  });
  const list = (page) => page.waitForSelector('.cm-tooltip-autocomplete li', quick).then(() => page.locator('.cm-tooltip-autocomplete li').allTextContents(), () => []);
  // the list is open with this option selected; CodeMirror takes Enter only after its interactionDelay (75 ms)
  const pick = async (page, label) => {
    const shown = await page.waitForFunction((l) => document.querySelector('.cm-tooltip-autocomplete li[aria-selected]')?.textContent?.startsWith(l), label, quick).then(() => true, () => false);
    await page.waitForTimeout(150);
    await page.keyboard.press('Enter');
    return shown;
  };

  const { context, page, errors } = await open();
  await page.goto(BASE);
  await page.waitForSelector('.block');

  // HTML: tags from snippets.js, the closing tag from lang-html, the textarea, preview and draft follow
  await tab(page, 'html');
  await page.waitForSelector('#pane-html .cm-editor', quick).catch(() => {});
  await page.click('#pane-html .cm-content');
  await page.keyboard.type('<p>Привет <tg-sp');
  const tags = await list(page);
  const spoiler = await pick(page, 'tg-spoiler');
  await page.keyboard.type('>секрет');
  const html = '<p>Привет <tg-spoiler>секрет</tg-spoiler></p>';
  const saved = await until(async () => (await draft(page))?.payload?.rich_message?.html === html);
  check('HTML tab is CodeMirror: tags from snippets.js, the closing tag written, textarea, preview and draft follow',
    spoiler && !tags.some((t) => t.startsWith('div')) && (await page.inputValue('#src-html')) === html && (await page.isHidden('#src-html'))
      && !!saved && /Привет/.test(await page.locator('#preview .tg-preview').textContent()),
    JSON.stringify({ tags: tags.slice(0, 5), textarea: await page.inputValue('#src-html'), saved: !!saved }));

  // Ctrl+Z in the editor is the editor's: the post-wide undo would leave a redo step behind (Ctrl+Y: Playwright's
  // Shift+z is a lowercase z, which CodeMirror reads as Ctrl+Z)
  await page.keyboard.press('Control+z');
  const undone = await doc(page, 'html');
  const redoAfterUndo = await page.isEnabled('#btn-redo');
  await page.keyboard.press('Control+y');
  const redone = await doc(page, 'html');
  check('Ctrl+Z and Ctrl+Y inside CodeMirror undo and redo its typing, not the whole post',
    undone !== html && redone === html && !redoAfterUndo && (await page.inputValue('#src-html')) === html,
    JSON.stringify({ undone, redone, redoAfterUndo }));

  // text from outside: the post-wide ↶ (focus outside the editor) puts its snapshot into CodeMirror
  await page.waitForTimeout(500); // history.js takes a snapshot after a 400 ms pause in editing
  await page.click('#pane-html .cm-content');
  await page.keyboard.press('Control+End');
  await page.keyboard.type(' и ещё');
  await until(async () => page.isEnabled('#btn-undo'));
  await page.waitForTimeout(500);
  await page.click('#btn-undo');
  const back = await until(async () => { const d = await doc(page, 'html'); return d !== null && !d.includes('и ещё') && d; });
  check('The post-wide ↶ brings its text into CodeMirror', back === (await page.inputValue('#src-html')) && back === html, JSON.stringify(back));

  // JSON: the post's fields, block types by their const, a block's fields, then «Применить JSON»
  await tab(page, 'json');
  await page.waitForSelector('#pane-json .cm-editor', quick).catch(() => {});
  await page.click('#pane-json .cm-content');
  await page.keyboard.press('Control+a');
  await page.keyboard.type('{"rich');
  const root = await pick(page, 'rich_message');
  await page.keyboard.type('"blo');
  const blocks = await pick(page, 'blocks');
  await page.keyboard.type('{"type": "hea');
  const types = await list(page);
  await page.waitForSelector('.cm-completionInfo', quick).catch(() => {});
  await page.screenshot({ path: 'test-results/ui/code-editor-1400.png' }).catch(() => {});
  const heading = await pick(page, 'heading');
  await page.keyboard.type(', "si');
  const size = await pick(page, 'size');
  await page.keyboard.type('2, "te');
  const text = await pick(page, 'text');
  await page.keyboard.type('Заголовок');
  const typed = await doc(page, 'json');
  await page.click('#json-apply');
  const applied = await until(async () => (await draft(page))?.payload?.rich_message?.blocks?.[0]);
  check('JSON tab: suggestions walk the post down to a block\'s type and fields, and the JSON applies',
    root && blocks && heading && size && text && types.some((t) => t.startsWith('heading')) && !types.some((t) => t.startsWith('photo'))
      && JSON.stringify(applied) === JSON.stringify({ type: 'heading', size: 2, text: 'Заголовок' }),
    JSON.stringify({ root, blocks, heading, size, text, typed, applied }));

  // «Простой редактор»: the textarea, remembered after a reload; off again: CodeMirror with the same text
  await tab(page, 'html');
  await page.locator('#pane-html .source-tools input[type=checkbox]').check();
  await page.reload();
  // the draft reopens in the HTML tab: the open tab is the post's format
  const plainVisible = await page.waitForSelector('#src-html', quick).then(() => true, () => false);
  const noEditor = (await page.locator('#pane-html .cm-editor').count()) === 0;
  await page.fill('#src-html', '<b>простое поле</b>');
  await page.locator('#pane-html .source-tools input[type=checkbox]').uncheck();
  const again = await page.waitForSelector("#pane-html .cm-editor", quick).then(() => true, () => false);
  const [shown, value, textareaShown] = [await doc(page, 'html'), await page.inputValue('#src-html'), await page.isVisible('#src-html')];
  check('«Простой редактор» shows the textarea and is remembered; off, CodeMirror comes back with the text',
    plainVisible && noEditor && again && shown === '<b>простое поле</b>' && value === shown && !textareaShown, JSON.stringify({ plainVisible, noEditor, again, shown, value, textareaShown }));
  check('No JS errors on the CodeMirror page', errors.length === 0, errors.join(' | '));
  await context.close();

  // the vendored files do not load: the textarea is the editor, the draft saves; the next open of the tab tries again
  const off = await open();
  await off.page.route('**/static/vendor/codemirror**', (r) => r.abort());
  await off.page.goto(BASE);
  await off.page.waitForSelector('.block');
  await tab(off.page, 'html');
  const note = await until(async () => /не загрузилась/.test(await off.page.locator('#pane-html .source-note').textContent()));
  await off.page.fill('#src-html', '<p>Без подсветки</p>');
  const kept = await until(async () => (await draft(off.page))?.payload?.rich_message?.html === '<p>Без подсветки</p>');
  await off.page.unroute('**/static/vendor/codemirror**');
  await tab(off.page, 'markdown');
  await tab(off.page, 'html');
  const later = await off.page.waitForSelector('#pane-html .cm-editor', quick).then(() => true, () => false);
  check('Without the vendored files the textarea works and the draft saves; the next open of the tab loads CodeMirror',
    note && !!kept && later && (await doc(off.page, 'html')) === '<p>Без подсветки</p>' && off.errors.length === 0,
    JSON.stringify({ note, kept: !!kept, later, errors: off.errors }));
  await off.context.close();

  // a phone: the description of an option fits the screen and leaves the options free to tap
  const phone = await open({ ...devices['iPhone 13'] });
  await phone.page.goto(BASE);
  await phone.page.waitForSelector('.block');
  await phone.page.tap('#mode-tabs [data-mode=json]');
  await phone.page.waitForSelector('#pane-json .cm-editor', quick).catch(() => {});
  await phone.page.tap('#pane-json .cm-content');
  await phone.page.keyboard.press('Control+a');
  await phone.page.keyboard.type('{"options": {"dis');
  await phone.page.waitForSelector('.cm-completionInfo', quick).catch(() => {});
  const boxes = await phone.page.evaluate(() => [...document.querySelectorAll('.cm-tooltip-autocomplete, .cm-completionInfo')].map((e) => e.getBoundingClientRect().toJSON()));
  const width = phone.page.viewportSize()?.width ?? 0;
  await phone.page.screenshot({ path: 'test-results/ui/code-editor-390.png' }).catch(() => {});
  const [menu, info] = boxes;
  check('On a phone the description fits the screen and does not cover the options',
    boxes.length === 2 && info.left >= 0 && info.right <= width && (info.top >= menu.bottom - 1 || info.bottom <= menu.top + 1),
    JSON.stringify({ width, boxes }));
  await phone.context.close();
}
