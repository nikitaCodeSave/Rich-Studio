// clicking another mode tab does not lose the blocks after autosave + reload
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  // «сохранено» shows once what is on screen is saved; any change before that shows «есть изменения» at once
  const saved = () => page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 8000 }).catch(() => {});
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Сохранить меня' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.click('#btn-save');
  await saved();
  await page.click('#mode-tabs [data-mode=html]');
  await saved(); // the autosave of the html mode
  await page.reload();
  await saved(); // the draft is open again
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.waitForFunction(() => document.querySelector('#pane-blocks .rte')?.textContent === 'Сохранить меня', null, { timeout: 8000 }).catch(() => {});
  const kept = await page.evaluate(() => document.querySelector('#pane-blocks .rte') && document.querySelector('#pane-blocks .rte').textContent);
  check('Drafts keep the text of every mode', kept === 'Сохранить меня', String(kept));
}
