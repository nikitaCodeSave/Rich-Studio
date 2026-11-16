// ✕ in «Файлы» deletes the file by the name its url has (encoded by the server): so does a name that is not UTF-8,
// shown with «�» while its url keeps the bytes (tests/test_regressions.py)
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, until }) {
  const name = 'кот_на_удаление.png';
  const names = () => page.evaluate(() => [...document.querySelectorAll('#uploads .name')].map((n) => n.textContent));
  await page.click('#side-tabs [data-tab=uploads]');
  await page.setInputFiles('#upload', [{ name: 'кот на удаление.png', mimeType: 'image/png', buffer: Buffer.from('fake png') }]);
  const listed = await until(async () => (await names()).includes(name));
  await page.locator('#uploads li', { hasText: name }).locator('button[title="Удалить файл"]').click().catch(() => {});
  // the list is read again after the delete: the file is gone from the studio, not only from the page
  const gone = await until(async () => !(await names()).includes(name));
  check('✕ deletes an uploaded file by its url', !!listed && gone, JSON.stringify({ listed, left: await names() }));
}
