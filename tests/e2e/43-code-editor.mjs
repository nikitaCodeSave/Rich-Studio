// The source tabs as CodeMirror (code-editor.js): what is typed is what the post gets, suggestions from snippets.js
// and from the spec down to block types, snippets over a selection, the textarea still the value (preview, autosave,
// the unapplied JSON of #117), Ctrl+Z inside the editor stays there, text from outside comes in, «Простой редактор»
// is remembered, the textarea works when the vendored files (all or one) do not load, a phone's screen.
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
  // nothing left to save: a reload with a save on the way would bring back a stale version and a conflict dialog
  const settled = (page) => page.waitForFunction(() => /^сохранено/.test(document.querySelector('#save-state')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
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

  // HTML: what is typed is what the post gets (closing tags typed by hand are not doubled), tags from snippets.js,
  // the textarea, preview and draft follow
  await tab(page, 'html');
  await page.waitForSelector('#pane-html .cm-editor', quick).catch(() => {});
  await page.click('#pane-html .cm-content');
  await page.keyboard.type('<p>Привет <b>жирный</b> и <i>курсив</i> <tg-sp');
  const tags = await list(page);
  const spoiler = await pick(page, 'tg-spoiler');
  await page.keyboard.type('>секрет</tg-spoiler></p>');
  const html = '<p>Привет <b>жирный</b> и <i>курсив</i> <tg-spoiler>секрет</tg-spoiler></p>';
  const saved = await until(async () => (await draft(page))?.payload?.rich_message?.html === html);
  check('HTML tab is CodeMirror: typed text goes as typed, tags from snippets.js, textarea, preview and draft follow',
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

  // Markdown: typed as typed (HTML tags in it too), and a snippet over a selection keeps "$$" as text
  await tab(page, 'markdown');
  await page.waitForSelector('#pane-markdown .cm-editor', quick).catch(() => {});
  await page.click('#pane-markdown .cm-content');
  await page.keyboard.type('**жирный** и <tg-sp');
  const inMarkdown = await pick(page, 'tg-spoiler');
  await page.keyboard.type('>x</tg-spoiler> [ссылка](https://t.me)');
  const markdown = await page.inputValue('#src-markdown');
  await page.keyboard.press('Control+a');
  await page.keyboard.type('$$x^2$$');
  await page.keyboard.press('Control+a');
  await page.locator('#snip-markdown').getByRole('button', { name: 'Ж', exact: true }).click();
  const bold = await until(async () => { const v = await page.inputValue('#src-markdown'); return v.startsWith('**') && v; });
  check('Markdown in CodeMirror goes as typed; a snippet wraps the selection, "$$" in it stays',
    inMarkdown && markdown === '**жирный** и <tg-spoiler>x</tg-spoiler> [ссылка](https://t.me)' && bold === '**$$x^2$$**' && (await doc(page, 'markdown')) === bold,
    JSON.stringify({ inMarkdown, markdown, bold }));

  // JSON typed by hand: the closing brackets and quotes CodeMirror adds are typed over, not doubled
  await tab(page, 'json');
  await page.waitForSelector('#pane-json .cm-editor', quick).catch(() => {});
  await page.click('#pane-json .cm-content');
  await page.keyboard.press('Control+a');
  await page.keyboard.type('{"a": 1, "b": [true, "x"], "c": {"d": "e"}}');
  const byHand = await page.inputValue('#src-json');
  check('JSON typed by hand comes out as typed', byHand === '{"a": 1, "b": [true, "x"], "c": {"d": "e"}}', JSON.stringify(byHand));

  // JSON: the post's fields, block types by their const, a block's fields, then «Применить JSON»
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

  // the unapplied JSON (#117) in CodeMirror: through the tabs and a reload, Ctrl+Z after it, «Отбросить»
  await page.click('#pane-json .cm-content');
  await page.keyboard.press('Control+a');
  await page.keyboard.type('{"rich_message": {"markdown": "Неприменённый');
  const unapplied = await page.inputValue('#src-json');
  await until(async () => JSON.stringify((await draft(page))?.payload ?? null).includes('Неприменённый'));
  await tab(page, 'blocks');
  await tab(page, 'json');
  const afterTabs = await doc(page, 'json');
  await settled(page);
  await page.reload();
  await page.waitForSelector('.block');
  await tab(page, 'json');
  await page.waitForSelector('#pane-json .cm-editor', quick).catch(() => {});
  const afterReload = await doc(page, 'json');
  const dot = await page.locator('#mode-tabs [data-mode=json]').evaluate((b) => b.classList.contains('pending'));
  await page.click('#pane-json .cm-content');
  await page.keyboard.press('Control+z');
  const afterUndo = await page.inputValue('#src-json');
  await tab(page, 'blocks');
  await page.locator('#pane-blocks .rte').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type('!');
  await tab(page, 'json');
  await page.locator('#json-stale').getByRole('button', { name: 'Отбросить' }).click(quick).catch(() => {});
  const dropped = await until(async () => { const d = await doc(page, 'json'); return d && !d.includes('Неприменённый') && d; });
  check('Unapplied JSON in CodeMirror survives the tabs and a reload, Ctrl+Z after it keeps it, «Отбросить» shows the post',
    afterTabs === unapplied && afterReload === unapplied && dot && afterUndo === unapplied
      && !!dropped && /Заголовок!/.test(dropped) && dropped === (await page.inputValue('#src-json')),
    JSON.stringify({ unapplied, afterTabs, afterReload, dot, afterUndo, dropped }));

  // «Простой редактор»: the textarea, remembered after a reload; a snippet over its selection; off again: CodeMirror
  // with the same text
  await tab(page, 'html');
  await page.locator('#pane-html .source-tools input[type=checkbox]').check();
  // the open tab is the post's format: saved as HTML, the draft reopens in the HTML tab
  await until(async () => (await draft(page))?.payload?._studio?.format === 'html');
  await settled(page);
  await page.reload();
  // the draft reopens in the HTML tab: the open tab is the post's format
  const plainVisible = await page.waitForSelector('#src-html', quick).then(() => true, () => false);
  const noEditor = (await page.locator('#pane-html .cm-editor').count()) === 0;
  await page.fill('#src-html', '$$a$$');
  await page.focus('#src-html');
  await page.keyboard.press('Control+a');
  await page.locator('#snip-html').getByRole('button', { name: 'Ж', exact: true }).click();
  const plainBold = await page.inputValue('#src-html');
  await page.fill('#src-html', '<b>простое поле</b>');
  await page.locator('#pane-html .source-tools input[type=checkbox]').uncheck();
  const again = await page.waitForSelector("#pane-html .cm-editor", quick).then(() => true, () => false);
  const [shown, value, textareaShown] = [await doc(page, 'html'), await page.inputValue('#src-html'), await page.isVisible('#src-html')];
  check('«Простой редактор» shows the textarea and is remembered; off, CodeMirror comes back with the text',
    plainVisible && noEditor && plainBold === '<b>$$a$$</b>' && again && shown === '<b>простое поле</b>' && value === shown && !textareaShown,
    JSON.stringify({ plainVisible, noEditor, plainBold, again, shown, value, textareaShown }));
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

  // one file of the set fails: the retry asks for the entry under another address, and the failed file again
  const part = await open();
  const one = '**/static/vendor/codemirror-*/view.js';
  await part.page.route(one, (r) => r.abort());
  await part.page.goto(BASE);
  await part.page.waitForSelector('.block');
  await tab(part.page, 'html');
  const partNote = await until(async () => /не загрузилась/.test(await part.page.locator('#pane-html .source-note').textContent()));
  await part.page.unroute(one);
  await tab(part.page, 'markdown');
  await tab(part.page, 'html');
  const recovered = await part.page.waitForSelector('#pane-html .cm-editor', quick).then(() => true, () => false);
  await part.page.click('#pane-html .cm-content').catch(() => {});
  await part.page.keyboard.type('<b>снова</b>');
  const partValue = await part.page.inputValue('#src-html');
  check('When one vendored file fails, the next open of the tab loads CodeMirror after all',
    partNote && recovered && partValue.includes('<b>снова</b>') && part.errors.length === 0,
    JSON.stringify({ partNote, recovered, partValue, errors: part.errors }));
  await part.context.close();

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
  // the search panel's field at 16px: a smaller one makes the phone zoom the page in when it gets the focus
  await phone.page.keyboard.press('Escape');
  await phone.page.keyboard.press('Control+f');
  const searchFont = await phone.page.waitForSelector('.cm-search .cm-textfield', quick).then((e) => e.evaluate((x) => parseFloat(getComputedStyle(x).fontSize)), () => 0);
  check('On a phone the description fits the screen and does not cover the options, the search field is 16px, no page errors',
    boxes.length === 2 && info.left >= 0 && info.right <= width && (info.top >= menu.bottom - 1 || info.bottom <= menu.top + 1)
      && searchFont >= 16 && phone.errors.length === 0,
    JSON.stringify({ width, boxes, searchFont, errors: phone.errors }));
  await phone.context.close();
}
