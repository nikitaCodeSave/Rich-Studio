// while a post is open for editing, «Опубликовать» posts a new copy on purpose (as_new), never a 409
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog }) {
  // an error toast of an earlier check (the spec refresh, 9 s) must not count against this one
  await page.evaluate(() => document.querySelector('#toasts').replaceChildren());
  const before = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  await page.click('#btn-publish');
  await page.waitForTimeout(1200);
  const after = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  const errs = await page.locator('.toast.err').allTextContents();
  check('Publishing while editing posts a new copy on purpose', after === before + 1 && !errs.length, JSON.stringify({ before, after, errs }));
}
