// undo/redo for the whole post: buttons and Ctrl+Z outside text fields
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
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
}
