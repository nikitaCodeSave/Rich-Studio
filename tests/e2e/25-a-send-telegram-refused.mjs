// a send Telegram refused stays in «Посты» with its first line and the error
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, until, setChat, applyJson, MOCK }) {
  const fail = (description) => fetch(MOCK + '/fail', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'sendRichMessage', description }) });
  const title = 'Пост, который не вышел';
  await fail("Bad Request: inline keyboard button URL 'https://' is invalid: URL host is empty");
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: title }] } });
  await setChat('@refused');
  await page.click('#btn-publish');
  // the studio has stored the refusal: only then the mock may answer sends again
  await until(() => page.evaluate((t) => fetch('/api/posts').then((r) => r.json()).then((ps) => ps.some((p) => p.status === 'failed' && (p.title || '').includes(t))), title));
  await fail(null);
  // the tab and the filter each reload the list: one after the other, or the unfiltered list may land last
  await page.click('#side-tabs [data-tab=posts]');
  await page.waitForFunction((t) => (document.querySelector('#posts li.post-row')?.textContent || '').includes(t), title, { timeout: 8000 }).catch(() => {});
  await page.selectOption('#posts-filter', 'failed');
  await page.waitForFunction(() => { const rows = [...document.querySelectorAll('#posts li.post-row')]; return rows.length > 0 && rows.every((r) => /ошибка/.test(r.textContent || '')); }, null, { timeout: 8000 }).catch(() => {});
  const rows = await page.locator('#posts li.post-row').allTextContents();
  await page.selectOption('#posts-filter', '');
  await page.click('#side-tabs [data-tab=drafts]');
  check('A refused send is listed in «Посты» with its title and the error',
    rows.length >= 1 && rows.every((t) => /ошибка/.test(t)) && /Пост, который не вышел/.test(rows[0]) && /@refused/.test(rows[0]) && /Не вышел: .*URL host is empty/.test(rows[0]),
    JSON.stringify(rows).slice(0, 300));
}
