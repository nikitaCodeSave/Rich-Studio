// clicking another mode tab does not lose the blocks after autosave + reload
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
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
}
