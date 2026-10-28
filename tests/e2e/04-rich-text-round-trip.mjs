// rich text round trip: trailing newline and unknown fields survive an edit
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson, applyJson }) {
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
}
