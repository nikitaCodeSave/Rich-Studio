// «Скачать бэкап» and «Try it out» in /docs read /api with X-Studio-Client: under a name from ALLOWED_HOSTS over plain
// http the browser sends no Sec-Fetch-Site, and the studio refuses a read by the editor's cookie without that header
// (#215). The stand runs on localhost, where the browser does mark it, so the refusal is played by a route here. No
// link, image or form of the editor points at /api/: they would go without the header.
import { readFileSync } from 'node:fs';

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, browser, check, BASE }) {
  const refused = JSON.stringify({ detail: 'чтение по cookie редактора без заголовка X-Studio-Client отклонено' });
  const asTheStudio = (/** @type {import('playwright').Route} */ route) => (route.request().headers()['x-studio-client']
    ? route.continue() : route.fulfill({ status: 403, contentType: 'application/json', body: refused }));
  await page.click('#side-tabs [data-tab=uploads]');
  await page.route('**/api/export', asTheStudio);
  const [dl, req] = await Promise.all([page.waitForEvent('download', { timeout: 8000 }), page.waitForRequest('**/api/export'), page.click('#export')]);
  const name = dl.suggestedFilename();
  const file = await dl.path();
  const zip = file ? readFileSync(file).subarray(0, 2).toString() : '';
  check('«Скачать бэкап» asks with X-Studio-Client and saves the zip', req.headers()['x-studio-client'] === 'ui'
    && /^rich-studio-\d{8}-\d{4}\.zip$/.test(name) && zip === 'PK', JSON.stringify({ header: req.headers()['x-studio-client'], name, zip }));
  await page.unroute('**/api/export', asTheStudio);

  // a refusal is a toast, not a file with the error in it
  let downloads = 0;
  const count = () => { downloads++; };
  const admin = (/** @type {import('playwright').Route} */ route) => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'у токена e2e нет права admin' }) });
  await page.route('**/api/export', admin);
  page.on('download', count);
  await page.click('#export');
  const toast = await page.waitForFunction(() => [...document.querySelectorAll('#toasts .toast.err')].some((t) => (t.textContent || '').includes('нет права admin')), null, { timeout: 8000 }).then(() => true, () => false);
  const enabled = await page.isEnabled('#export');
  check('A refused backup shows the reason and downloads nothing', toast && downloads === 0 && enabled, JSON.stringify({ toast, downloads, enabled }));
  page.off('download', count);
  await page.unroute('**/api/export', admin);

  const links = [];
  for (const tab of ['drafts', 'posts', 'uploads']) {
    await page.click(`#side-tabs [data-tab=${tab}]`);
    links.push(...await page.evaluate(() => [...document.querySelectorAll('[href*="/api/"], [src*="/api/"], [action*="/api/"]')]
      .map((e) => e.tagName + ' ' + (e.getAttribute('href') || e.getAttribute('src') || e.getAttribute('action')))));
  }
  check('No link, image or form of the editor points at /api/', links.length === 0, links.join(', '));
  await page.click('#side-tabs [data-tab=drafts]');

  const docs = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  await docs.goto(BASE + '/docs');
  await docs.waitForSelector('.opblock');
  const op = docs.locator('#operations-service-config_api_config_get');
  await op.locator('.opblock-summary').first().click();
  await op.locator('.try-out__btn').click();
  const sent = docs.waitForRequest((r) => r.url().endsWith('/api/config'));
  await op.locator('.execute').click();
  const client = (await sent).headers()['x-studio-client'];
  check('«Try it out» in /docs names itself in X-Studio-Client', client === 'docs', String(client));
  await docs.close();
}
