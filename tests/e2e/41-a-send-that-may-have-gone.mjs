// a send Telegram did not answer (the post may be in the channel) is listed as such, not as «не вышел»; only «Убрать»
// takes it away, after the person has looked at the channel
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
  await page.selectOption('#posts-filter', 'failed');
  const row = page.locator('#posts li.post-row', { hasText: title });
  await row.waitFor({ timeout: 8000 }).catch(() => {});
  const text = await row.textContent().catch(() => '');
  const tip = await row.locator('button', { hasText: 'Убрать' }).getAttribute('title').catch(() => '');
  await page.screenshot({ path: 'test-results/ui/may-have-gone-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.screenshot({ path: 'test-results/ui/may-have-gone-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  check('A send that may have gone is listed with an unknown outcome, not as not sent',
    !!rec && rec.outcome_unknown === true && /Исход неизвестен: .*мог уйти/.test(text || '') && !/Не вышел/.test(text || '') && /проверьте канал/.test(tip || ''),
    JSON.stringify({ flag: rec && rec.outcome_unknown, text: (text || '').slice(0, 200), tip }));
  await row.locator('button', { hasText: 'Убрать' }).click().catch(() => {});
  const gone = await until(async () => !(await mine()));
  await page.selectOption('#posts-filter', '');
  await page.click('#side-tabs [data-tab=drafts]');
  check('«Убрать» takes the row away once the person has looked at the channel', !!gone);
}
