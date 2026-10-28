// a send Telegram refused stays in «Посты» with its first line and the error
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, setChat, applyJson, MOCK }) {
  const fail = (description) => fetch(MOCK + '/fail', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'sendRichMessage', description }) });
  await fail("Bad Request: inline keyboard button URL 'https://' is invalid: URL host is empty");
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Пост, который не вышел' }] } });
  await setChat('@refused');
  await page.click('#btn-publish');
  await page.waitForTimeout(800);
  await fail(null);
  await page.click('#side-tabs [data-tab=posts]');
  await page.selectOption('#posts-filter', 'failed');
  await page.waitForTimeout(300);
  const rows = await page.locator('#posts li.post-row').allTextContents();
  await page.selectOption('#posts-filter', '');
  await page.click('#side-tabs [data-tab=drafts]');
  check('A refused send is listed in «Посты» with its title and the error',
    rows.length >= 1 && rows.every((t) => /ошибка/.test(t)) && /Пост, который не вышел/.test(rows[0]) && /@refused/.test(rows[0]) && /Не вышел: .*URL host is empty/.test(rows[0]),
    JSON.stringify(rows).slice(0, 300));
}
