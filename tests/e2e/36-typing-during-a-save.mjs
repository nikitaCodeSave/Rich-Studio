// what is typed while an autosave is on the way is saved too; «Новый пост» while a save is on the way
// neither writes the new post into the draft left behind nor duplicates it
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const delay = (ms) => async (route) => { await new Promise((r) => setTimeout(r, ms)); await route.continue().catch(() => {}); };
  const settled = () => page.waitForFunction(() => /сохранено/.test(document.querySelector('#save-state').textContent), null, { timeout: 15000 }).catch(() => {});
  const drafts = () => page.evaluate(async () => Promise.all((await (await fetch('/api/drafts')).json()).map(async (d) => (await fetch('/api/drafts/' + d.id)).json())));
  const named = async (title) => (await drafts()).filter((d) => d.title === title);
  const conflict = () => page.evaluate(() => [...document.querySelectorAll('dialog[open]')].some((d) => /Черновик изменился/.test(d.textContent)));

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

  // A: «Новый пост» while the PUT of an existing draft is on the way
  await page.fill('#title', 'Сценарий А');
  await applyJson({ rich_message: { markdown: 'А1' } });
  await settled();
  const a = await page.evaluate(() => JSON.parse(localStorage.getItem('rs.lastDraft') || 'null'));
  const slowA = delay(2000);
  await page.route('**/api/drafts/' + a, slowA);
  await applyJson({ rich_message: { markdown: 'А2' } });
  await page.waitForTimeout(2800); // the autosave of «А2» is on the way
  await applyJson({ rich_message: { markdown: 'А3' } });
  await page.click('#btn-new');
  await page.fill('#title', 'После А');
  await applyJson({ rich_message: { markdown: 'Новый А' } });
  await page.waitForTimeout(5000);
  await settled();
  await page.unroute('**/api/drafts/' + a, slowA);
  const leftA = await page.evaluate(async (i) => (await fetch('/api/drafts/' + i)).json(), a);
  const afterA = await named('После А');
  check('«Новый пост» during a PUT: the draft left behind keeps its last text, not the new post', leftA.title === 'Сценарий А' && leftA.payload.rich_message.markdown === 'А3',
    JSON.stringify({ title: leftA.title, payload: leftA.payload }));
  check('«Новый пост» during a PUT: the new post is a draft of its own', afterA.length === 1 && afterA[0].id !== a && afterA[0].payload.rich_message.markdown === 'Новый А',
    JSON.stringify(afterA.map((d) => [d.id, d.payload])));
  check('«Новый пост» during a PUT: no false conflict', !(await conflict()));

  // B: «Новый пост» while the first POST of a new draft is on the way
  await page.click('#btn-new');
  const slowPost = async (route) => { if (route.request().method() === 'POST') await new Promise((r) => setTimeout(r, 2000)); await route.continue().catch(() => {}); };
  await page.route('**/api/drafts', slowPost);
  await page.fill('#title', 'Сценарий Б');
  await applyJson({ rich_message: { markdown: 'Б1' } });
  await page.waitForTimeout(2800); // the first save of «Б1» is on the way
  await applyJson({ rich_message: { markdown: 'Б2' } });
  await page.click('#btn-new');
  await page.fill('#title', 'После Б');
  await applyJson({ rich_message: { markdown: 'Новый Б' } });
  await page.waitForTimeout(5000);
  await settled();
  await page.unroute('**/api/drafts', slowPost);
  const leftB = await named('Сценарий Б');
  const afterB = await named('После Б');
  check('«Новый пост» during the first POST: one draft left behind, with its last text', leftB.length === 1 && leftB[0].payload.rich_message.markdown === 'Б2',
    JSON.stringify(leftB.map((d) => [d.id, d.payload])));
  check('«Новый пост» during the first POST: the new post is a draft of its own', afterB.length === 1 && (!leftB.length || afterB[0].id !== leftB[0].id) && afterB[0].payload.rich_message.markdown === 'Новый Б',
    JSON.stringify(afterB.map((d) => [d.id, d.payload])));
  check('«Новый пост» during the first POST: no false conflict', !(await conflict()));
}
