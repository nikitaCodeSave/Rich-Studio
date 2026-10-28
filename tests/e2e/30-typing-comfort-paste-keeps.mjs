// typing comfort: paste keeps formatting, Ctrl+K edits a link, the table is a grid, checks point at the block
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson, applyJson }) {
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'x' }, { type: 'details', summary: '', blocks: [{ type: 'paragraph', text: 'внутри' }] }, { type: 'table', cells: [[{ text: 'A', align: 'left', valign: 'top' }, { text: 'B', align: 'left', valign: 'top' }]] }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(300);
  const paste = (sel, data, file) => page.evaluate(([s, d, f]) => {
    const ed = /** @type {HTMLElement} */ (document.querySelector(s));
    ed.focus();
    const r = document.createRange(); r.selectNodeContents(ed); getSelection()?.removeAllRanges(); getSelection()?.addRange(r);
    const dt = new DataTransfer();
    for (const [k, v] of Object.entries(d)) dt.setData(k, v);
    if (f) dt.items.add(new File([Uint8Array.from(atob(f), (c) => c.charCodeAt(0))], 'image.png', { type: 'image/png' }));
    ed.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, [sel, data, file]);
  const para = '#pane-blocks > .blocks > .block:nth-child(1) .rte';
  // Google Docs wraps everything in <b style="font-weight:normal">; pictures, scripts, javascript: and host-less links are dropped
  await paste(para, { 'text/plain': 'Жирный и курсив\nссылка', 'text/html': '<meta charset="utf-8"><b style="font-weight:normal" id="docs-internal-guid-1"><p><span style="font-weight:700">Жирный</span><span> и </span><span style="font-style:italic">курсив</span><a href="javascript:alert(2)">!</a><a href="https://">?</a></p><p><a href="https://t.me/x"><span>ссылка</span></a><img src="https://example.com/a.png"><script>alert(1)</script></p></b>' });
  await page.waitForTimeout(400);
  await showJson();
  const pasted = (await state()).rich_message.blocks[0].text;
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(200);
  // Ctrl+K inside a link opens it for editing
  await page.evaluate((s) => { const a = document.querySelector(s + ' a'); const r = document.createRange(); r.setStart(a.firstChild, 2); r.collapse(true); a.closest('.rte').focus(); getSelection()?.removeAllRanges(); getSelection()?.addRange(r); }, para);
  await page.keyboard.press('Control+k');
  const dlg = page.locator('dialog[open]');
  const oldUrl = await dlg.locator('input[type=url]').inputValue();
  const canRemove = await dlg.locator('button', { hasText: 'Убрать ссылку' }).isVisible();
  await dlg.locator('input[type=url]').fill('https://t.me/y');
  await dlg.locator('button', { hasText: 'Сохранить' }).click();
  // a new link at the caret: "https://" alone is refused, like Telegram does
  await page.evaluate((s) => { const ed = document.querySelector(s); ed.focus(); const r = document.createRange(); r.selectNodeContents(ed); r.collapse(false); getSelection()?.removeAllRanges(); getSelection()?.addRange(r); }, para);
  await page.keyboard.press('Control+k');
  await dlg.locator('input[aria-label="Текст ссылки"]').fill(' тут');
  await dlg.locator('input[type=url]').fill('https://');
  await dlg.locator('button', { hasText: 'Вставить' }).click();
  const refused = await dlg.locator('.err').textContent();
  await dlg.locator('input[type=url]').fill('https://t.me/z');
  await dlg.locator('button', { hasText: 'Вставить' }).click();
  await page.waitForTimeout(400);
  await showJson();
  const linked = (await state()).rich_message.blocks[0].text;
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(200);
  // the table: plain cells, the bar acts on the cell in focus, a spreadsheet pastes into the grid
  const tableBlock = '#pane-blocks > .blocks > .block:nth-child(3)';
  const perCellControls = await page.locator(tableBlock + ' td select, ' + tableBlock + ' td input').count();
  await page.locator(tableBlock + ' td .rte').first().click();
  await page.locator(tableBlock + ' .cell-bar button', { hasText: 'Заголовок' }).click();
  await paste(tableBlock + ' tr:nth-child(1) td:nth-child(2) .rte', { 'text/plain': 'Тема\tВедущий\r\nОбзор\tНикита\r\n' });
  await page.waitForTimeout(400);
  await showJson();
  const cells = (await state()).rich_message.blocks[2].cells;
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(700);
  // a check names the block in words and jumps to the field
  const warn = page.locator('#validation li button', { hasText: 'Раскрывашка №2 › Заголовок' });
  const warnText = await warn.textContent().catch(() => '');
  await warn.click().catch(() => {});
  await page.waitForTimeout(200);
  const flashed = await page.evaluate(() => !!document.querySelector('#pane-blocks [data-field=summary].flash'));
  const pill = await page.locator('#check-pill').textContent();
  // a picture pasted into a text field becomes a photo block after it
  await paste(para, {}, 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==');
  await page.waitForTimeout(800);
  await showJson();
  const afterImg = (await state()).rich_message.blocks;
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(200);
  // the palette is grouped and searchable
  await page.locator('#pane-blocks > .blocks > .add-block').click();
  const groups = await page.locator('dialog[open] .palette-group h4').allTextContents();
  await page.locator('dialog[open] input[type=search]').fill('табл');
  const found = await page.locator('dialog[open] .palette button:visible').allTextContents();
  await page.locator('dialog[open] button', { hasText: 'Отмена' }).click();
  await page.screenshot({ path: 'test-results/ui/typing-1400.png', fullPage: true }).catch(() => {});
  const json = (x) => JSON.stringify(x);
  check('Paste keeps formatting, Ctrl+K edits links, table grid with spreadsheet paste, checks point at the block',
    json(pasted) === json([{ type: 'bold', text: 'Жирный' }, ' и ', { type: 'italic', text: 'курсив' }, '!?\n', { type: 'url', text: 'ссылка', url: 'https://t.me/x' }])
      && oldUrl === 'https://t.me/x' && canRemove && /нет сайта|не похоже/.test(refused || '')
      && json(linked.slice(-2)) === json([{ type: 'url', text: 'ссылка', url: 'https://t.me/y' }, { type: 'url', text: ' тут', url: 'https://t.me/z' }])
      && perCellControls === 0 && cells[0][0].is_header === true && cells.length === 2 && cells[0].length === 3 && cells[0][1].text === 'Тема' && cells[1][2].text === 'Никита'
      && /пустой текст/.test(warnText || '') && flashed && /символ/.test(pill || '')
      && afterImg[1]?.type === 'photo' && /^upload:paste-/.test(afterImg[1]?.photo?.media || '')
      && groups.slice(0, 4).join() === 'Текст,Медиа,Структура,Кнопки' && found.length === 1 && /Таблица/.test(found[0]),
    json({ pasted, oldUrl, canRemove, refused, linked, perCellControls, cells, warnText, flashed, pill, img: afterImg[1], groups, found }));
}
