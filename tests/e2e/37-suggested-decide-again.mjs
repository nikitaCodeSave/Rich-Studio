// a decision on a suggested post that got no answer from Telegram can be made again from its dialog, at once
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push, MOCK }) {
  const fail = (body) => fetch(MOCK + '/fail', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'approveSuggestedPost', ...body }) });
  const mid = 1000 + (Date.now() % 100000); // its own suggestion, also on a reused database
  const dm = { id: -1009, type: 'supergroup', title: 'Тест Direct Messages', is_direct_messages: true };
  await push({ message: { message_id: mid, date: Math.floor(Date.now() / 1000), chat: dm, from: { id: 6, first_name: 'Боря' },
    text: `Пост ${mid}, решение по которому не дошло`, suggested_post_info: { state: 'pending' } } });
  const open = () => page.evaluate(() => fetch('/api/suggested-posts?state=open').then((r) => r.json()));
  for (let i = 0; i < 20 && !(await open()).some((s) => s.message_id === mid); i++) await page.waitForTimeout(300);
  await page.click('#side-tabs [data-tab=drafts]');
  await page.click('#side-tabs [data-tab=posts]');
  const row = page.locator('#suggested li', { hasText: `Пост ${mid},` });
  await row.click();
  // Telegram's gateway answers 502: the decision may have gone, the suggestion stays «approving»
  await fail({ description: 'Bad Gateway', error_code: 502 });
  await page.locator('dialog[open] button', { hasText: /Принять|Опубликовать сейчас/ }).click();
  await page.waitForTimeout(600);
  await fail({ description: null });
  await page.locator('dialog[open] button', { hasText: 'Закрыть' }).click().catch(() => {});
  const stuck = (await open()).find((s) => s.message_id === mid);
  await row.click();
  await page.waitForTimeout(300);
  const dlg = await page.locator('dialog[open]').textContent();
  await page.screenshot({ path: 'test-results/ui/suggested-again-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: 'test-results/ui/suggested-again-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.locator('dialog[open] button', { hasText: 'Решить снова' }).click();
  await page.waitForTimeout(600);
  const reopened = (await open()).find((s) => s.message_id === mid);
  const again = await page.locator('dialog[open]').textContent();
  await page.locator('dialog[open] button', { hasText: /Принять|Опубликовать сейчас/ }).click();
  await page.waitForTimeout(600);
  const calls = (await mockLog()).filter((c) => c.method === 'approveSuggestedPost' && c.body.message_id === mid);
  const done = (await page.evaluate(() => fetch('/api/suggested-posts').then((r) => r.json()))).find((s) => s.message_id === mid);
  check('A decision without an answer is made again from the dialog at once',
    stuck && stuck.state === 'approving' && /не ответил/.test(dlg) && reopened && reopened.state === 'pending' && /Опубликовать сейчас/.test(again)
      && calls.length === 2 && done && done.state === 'approved',
    JSON.stringify({ stuck: stuck && stuck.state, dlg: dlg.slice(0, 120), reopened: reopened && reopened.state, calls: calls.length, done: done && done.state }));
}
