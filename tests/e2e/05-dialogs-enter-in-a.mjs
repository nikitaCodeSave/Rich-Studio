// dialogs: Enter in a field does not discard, nested dialogs do not clobber each other
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson }) {
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.locator('#pane-blocks .rte').first().click();
  await page.selectOption('.toolbar select', { label: 'Кнопка в тексте' });
  await page.waitForSelector('dialog[open]');
  await page.locator('dialog[open] select.add-field').first().selectOption('url');
  const urlInput = page.locator('dialog[open] input[type=text]').first();
  await urlInput.click();
  await page.keyboard.press('Enter');
  const stillOpen = await page.locator('dialog[open]').count();
  await page.locator('dialog[open] .rte').first().click();
  await page.selectOption('.toolbar select', { label: 'Дата и время' });
  await page.waitForTimeout(300);
  const stacked = await page.locator('dialog[open]').count();
  await page.locator('dialog[open]').last().locator('button:has-text("Готово")').click();
  await page.waitForTimeout(200);
  const afterInner = await page.locator('dialog[open]').count();
  await page.locator('dialog[open]').last().locator('button:has-text("Готово")').click();
  await page.waitForTimeout(400);
  await showJson();
  const para = JSON.stringify((await state()).rich_message.blocks[0].text);
  check('Dialogs: Enter keeps it open, nested dialog stacks and both save', stillOpen === 1 && stacked === 2 && afterInner === 1 && /"type":"button"/.test(para) && /date_time/.test(para), `${stillOpen}/${stacked}/${afterInner}`);
}
