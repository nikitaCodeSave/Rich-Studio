// suggested posts: a suggestion from the channel's direct messages shows above the posts and is approved there
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push, until }) {
  const dm = { id: -1009, type: 'supergroup', title: 'Тест Direct Messages', is_direct_messages: true };
  await push({ message: { message_id: 50, date: Math.floor(Date.now() / 1000), chat: dm, from: { id: 5, first_name: 'Аня', username: 'anya' },
    text: 'Предлагаю пост про новый Bot API\nВторая строка', suggested_post_info: { state: 'pending', price: { currency: 'XTR', amount: 100 } } } });
  let listed = '[]';
  for (let i = 0; i < 20 && listed === '[]'; i++) {
    await page.waitForTimeout(300);
    listed = await page.evaluate(() => fetch('/api/suggested-posts?state=pending').then((r) => r.text()));
  }
  await page.click('#side-tabs [data-tab=drafts]');
  await page.click('#side-tabs [data-tab=posts]');
  await page.waitForSelector('#suggested li');
  const row = await page.locator('#suggested').textContent();
  await page.locator('#suggested li').first().click();
  await page.waitForSelector('dialog[open] >> text=Предложенный пост', { timeout: 8000 }).catch(() => {});
  const dlg = await page.locator('dialog[open]').textContent();
  await page.screenshot({ path: 'test-results/ui/suggested-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: 'test-results/ui/suggested-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  const approvals = async () => (await mockLog()).filter((c) => c.method === 'approveSuggestedPost');
  const before = (await approvals()).length;
  await page.locator('dialog[open] button', { hasText: 'Опубликовать сейчас' }).click();
  const approved = (await until(async () => { const a = await approvals(); return a.length > before && a; }) || []).pop();
  const gone = await page.locator('#suggested').waitFor({ state: 'hidden', timeout: 3000 }).then(() => true, () => false);
  const left = await page.evaluate(() => fetch('/api/suggested-posts?state=pending').then((r) => r.text()));
  check('A suggested post is listed above the posts and approved from its dialog',
    /Предложенные посты: 1/.test(row) && /Аня \(@anya\) · 100 ⭐/.test(row) && /Вторая строка/.test(dlg) && approved && approved.body.message_id === 50 && approved.body.chat_id === -1009 && gone,
    JSON.stringify({ row, dlg: dlg.slice(0, 160), approved: approved && approved.body, gone, left }));
}
