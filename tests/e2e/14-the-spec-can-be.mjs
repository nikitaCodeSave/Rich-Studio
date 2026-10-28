// the spec can be refreshed on demand; a failed download is reported, not hidden (the dev studio has no network spec)
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  await page.click('#spec-refresh');
  await page.waitForSelector('.toast.err');
  const refreshToast = await page.locator('.toast.err').last().textContent();
  const badgeWarn = await page.locator('#spec-badge.warn').count();
  await page.click('#spec-badge');
  await page.waitForSelector('dialog[open]');
  const specDialog = await page.locator('dialog[open]').textContent();
  await page.locator('dialog[open] button:has-text("Закрыть")').click();
  check('Spec refresh button reports the result and what the editor lacks',
    /Не удалось скачать спецификацию/.test(refreshToast) && badgeWarn === 1 && /Последняя проверка: .*скачать не удалось/.test(specDialog) && /знают все блоки/.test(specDialog),
    JSON.stringify({ refreshToast, badgeWarn, specDialog: specDialog.slice(0, 300) }));
}
