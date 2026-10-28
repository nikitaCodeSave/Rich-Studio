// the standalone render page works under its CSP
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, browser, check, BASE }) {
  const d = await page.evaluate(async () => (await fetch('/api/drafts', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'r', post: { rich_message: { markdown: '# Заголовок\n\n<tg-math>e^{i\\\\pi}+1=0</tg-math>' } } }) })).json());
  const rp = await browser.newPage();
  const rpErrors = [];
  rp.on('console', (m) => { if (m.type() === 'error') rpErrors.push(m.text()); });
  rp.on('pageerror', (e) => rpErrors.push(e.message));
  await rp.goto(`${BASE}/render/draft/${d.id}?theme=dark`);
  await rp.waitForSelector('.tg-bubble h1');
  check('Render page draws under CSP', rpErrors.length === 0 && await rp.locator('.katex').count() === 1, rpErrors.join(' | '));
}
