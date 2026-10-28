// the Files tab downloads a backup zip
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  await page.click('#side-tabs [data-tab=uploads]');
  const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }), page.click('#export')]);
  const name = dl.suggestedFilename();
  check('Backup downloads as a zip', /^rich-studio-\d{8}-\d{4}\.zip$/.test(name), name);
}
