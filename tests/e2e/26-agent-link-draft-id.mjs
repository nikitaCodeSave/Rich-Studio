// agent link: /?draft=<id> opens that draft; a newer version from an agent shows up by itself or asks
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, BASE }) {
  // the agent is outside the page, so holding the page's own saves below never holds the agent's
  const api = (method, path, body) => fetch(BASE + path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).then((r) => r.json());
  const title = (t) => page.waitForFunction((x) => /** @type {HTMLInputElement} */ (document.querySelector('#title')).value === x, t, { timeout: 12000 }).catch(() => {});
  const text = (t) => ({ rich_message: { blocks: [{ type: 'paragraph', text: t }] } });
  const d = await api('POST', '/api/drafts', { title: 'От агента', post: text('Первая версия') });
  await page.goto(`${BASE}/?draft=${d.id}`);
  await page.waitForSelector('.block');
  await title('От агента');
  const opened = await page.inputValue('#title');
  const addr = new URL(page.url()).search;
  const v2 = await api('PUT', '/api/drafts/' + d.id, { title: 'От агента 2', post: text('Вторая версия'), version: d.version });
  // the editor looks every 4 s, then reads the list and the draft
  await page.waitForFunction(() => /** @type {HTMLInputElement} */ (document.querySelector('#title')).value === 'От агента 2'
    && document.querySelector('#preview').shadowRoot.textContent.includes('Вторая версия'), null, { timeout: 12000 }).catch(() => {});
  const reloaded = await page.inputValue('#title');
  const undoBack = await page.locator('#btn-undo').isEnabled();
  const reloadedText = await page.evaluate(() => document.querySelector('#preview').shadowRoot.textContent);
  // this tab's save of «Моя правка» waits at the network until the agent's version is in, as in 19
  let agentSaved = () => {};
  const agentIn = new Promise((r) => { agentSaved = r; });
  const hold = async (route) => { if (route.request().method() === 'PUT') await agentIn; await route.continue().catch(() => {}); };
  await page.route('**/api/drafts/' + d.id, hold);
  await page.fill('#title', 'Моя правка');
  await api('PUT', '/api/drafts/' + d.id, { title: 'От агента 3', post: text('Третья версия'), version: v2.version });
  agentSaved();
  await page.waitForSelector('dialog[open]', { timeout: 8000 }).catch(() => {});
  const asked = (await page.locator('dialog[open]').textContent().catch(() => '')) || '';
  await page.locator('dialog[open] button', { hasText: 'Открыть их версию' }).click().catch(() => {});
  await title('От агента 3');
  await page.unroute('**/api/drafts/' + d.id, hold);
  const theirs = await page.inputValue('#title');
  check('A draft link opens the draft; an agent\'s new version reloads it or asks when there are unsaved edits',
    opened === 'От агента' && addr === '?draft=' + d.id && reloaded === 'От агента 2' && /Вторая версия/.test(reloadedText) && undoBack && /Черновик изменился/.test(asked) && theirs === 'От агента 3',
    JSON.stringify({ opened, addr, reloaded, undoBack, asked: asked.slice(0, 80), theirs }));
}
