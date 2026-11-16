// ✕ in «Файлы» deletes the file by the name its url has (encoded by the server): so does a name that is not UTF-8,
// shown with «�» while its url keeps the bytes (tests/test_regressions.py)
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, until }) {
  const names = () => page.evaluate(async () => (await (await fetch('/api/media')).json()).map((u) => u.name));
  await page.click('#side-tabs [data-tab=uploads]');
  await page.setInputFiles('#upload', [{ name: 'кот на удаление.png', mimeType: 'image/png', buffer: Buffer.from('fake png') }]);
  const row = page.locator('#uploads li', { hasText: 'кот_на_удаление.png' });
  await row.waitFor({ timeout: 8000 }).catch(() => {});
  const listed = (await names()).includes('кот_на_удаление.png');
  await row.locator('button[title="Удалить файл"]').click().catch(() => {});
  const gone = await until(async () => !(await names()).includes('кот_на_удаление.png'));
  check('✕ deletes an uploaded file by its url', listed && gone, JSON.stringify({ listed, left: await names() }));
}
