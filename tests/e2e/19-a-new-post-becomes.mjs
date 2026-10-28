// a new post becomes a draft without Ctrl+S; a draft saved elsewhere meanwhile is not overwritten
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await applyJson({ rich_message: { markdown: 'Сохранюсь сам' } });
  await page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 8000 }).catch(() => {});
  const autoId = await page.evaluate(() => JSON.parse(localStorage.getItem('rs.lastDraft') || 'null'));
  const autoDraft = autoId ? await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId) : null;
  check('A new post is saved as a draft on its own', !!autoDraft && autoDraft.payload.rich_message.markdown === 'Сохранюсь сам', String(autoId));
  // "another tab" saves the same draft, then this tab keeps typing
  await page.evaluate(async (id) => fetch('/api/drafts/' + id, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'из другой вкладки', post: { rich_message: { markdown: 'Чужая правка' } } }) }), autoId);
  await applyJson({ rich_message: { markdown: 'Моя правка' } });
  await page.waitForSelector('dialog[open] >> text=Черновик изменился', { timeout: 8000 }).catch(() => {});
  const conflictShown = await page.locator('dialog[open] >> text=Черновик изменился').count();
  const untouched = await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId);
  await page.click('dialog[open] >> text=Сохранить мою поверх').catch(() => {});
  await page.waitForTimeout(500);
  const mine = await page.evaluate(async (id) => (await fetch('/api/drafts/' + id)).json(), autoId);
  check('A draft changed in another tab is not overwritten silently',
    conflictShown === 1 && untouched.payload.rich_message.markdown === 'Чужая правка' && mine.payload.rich_message.markdown === 'Моя правка',
    JSON.stringify({ conflictShown, untouched: untouched.payload.rich_message, mine: mine.payload.rich_message }));
}
