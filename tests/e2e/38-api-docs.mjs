// /docs: Swagger UI from the studio's own files draws under its CSP, nothing leaves the studio (no CDN, no
// validator), and a key typed into «Authorize» goes with «Try it out» to our API
import { mkdirSync } from 'node:fs';

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ browser, check, BASE }) {
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const problems = [];
  const outside = [];
  page.on('pageerror', (e) => problems.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error' || /Content Security Policy/i.test(m.text())) problems.push(m.text()); });
  page.on('request', (r) => { if (new URL(r.url()).origin !== new URL(BASE).origin && !r.url().startsWith('data:')) outside.push(r.url()); });
  await page.goto(BASE + '/docs');
  await page.waitForSelector('.opblock');
  const validator = await page.evaluate(() => /** @type {any} */ (window).ui.getConfigs().validatorUrl);
  mkdirSync('test-results/ui', { recursive: true });
  await page.screenshot({ path: 'test-results/ui/docs-1400.png' });

  await page.click('.auth-wrapper .authorize');
  const form = page.locator('.auth-container').filter({ hasText: 'X-API-Key' });
  await form.locator('input').fill('e2e-docs-key');
  await form.locator('button[type=submit]').click();
  await page.locator('.modal-ux .close-modal').click();
  const op = page.locator('#operations-service-config_api_config_get');
  await op.locator('.opblock-summary').first().click();
  await op.locator('.try-out__btn').click();
  const sent = page.waitForRequest((r) => r.url().endsWith('/api/config'));
  await op.locator('.execute').click();
  const key = (await sent).headers()['x-api-key'];
  const status = op.locator('.live-responses-table tbody .response-col_status');
  await status.waitFor();
  const shown = (await status.textContent())?.trim();

  check('API docs draw from the studio under its CSP, nothing goes outside, Try it out sends the key',
    problems.length === 0 && outside.length === 0 && validator === null && key === 'e2e-docs-key' && shown === '200',
    JSON.stringify({ problems, outside, validator, key, shown }));
  await page.close();
}
