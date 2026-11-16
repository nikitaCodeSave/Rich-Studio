// post templates (#188): the template form offers as a field only what the service takes. A caption next to its
// parse_mode is not offered (services/templates.py _formatted_by), a button's callback_data with an answer in callbacks
// is listed with why instead of «→ поле». And ✕ puts an HTML string back byte for byte: &amp; in the text and in href
// of rich_message.html, &quot; in the first comment, &lt; in the rich reply to a button. Every string it does offer,
// made a field, the service saves
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const quick = { timeout: 8000 };
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const title = 'Что предлагает окно ' + (Date.now() % 100000); // its own template, also on a reused database
  /** what the form would send: {title, post, fields} */
  const formValue = () => page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form')).value);
  const fieldsAre = (n) => page.waitForFunction((x) => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.fields?.length === x, n, quick).catch(() => {});
  const post = {
    rich_message: {
      html: '<p>Кофе &amp; чай</p><p><a href="https://e.example/menu?a=1&amp;b=2">Меню</a></p><p><img src="tg://photo?id=p1"></p><p><img src="tg://photo?id=p2"></p>',
      media: [
        { id: 'p1', media: { type: 'photo', media: 'https://e.example/p1.jpg', caption: '<b>Размеченная</b> подпись', parse_mode: 'HTML' } },
        { id: 'p2', media: { type: 'photo', media: 'https://e.example/p2.jpg', caption: 'Простая подпись' } },
      ],
    },
    comment: { html: '<p>Он сказал &quot;да&quot;</p>' },
    reply_markup: { inline_keyboard: [[{ text: 'Голос', callback_data: 'vote' }, { text: 'Без ответа', callback_data: 'free' }]] },
    callbacks: { vote: { text: 'Спасибо', reply: { rich_message: { html: '<p>1 &lt; 2</p>' } } } },
  };
  await page.click('#btn-new');
  await applyJson(post);
  await page.click('#side-tabs [data-tab=drafts]');
  await page.evaluate(() => { /** @type {any} */ (document.querySelector('#templates')).open = true; });
  await page.locator('#templates button', { hasText: 'Текущий пост шаблоном' }).click();
  const form = page.locator('dialog[open] rs-template-form');
  await form.locator('li').first().waitFor(quick);
  const original = (await formValue()).post;
  const rows = await form.locator('li').evaluateAll((lis) => lis.map((li) => ({
    value: li.querySelector('.value')?.textContent || '', field: !!li.querySelector('button'), why: li.querySelector('.why')?.textContent || '',
  })));
  const row = (value) => rows.find((r) => r.value === value);
  // the row with why instead of «→ поле», as the person sees it
  await form.locator('li .why').first().scrollIntoViewIfNeeded().catch(() => {});
  for (const width of [1400, 390]) {
    await page.setViewportSize({ width, height: width === 1400 ? 900 : 844 });
    await page.screenshot({ path: `test-results/ui/template-suggestions-${width}.png` }).catch(() => {});
  }
  await page.setViewportSize({ width: 1400, height: 900 });
  const photo = post.rich_message.media[0].media;

  // 1. a string next to parse_mode (in InputMediaPhoto: its caption, its file) is never offered; a caption without it
  // is, and so is the id of the media next to it
  // 2. callback_data "vote" has an answer: listed with why, no «→ поле»; "free" has none and is offered
  check('Template form: a caption next to parse_mode and a callback_data with an answer in callbacks are not offered as a field',
    [photo.caption, photo.media, photo.parse_mode].every((v) => !row(v)) && !!row('Простая подпись')?.field
      && !!row('https://e.example/p2.jpg')?.field && !!row('p1')?.field
      && !!row('vote') && !row('vote')?.field && (row('vote')?.why || '').includes('связан с ответом на нажатие в callbacks')
      && !!row('free')?.field,
    JSON.stringify(rows));

  // 3. «→ поле» on four HTML strings with entities, then ✕ on each: the post is back as it was, byte for byte
  const values = ['Кофе & чай', 'https://e.example/menu?a=1&b=2', 'Он сказал "да"', '1 < 2'];
  for (const [i, v] of values.entries()) {
    await form.locator('li').filter({ has: page.getByText(v, { exact: true }) }).locator('button').click(quick).catch(() => {});
    await fieldsAre(i + 1);
  }
  const made = await formValue();
  for (let i = values.length; i > 0; i--) {
    await form.locator('.field button.x').first().click(quick).catch(() => {});
    await fieldsAre(i - 1);
  }
  const back = (await formValue()).post;
  const sources = (p) => [p.rich_message.html, p.comment.html, p.callbacks.vote.reply.rich_message.html];
  check('Template form: ✕ puts &amp;, &quot; and &lt; back in HTML byte for byte (text, href, first comment, rich reply)',
    JSON.stringify(sources(original)) === JSON.stringify([post.rich_message.html, post.comment.html, post.callbacks.vote.reply.rich_message.html])
      && made.fields.map((f) => f.default).join('|') === values.join('|') && sources(made.post).every((s) => /\{\{\w+\}\}/.test(s))
      && JSON.stringify(sources(back)) === JSON.stringify(sources(original)) && JSON.stringify(back) === JSON.stringify(original),
    JSON.stringify({ fields: made.fields.map((f) => f.default), made: sources(made.post), back: sources(back), original: sources(original) }));

  // 4. every string offered becomes a field, and the service takes the template
  const offered = await form.locator('li button').count();
  for (let i = 1; i <= offered; i++) {
    await form.locator('li button').first().click(quick).catch(() => {});
    await fieldsAre(i);
  }
  await form.locator('input[name=title]').fill(title);
  await page.locator('dialog[open] .dlg-actions button', { hasText: 'Сохранить шаблон' }).click();
  // saved (the row shows up) or refused (the form shows the error)
  await page.waitForFunction((t) => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.error
    || [...(document.querySelector('#templates')?.shadowRoot?.querySelectorAll('li') || [])].some((li) => (li.textContent || '').includes(t)), title, quick).catch(() => {});
  const error = await page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.error || '');
  const saved = (await api('GET', '/api/templates')).find((t) => t.title === title);
  if (saved) await api('DELETE', `/api/templates/${saved.id}?version=${saved.version}`);
  if (await page.locator('dialog[open]').count()) await page.keyboard.press('Escape'); // a refused template leaves it open
  check('Template form: every string it offers, made a field, the service saves',
    offered > 5 && !error && !!saved && saved.fields.length === offered, JSON.stringify({ offered, error, fields: saved && saved.fields.map((f) => f.default) }));
}
