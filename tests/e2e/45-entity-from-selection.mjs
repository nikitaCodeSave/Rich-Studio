// an entity inserted over selected text takes it into its required field (address, username)
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson, applyJson }) {
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'UQ-address' }, { type: 'paragraph', text: '@name' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForTimeout(300);
  const insert = async (block, typeName) => {
    const sel = `#pane-blocks > .blocks > .block:nth-child(${block}) .rte`;
    await page.evaluate((s) => { const ed = document.querySelector(s); ed.focus(); const r = document.createRange(); r.selectNodeContents(ed); getSelection()?.removeAllRanges(); getSelection()?.addRange(r); }, sel);
    await page.waitForTimeout(150);
    await page.locator('select[title="Вставить элемент из спецификации"]').first().selectOption(typeName);
    await page.locator('dialog[open] button', { hasText: 'Готово' }).click();
    await page.waitForTimeout(300);
  };
  await insert(1, 'RichTextTonAddress');
  await insert(2, 'RichTextMention');
  await showJson();
  const [a, b] = (await state()).rich_message.blocks.map((x) => x.text);
  const json = (x) => JSON.stringify(x);
  check('An entity inserted over a selection fills its required field from it',
    json(a) === json({ type: 'ton_address', text: 'UQ-address', address: 'UQ-address' }) && json(b) === json({ type: 'mention', text: '@name', username: 'name' }), json({ a, b }));
}
