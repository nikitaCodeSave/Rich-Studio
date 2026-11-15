// undo/redo for the whole post: buttons and Ctrl+Z outside text fields
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const previewText = () => page.locator('#preview .tg-preview').textContent();
  const shows = (text) => page.waitForFunction((t) => document.querySelector('#preview').shadowRoot.textContent.includes(t), text, { timeout: 8000 }).catch(() => {});
  // a new draft starts with nothing to undo, so «Отменить» turning on says the history took the first version
  // as a step of its own (it waits for a pause in editing)
  await page.click('#btn-new');
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Версия один' }] } });
  await page.waitForSelector('#btn-undo:enabled', { timeout: 8000 }).catch(() => {});
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Версия два' }] } });
  await page.evaluate(() => /** @type {HTMLElement} */ (document.activeElement).blur());
  const undoEnabled = await page.locator('#btn-undo').isEnabled();
  await page.keyboard.press('Control+z');
  await shows('Версия один');
  const afterUndo = await previewText();
  const redoEnabled = await page.locator('#btn-redo').isEnabled();
  await page.click('#btn-redo');
  await shows('Версия два');
  const afterRedo = await previewText();
  await page.click('#btn-undo');
  await shows('Версия один');
  const afterButtonUndo = await previewText();
  check('Undo and redo restore the whole post', undoEnabled && redoEnabled && /Версия один/.test(afterUndo) && /Версия два/.test(afterRedo) && /Версия один/.test(afterButtonUndo) && await page.locator('#save-state').textContent() === 'есть изменения',
    JSON.stringify({ undoEnabled, redoEnabled, afterUndo, afterRedo, afterButtonUndo }));
}
