// agent link: /?draft=<id> opens that draft; a newer version from an agent shows up by itself or asks
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, BASE }) {
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const text = (t) => ({ rich_message: { blocks: [{ type: 'paragraph', text: t }] } });
  const d = await api('POST', '/api/drafts', { title: 'От агента', post: text('Первая версия') });
  await page.goto(`${BASE}/?draft=${d.id}`);
  await page.waitForSelector('.block');
  await page.waitForTimeout(600);
  const opened = await page.inputValue('#title');
  const addr = new URL(page.url()).search;
  const v2 = await api('PUT', '/api/drafts/' + d.id, { title: 'От агента 2', post: text('Вторая версия'), version: d.version });
  await page.waitForTimeout(5000); // the editor looks every 4 s
  const reloaded = await page.inputValue('#title');
  const undoBack = await page.locator('#btn-undo').isEnabled();
  const reloadedText = await page.evaluate(() => document.querySelector('#preview').shadowRoot.textContent);
  await page.fill('#title', 'Моя правка');
  await api('PUT', '/api/drafts/' + d.id, { title: 'От агента 3', post: text('Третья версия'), version: v2.version });
  await page.waitForSelector('dialog[open]', { timeout: 8000 }).catch(() => {});
  const asked = (await page.locator('dialog[open]').textContent().catch(() => '')) || '';
  await page.locator('dialog[open] button', { hasText: 'Открыть их версию' }).click().catch(() => {});
  await page.waitForTimeout(600);
  const theirs = await page.inputValue('#title');
  check('A draft link opens the draft; an agent\'s new version reloads it or asks when there are unsaved edits',
    opened === 'От агента' && addr === '?draft=' + d.id && reloaded === 'От агента 2' && /Вторая версия/.test(reloadedText) && undoBack && /Черновик изменился/.test(asked) && theirs === 'От агента 3',
    JSON.stringify({ opened, addr, reloaded, undoBack, asked: asked.slice(0, 80), theirs }));
}
