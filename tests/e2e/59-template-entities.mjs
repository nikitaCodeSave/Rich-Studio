// template form (#197): HTML entities are decoded as Telegram does (16 names, case matters, numeric as measured), the
// same table as the preview (RichRender), not the browser's: data="a&mdash;b" with an answer a—b is not offered as a
// field, and &mdash; in a text comes back as it was after «→ поле» and ✕
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const quick = { timeout: 8000 };
  await page.click('#btn-new');
  await applyJson({
    rich_message: { html: '<p>Тире &mdash; тут</p><tg-button-row><tg-button data="a&mdash;b">Да</tg-button><tg-button data="x&NBSP;y">Нет</tg-button></tg-button-row>' },
    callbacks: { 'a—b': 'Спасибо' },
  });
  await page.click('#side-tabs [data-tab=drafts]');
  await page.evaluate(() => { /** @type {any} */ (document.querySelector('#templates')).open = true; });
  await page.locator('#templates button', { hasText: 'Текущий пост шаблоном' }).click();
  const form = page.locator('dialog[open] rs-template-form');
  await form.locator('li').first().waitFor(quick);
  const rows = await form.locator('li').evaluateAll((lis) => lis.map((li) => ({
    value: li.querySelector('.value')?.textContent || '', field: !!li.querySelector('button'), why: li.querySelector('.why')?.textContent || '',
  })));
  const answered = rows.find((r) => r.value === 'a—b');
  const kept = rows.find((r) => r.value === 'x&NBSP;y');
  check('Template form: data="a&mdash;b" with callbacks["a—b"] is not offered as a field; &NBSP; stays as typed',
    !!answered && !answered.field && /callbacks/.test(answered.why) && !!kept && kept.field, JSON.stringify(rows));
  const value = () => page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form')).value);
  await form.locator('li', { hasText: 'Тире' }).locator('button').first().click(quick);
  const filled = (await value()).post.rich_message.html;
  const field = (await page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form')).fields))[0];
  await form.locator('button', { hasText: '✕' }).first().click(quick).catch(() => {});
  const back = (await value()).post.rich_message.html;
  check('Template form: the text with &mdash; becomes a field with the dash, and ✕ puts it back as it was',
    /\{\{\w+\}\}/.test(filled) && field?.default === 'Тире — тут' && back.includes('<p>Тире &mdash; тут</p>'),
    JSON.stringify({ filled, field, back }));
  if (await page.locator('dialog[open]').count()) await page.keyboard.press('Escape');
}
