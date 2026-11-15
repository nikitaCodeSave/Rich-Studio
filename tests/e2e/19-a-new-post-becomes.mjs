// a new post becomes a draft without Ctrl+S; a draft saved elsewhere meanwhile is not overwritten
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ BASE, page, check, applyJson }) {
  const saved = () => page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 8000 }).catch(() => {});
  await page.click('#btn-new');
  await applyJson({ rich_message: { markdown: 'Сохранюсь сам' } });
  await saved();
  const autoId = await page.evaluate(() => JSON.parse(localStorage.getItem('rs.lastDraft') || 'null'));
  const autoDraft = autoId ? await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId) : null;
  check('A new post is saved as a draft on its own', !!autoDraft && autoDraft.payload.rich_message.markdown === 'Сохранюсь сам', String(autoId));
  // this tab types, and "another tab" saves the same draft while that is still unsaved here. The other order races
  // the editor's 4 s watch (watchDrafts): a tab with nothing unsaved just shows the newer version, nothing to ask (#134).
  // This tab's own save waits at the network until the other one is in, so it always carries the older version
  let otherSaved = () => {};
  const otherIn = new Promise((r) => { otherSaved = r; });
  const hold = async (route) => { if (route.request().method() === 'PUT') await otherIn; await route.continue().catch(() => {}); };
  await page.route('**/api/drafts/' + autoId, hold);
  await applyJson({ rich_message: { markdown: 'Моя правка' } });
  await page.waitForFunction(() => /есть изменения/.test(document.querySelector('#save-state').textContent), null, { timeout: 8000 }).catch(() => {});
  // from outside the page, as another tab would, so the hold above does not catch it
  await fetch(BASE + '/api/drafts/' + autoId, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'из другой вкладки', post: { rich_message: { markdown: 'Чужая правка' } } }) });
  otherSaved();
  await page.waitForSelector('dialog[open] >> text=Черновик изменился', { timeout: 8000 }).catch(() => {});
  const conflictShown = await page.locator('dialog[open] >> text=Черновик изменился').count();
  const untouched = await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId);
  await page.click('dialog[open] >> text=Сохранить мою поверх').catch(() => {});
  await saved();
  await page.unroute('**/api/drafts/' + autoId, hold);
  const mine = await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId);
  check('A draft changed in another tab is not overwritten silently',
    conflictShown === 1 && untouched.payload.rich_message.markdown === 'Чужая правка' && mine.payload.rich_message.markdown === 'Моя правка',
    JSON.stringify({ conflictShown, untouched: untouched.payload.rich_message, mine: mine.payload.rich_message }));
}
