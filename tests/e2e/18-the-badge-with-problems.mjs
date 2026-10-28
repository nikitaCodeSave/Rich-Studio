// the badge with problems, a studio that does not answer, and one that wants the key (no prompt from polling)
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  const read = () => page.evaluate(() => { const b = document.querySelector('#health').shadowRoot.querySelector('button'); return { cls: b.className, text: b.textContent.trim() }; });
  const poll = () => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  // wait for the class and the count together: the real /api/health of a test studio is already «warn» (no spec
  // download), so the class alone could match before the routed answer arrives
  const settle = (cls, text = '') => page.waitForFunction(([c, t]) => {
    const b = document.querySelector('#health').shadowRoot.querySelector('button');
    return b.className === c && b.textContent.trim() === t;
  }, [cls, text], { timeout: 5000 }).catch(() => {});
  let prompts = 0;
  const onDialog = (d) => { prompts++; d.dismiss(); };
  page.on('dialog', onDialog);
  await page.route('**/api/health', async (route) => {
    const res = await route.fetch().catch(() => null);
    if (!res) return;
    const r = await res.json();
    r.status = 'warn'; r.problems = ['posts: 1 постов требуют внимания', 'backup: свежей копии нет'];
    await route.fulfill({ json: r }).catch(() => {}); // a poll still in flight when the test unroutes
  });
  await poll(); await settle('warn', '2');
  const warn = await read();
  await page.unroute('**/api/health');
  await page.route('**/api/health', (route) => route.abort());
  await poll(); await settle('down');
  const down = await read();
  await page.click('#health');
  const downDlg = await page.waitForSelector('dialog.dlg', { timeout: 3000 }).then((d) => d.textContent()).catch(() => '');
  await page.keyboard.press('Escape');
  await page.unroute('**/api/health');
  await page.route('**/api/health', (route) => route.fulfill({ status: 401, json: { detail: 'нужен ключ' } }));
  await poll(); await settle('locked');
  const locked = await read();
  await page.unroute('**/api/health');
  page.off('dialog', onDialog);
  await poll(); await settle('ok');
  check('Health badge: problems counted, down shows no stale report, 401 never prompts',
    warn.cls === 'warn' && warn.text === '2' && down.cls === 'down' && down.text === '' && /не ответила/.test(downDlg) && !/в порядке/.test(downDlg)
      && locked.cls === 'locked' && prompts === 0, JSON.stringify({ warn, down, downDlg: downDlg.slice(0, 120), locked, prompts }));
}
