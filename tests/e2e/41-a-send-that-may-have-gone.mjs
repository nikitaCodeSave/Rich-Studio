// a send Telegram did not answer (the post may be in the channel) is listed as such, not as «не вышел»: its own badge
// and filter; «Убрать» takes it away only after the studio's dialog, where the person says they looked at the channel
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, until, setChat, applyJson, MOCK }) {
  const fail = (body) => fetch(MOCK + '/fail', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'sendRichMessage', ...body }) });
  const title = `Пост, который мог уйти ${Date.now() % 100000}`; // its own row, also on a reused database
  const mine = () => page.evaluate((t) => fetch('/api/posts?status=failed').then((r) => r.json()).then((ps) => ps.find((p) => p.title === t)), title);
  await fail({ description: 'Bad Gateway', error_code: 502 });
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: title }] } });
  await setChat('@maybe');
  await page.click('#btn-publish');
  const rec = await until(mine);
  await fail({ description: null });
  await page.click('#side-tabs [data-tab=posts]');
  const row = page.locator('#posts li.post-row', { hasText: title });
  // «Не вышли» lists refusals only; the unknown outcome has a filter of its own
  await page.selectOption('#posts-filter', 'failed');
  await page.waitForFunction(() => [...document.querySelectorAll('#posts li.post-row')].every((r) => /ошибка/.test(r.textContent || '')), null, { timeout: 8000 }).catch(() => {});
  const inRefused = await row.count();
  await page.selectOption('#posts-filter', 'unknown');
  await row.waitFor({ timeout: 8000 }).catch(() => {});
  const text = (await row.textContent().catch(() => '')) || '';
  const badge = await row.locator('.status').textContent().catch(() => '');
  check('A send that may have gone has its own badge and filter, not «ошибка» or «Не вышел»',
    !!rec && rec.outcome_unknown === true && inRefused === 0 && badge === 'исход неизвестен' && /мог уйти/.test(text) && !/Не вышел|ошибка/.test(text),
    JSON.stringify({ flag: rec && rec.outcome_unknown, inRefused, badge, text: text.slice(0, 200) }));
  // «Убрать» asks in the studio's own dialog (a browser confirm is easy to click through), on a phone too
  let native = 0;
  const seen = () => { native++; };
  page.on('dialog', seen);
  await page.setViewportSize({ width: 390, height: 844 });
  await row.locator('button', { hasText: 'Убрать' }).click().catch(() => {});
  const dlg = page.locator('dialog.dlg[open]');
  await dlg.waitFor({ timeout: 8000 }).catch(() => {});
  const asked = (await dlg.textContent().catch(() => '')) || '';
  const inView = await dlg.locator('button', { hasText: 'убрать' }).isVisible().catch(() => false);
  await page.screenshot({ path: 'test-results/ui/may-have-gone-390.png' }).catch(() => {});
  await dlg.locator('button', { hasText: 'Отмена' }).click().catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  const kept = !!(await mine());
  await row.locator('button', { hasText: 'Убрать' }).click().catch(() => {});
  await dlg.waitFor({ timeout: 8000 }).catch(() => {});
  await page.screenshot({ path: 'test-results/ui/may-have-gone-1400.png' }).catch(() => {});
  await dlg.locator('button', { hasText: 'убрать' }).click().catch(() => {});
  const gone = await until(async () => !(await mine()));
  page.off('dialog', seen);
  await page.selectOption('#posts-filter', '');
  await page.click('#side-tabs [data-tab=drafts]');
  check('«Убрать» asks in the studio dialog to look at the channel first, on 390 px too; «Отмена» keeps the row',
    /Проверьте канал/.test(asked) && inView && native === 0 && kept && !!gone,
    JSON.stringify({ asked: asked.slice(0, 200), inView, native, kept, gone }));
}
