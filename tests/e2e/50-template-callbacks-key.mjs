// post templates (#184): a key of callbacks is a button's callback_data, not a field's name, in the template form as in
// services/templates.py _key: a string answer to callback_data "url" becomes a text field and the template saves; one to
// "html" is a plain string, not HTML, and ✕ puts it back as it was
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const quick = { timeout: 8000 };
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  /** what the form would send: {title, post, fields} */
  const formValue = () => page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form')).value);
  const fieldsAre = (n) => page.waitForFunction((x) => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.fields?.length === x, n, quick).catch(() => {});
  const title = 'Ответы кнопок ' + (Date.now() % 100000); // its own template, also on a reused database
  const thanks = 'Спасибо за голос';
  const markup = '<b>Не</b> разметка & текст';
  await page.click('#btn-new');
  await applyJson({
    rich_message: { html: '<p>Вопрос дня</p>' },
    reply_markup: { inline_keyboard: [[{ text: 'Да', callback_data: 'url' }, { text: 'Нет', callback_data: 'html' }]] },
    callbacks: { url: thanks, html: markup },
  });
  await page.click('#side-tabs [data-tab=drafts]');
  await page.evaluate(() => { /** @type {any} */ (document.querySelector('#templates')).open = true; });
  await page.locator('#templates button', { hasText: 'Текущий пост шаблоном' }).click();
  const form = page.locator('dialog[open] rs-template-form');
  await form.locator('input[name=title]').fill(title);
  const spots = await form.locator('li .value').allTextContents();

  // callback_data "url": a text field, not a link
  await form.locator('li', { hasText: thanks }).locator('button').click();
  await fieldsAre(1);
  const urlType = await form.locator('.field select').first().evaluate((s) => /** @type {HTMLSelectElement} */ (s).selectedOptions[0]?.textContent);
  // callback_data "html": the whole string is one spot, its default is the string as it stands
  const whole = form.locator('li', { has: page.locator('.value', { hasText: markup }) });
  await whole.locator('button').click({ timeout: 3000 }).catch(() => {});
  await fieldsAre(2);
  const made = await formValue();
  // ✕ puts the answer back unescaped, «→ поле» makes the field again
  await form.locator('.field button.x').last().click();
  await fieldsAre(1);
  const restored = (await formValue()).post.callbacks;
  await whole.locator('button').click({ timeout: 3000 }).catch(() => {});
  await fieldsAre(2);

  await page.locator('dialog[open] .dlg-actions button', { hasText: 'Сохранить шаблон' }).click();
  // saved (the row shows up) or refused (the form shows the error)
  await page.waitForFunction((t) => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.error
    || [...(document.querySelector('#templates')?.shadowRoot?.querySelectorAll('li') || [])].some((li) => (li.textContent || '').includes(t)), title, quick).catch(() => {});
  const error = await page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.error || '');
  const saved = (await api('GET', '/api/templates')).find((t) => t.title === title);
  const draft = saved ? await api('POST', `/api/templates/${saved.id}/draft`, { version: saved.version, values: { text: 'Голос учтён', text_2: '<i>x</i> & y' } }) : null;
  if (draft && draft.id) await api('DELETE', '/api/drafts/' + draft.id);
  if (saved) await api('DELETE', `/api/templates/${saved.id}?version=${saved.version}`);
  if (await page.locator('dialog[open]').count()) await page.keyboard.press('Escape'); // a refused template leaves it open
  const answers = draft && draft.payload ? draft.payload.callbacks : null;

  check('Template form: a callbacks key is callback_data, not a field name: "url" makes a text field, "html" a plain string',
    urlType === 'Строка' && spots.includes(markup) && !spots.includes('Не')
      && made.fields[0]?.type === 'text' && made.fields[1]?.type === 'text' && made.fields[1]?.default === markup
      && made.post.callbacks.url === '{{text}}' && made.post.callbacks.html === '{{text_2}}' && restored.html === markup
      && !error && !!saved && saved.fields.map((f) => f.type).join() === 'text,text'
      && !!answers && answers.url === 'Голос учтён' && answers.html === '<i>x</i> & y',
    JSON.stringify({ spots, urlType, fields: made.fields, callbacks: made.post.callbacks, restored, error, saved: saved && saved.fields, answers }));
}
