// the health badge in the header follows /api/health and opens the list of checks
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  await page.waitForFunction(() => { const b = document.querySelector('#health')?.shadowRoot?.querySelector('button'); return b && !b.classList.contains('unknown'); }, null, { timeout: 8000 }).catch(() => {});
  const want = await page.evaluate(async () => (await fetch('/api/health')).json());
  const badge = await page.evaluate(() => { const b = document.querySelector('#health').shadowRoot.querySelector('button'); return { cls: b.className, text: b.textContent.trim(), title: b.title }; });
  await page.click('#health');
  const dlg = await page.waitForSelector('dialog.dlg', { timeout: 3000 }).then((d) => d.textContent()).catch(() => '');
  await page.keyboard.press('Escape');
  check('Health badge shows /api/health and lists the checks',
    badge.cls === want.status && badge.text === (want.problems.length ? String(want.problems.length) : '') && want.problems.every((p) => badge.title.includes(p))
      && want.checks.every((c) => dlg.includes(c.name)), JSON.stringify({ badge, status: want.status, dlg: dlg.slice(0, 200) }));
}
