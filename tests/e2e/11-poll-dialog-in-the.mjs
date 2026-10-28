// poll dialog (in the «Опубликовать ▾» menu)
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog }) {
  await page.click('#btn-more');
  await page.click('#btn-poll');
  await page.waitForSelector('dialog[open]');
  const dlg = page.locator('dialog[open]');
  await dlg.locator('input[type=text]').nth(0).fill('Когда эфир?');
  await dlg.locator('input[type=text]').nth(1).fill('Утром');
  await dlg.locator('input[type=text]').nth(2).fill('Вечером');
  await dlg.locator('label:has-text("старше 24 часов") input').check();
  await dlg.locator('input[placeholder="пусто = все"]').fill('ru, kz');
  await dlg.locator('button:has-text("Отправить")').click();
  await page.waitForTimeout(800);
  const pollCall = (await mockLog()).filter((c) => c.method === 'sendPoll').pop();
  const pb = pollCall && pollCall.body;
  const pollRow = await page.locator('#posts li').first().textContent();
  check('Poll dialog sends a channel poll', pb && pb.question === 'Когда эфир?' && pb.members_only === true && JSON.stringify(pb.country_codes) === '["RU","KZ"]' && pb.options.length === 2 && /опрос/.test(pollRow) && await page.locator('dialog[open]').count() === 0,
    JSON.stringify(pb) + ' | ' + pollRow);
}
