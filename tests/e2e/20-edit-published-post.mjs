// «Править» opens a published post with a bar over the editor; «Обновить пост» replaces it and keeps history
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, applyJson }) {
  const sent = await page.evaluate(async () => (await fetch('/api/send', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ chat_id: '@edit_me', post: { rich_message: { markdown: 'Первая версия' } } }) })).json());
  const mid = sent.message.message_id;
  await page.click('#side-tabs [data-tab=drafts]');
  await page.click('#side-tabs [data-tab=posts]');
  await page.locator('#posts li', { hasText: '#' + mid }).getByRole('button', { name: 'Править' }).click();
  await page.waitForSelector('#edit-bar >> text=Правите пост', { timeout: 5000 }).catch(() => {});
  const barText = await page.locator('#edit-bar').textContent().catch(() => '') || await page.evaluate(() => document.querySelector('#edit-bar').shadowRoot.textContent);
  await applyJson({ rich_message: { markdown: 'Вторая версия' } });
  await page.click('#edit-bar >> text=Обновить пост');
  // the edit goes through the studio to Telegram and back: wait for it instead of a fixed pause
  let edits = [];
  for (let i = 0; i < 50 && !edits.length; i++) {
    await page.waitForTimeout(100);
    edits = (await mockLog()).filter((c) => c.method === 'editMessageText' && c.body.message_id === mid);
  }
  await page.waitForTimeout(200);
  await page.click('#edit-bar >> text=История');
  await page.waitForSelector('dialog[open] >> text=История поста');
  const history = await page.locator('dialog[open] li').allTextContents();
  await page.keyboard.press('Escape');
  check('Editing a published post: bar, update in place, history',
    /#\d+ в @edit_me/.test(barText) && edits.length === 1 && edits[0].body.rich_message.markdown === 'Вторая версия' && history.length === 2 && /До замены/.test(history[1]),
    JSON.stringify({ barText: barText.replace(/\s+/g, ' ').trim(), edits: edits.length, history }));
}
