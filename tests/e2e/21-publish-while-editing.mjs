// while a post is open for editing, «Опубликовать» posts a new copy on purpose (as_new), never a 409
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog }) {
  // an error toast of an earlier check (the spec refresh, 9 s) must not count against this one
  await page.evaluate(() => document.querySelector('#toasts').replaceChildren());
  const before = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  await page.click('#btn-publish');
  // the publish ends with a toast either way: «Опубликовано…» or the error
  await page.waitForSelector('#toasts .toast.ok:has-text("Опубликовано"), #toasts .toast.err', { timeout: 8000 }).catch(() => {});
  const after = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  const errs = await page.locator('.toast.err').allTextContents();
  check('Publishing while editing posts a new copy on purpose', after === before + 1 && !errs.length, JSON.stringify({ before, after, errs }));
}
