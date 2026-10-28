// channels: a checked channel shows its title, the draft remembers its channel
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  await page.click('#btn-new');
  await page.waitForTimeout(400);
  await page.fill('#title', 'Канал черновика');
  await page.click('#btn-check');
  await page.locator('dialog[open] input[placeholder="@канал, @другой"]').fill('@kept_chan');
  await page.keyboard.press('Tab');
  await page.waitForTimeout(500);
  await page.locator('dialog[open] .channel', { hasText: '@kept_chan' }).locator('button', { hasText: 'Проверить' }).click();
  await page.waitForTimeout(600);
  const dialogText = await page.locator('dialog[open] .channels').textContent();
  await page.keyboard.press('Escape');
  await page.click('#btn-save');
  await page.waitForTimeout(500);
  const keptId = await page.evaluate(() => localStorage.getItem('rs.lastDraft'));
  const header = await page.evaluate(() => document.querySelector('#preview').shadowRoot.textContent);
  const label = await page.locator('#btn-check').textContent();
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  const fresh = await page.inputValue('#chat');
  await page.click('#side-tabs [data-tab=drafts]');
  await page.locator('#drafts li', { hasText: 'Канал черновика' }).first().click();
  await page.waitForTimeout(500);
  const reopened = await page.inputValue('#chat');
  const draftRow = await page.locator('#drafts li', { hasText: 'Канал черновика' }).first().textContent();
  const stored = await page.evaluate((id) => fetch('/api/drafts/' + JSON.parse(id)).then((r) => r.json()), keptId);
  check('A checked channel shows its title and the draft keeps its channel',
    /Тест/.test(dialogText) && /публикация/.test(dialogText) && /Тест/.test(header) && label === 'Тест · @kept_chan ▾' && fresh === '@test' && reopened === '@kept_chan' && /→ Тест · @kept_chan/.test(draftRow) && JSON.stringify(stored.chat_ids) === '["@kept_chan"]',
    JSON.stringify({ dialogText: dialogText.slice(0, 120), label, fresh, reopened, draftRow, stored: stored.chat_ids }));
}
