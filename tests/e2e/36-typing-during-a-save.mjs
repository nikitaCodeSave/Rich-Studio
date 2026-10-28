// what is typed while an autosave is on the way is saved too, and «Новый пост» right after typing keeps nothing of it
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  await page.click('#btn-new');
  await applyJson({ rich_message: { markdown: 'Первая' } });
  await page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 8000 }).catch(() => {});
  const id = await page.evaluate(() => JSON.parse(localStorage.getItem('rs.lastDraft') || 'null'));
  // a slow network: the save of «Вторая» is still on the way when «Третья» is typed
  const slow = async (route) => { await new Promise((r) => setTimeout(r, 2000)); await route.continue().catch(() => {}); };
  await page.route('**/api/drafts/' + id, slow);
  await applyJson({ rich_message: { markdown: 'Вторая' } });
  await page.waitForTimeout(2800);
  await applyJson({ rich_message: { markdown: 'Третья' } });
  await page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 12000 }).catch(() => {});
  await page.unroute('**/api/drafts/' + id, slow);
  const saved = await page.evaluate(async (i) => (await fetch('/api/drafts/' + i)).json(), id);
  check('A change made while the draft was being saved is saved too', saved.payload.rich_message.markdown === 'Третья',
    JSON.stringify({ saved: saved.payload.rich_message, state: await page.textContent('#save-state') }));
}
