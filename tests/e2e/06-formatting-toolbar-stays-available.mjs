// formatting toolbar stays available when moving between fields
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'один' }, { type: 'paragraph', text: 'два' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.locator('#pane-blocks .rte').nth(0).click();
  await page.locator('#pane-blocks .rte').nth(1).click();
  await page.waitForTimeout(400);
  check('Toolbar stays visible when switching fields', await page.locator('.toolbar.on').count() === 1);
}
