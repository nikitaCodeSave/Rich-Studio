// <rs-preview> keeps the post in a shadow root; in-post anchor links still scroll to their target
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const filler = Array.from({ length: 40 }, (_, i) => ({ type: 'paragraph', text: 'Строка ' + i }));
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: { type: 'anchor_link', anchor_name: 'end', text: 'вниз' } }, ...filler, { type: 'anchor', name: 'end' }, { type: 'paragraph', text: 'Конец' }] } });
  await page.waitForTimeout(300);
  const scrollBefore = await page.evaluate(() => document.querySelector('.preview-col').scrollTop);
  await page.locator('#preview a.tg-link').click();
  await page.waitForTimeout(800);
  const scrollAfter = await page.evaluate(() => document.querySelector('.preview-col').scrollTop);
  const isolated = await page.evaluate(() => !!document.querySelector('#preview').shadowRoot && !document.querySelector('.tg-bubble') && !location.hash);
  check('Preview is isolated in a shadow root and anchor links scroll inside it', isolated && scrollAfter > scrollBefore + 100, JSON.stringify({ isolated, scrollBefore, scrollAfter }));
}
