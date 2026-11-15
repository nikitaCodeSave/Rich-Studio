// A callback button gets a rich reply (Bot API 10.4) in its own dialog with a preview; a press sends it as an
// ephemeral sendRichMessage before answerCallbackQuery; a refused reply is not repeated and shows next to the clicks
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push, until, applyJson, MOCK }) {
  const quick = { timeout: 5000 };
  const calls = async (method) => (await mockLog()).filter((c) => c.method === method);
  const getPost = (id) => page.evaluate((x) => fetch('/api/posts/' + x).then((r) => r.json()), id);
  await page.click('#btn-new');
  await applyJson({ rich_message: { html: '<p>Узнайте ответ</p>' }, reply_markup: { inline_keyboard: [[{ text: '🔑 Ответ', callback_data: 'key' }]] }, callbacks: { key: 'Смотрите ниже' } });
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-kb')).open = true; });
  const card = page.locator('#kb .kb-btn').first();
  await card.locator('.cb-reply button').click();
  await page.waitForSelector('dialog[open] #reply-src', quick);
  await page.fill('#reply-src', '<p><b>Секрет</b> только для вас: <code>RICH2026</code></p>');
  await page.check('#reply-replace');
  // the reply is drawn by RichRender inside <rs-preview>: its own shadow root, the sanitizer's DOM
  await page.waitForFunction(() => {
    const pv = document.querySelector('dialog[open] rs-preview');
    return pv?.shadowRoot?.querySelector('.tg-body b')?.textContent === 'Секрет' && /вместо поста/.test(pv.shadowRoot.textContent || '');
  }, null, quick).catch(() => {});
  const shown = await page.evaluate(() => {
    const root = document.querySelector('dialog[open] rs-preview')?.shadowRoot;
    return { bold: root?.querySelector('.tg-body b')?.textContent, code: root?.querySelector('.tg-body code')?.textContent, title: root?.querySelector('.tg-channel')?.textContent };
  });
  // the dialog lists what the check says about this button: a good reply passes
  await page.waitForSelector('dialog[open] .reply-issues .ok >> text=проходит проверку', quick).catch(() => {});
  const issues = await page.locator('dialog[open] .reply-issues .e').count();
  const passes = await page.locator('dialog[open] .reply-issues .ok').count();
  await page.screenshot({ path: 'test-results/ui/reply-dialog-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => innerWidth === 390, null, quick).catch(() => {});
  await page.screenshot({ path: 'test-results/ui/reply-dialog-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.click('dialog[open] button[value=ok]:has-text("Готово")');
  const summary = await card.locator('.cb-reply span').textContent();

  const sendsBefore = (await calls('sendRichMessage')).length;
  await page.click('#btn-publish');
  await until(async () => (await calls('sendRichMessage')).length > sendsBefore);
  const posted = (await calls('sendRichMessage')).slice(sendsBefore)[0];
  const post = await until(async () => (await page.evaluate(() => fetch('/api/posts').then((r) => r.json()))).find((p) => p.title === 'Узнайте ответ'));
  const mid = (await getPost(post.id)).message_id;

  // a press: the ephemeral reply first, then the answer, both for the same press
  const press = (id) => push({ callback_query: { id, data: 'key', from: { id: 5 }, message: { message_id: mid, chat: { id: -1001 } } } });
  await press('cq-reply-1');
  const answer = await until(async () => (await calls('answerCallbackQuery')).find((c) => c.body.callback_query_id === 'cq-reply-1'), 15000);
  const log = await mockLog();
  const replyAt = log.findIndex((c) => c.method === 'sendRichMessage' && c.body.ephemeral_message_parameters?.callback_query_id === 'cq-reply-1');
  const answerAt = log.findIndex((c) => c.method === 'answerCallbackQuery' && c.body.callback_query_id === 'cq-reply-1');
  const reply = replyAt >= 0 ? log[replyAt].body : null;

  // Telegram refuses the reply: no second try, the answer still goes, the post counts the failure next to its clicks
  await fetch(MOCK + '/fail', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'sendRichMessage', description: 'Bad Request: query is too old' }) });
  await press('cq-reply-2');
  await until(async () => (await calls('answerCallbackQuery')).find((c) => c.body.callback_query_id === 'cq-reply-2'), 15000);
  await fetch(MOCK + '/fail', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ method: 'sendRichMessage', description: null }) });
  const tries = (await calls('sendRichMessage')).filter((c) => c.body.ephemeral_message_parameters?.callback_query_id === 'cq-reply-2').length;
  const rec = await until(async () => { const p = await getPost(post.id); return p.reply_errors && p.clicks?.key === 2 ? p : null; });
  // open the post for editing: its button card says how the presses went
  await page.click('#side-tabs [data-tab=drafts]');
  await page.click('#side-tabs [data-tab=posts]');
  await page.locator('#posts li', { hasText: '#' + mid }).getByRole('button', { name: 'Править' }).click();
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-kb')).open = true; });
  await page.waitForFunction(() => /Нажатий: 2 · rich-ответ не дошёл 1 раз/.test(document.querySelector('#kb .cb-answer')?.textContent || ''), null, quick).catch(() => {});
  const stats = await page.locator('#kb .cb-answer').first().textContent();
  await page.locator('#kb .kb-btn').first().screenshot({ path: 'test-results/ui/reply-card-1400.png' }).catch(() => {});
  await page.click('#btn-new'); // leave the editing of that post for the checks after this one

  check('Rich reply to a button: dialog with preview, ephemeral send before the answer, failure next to the clicks',
    shown.bold === 'Секрет' && shown.code === 'RICH2026' && /вместо поста/.test(shown.title || '') && issues === 0 && passes === 1
      && /^Rich-ответ вместо поста: Секрет только для вас/.test(summary || '')
      && JSON.stringify(posted.body.reply_markup) === JSON.stringify({ inline_keyboard: [[{ text: '🔑 Ответ', callback_data: 'key' }]] }) && posted.body.ephemeral_message_parameters === undefined
      && !!reply && reply.chat_id === undefined && reply.rich_message.html === '<p><b>Секрет</b> только для вас: <code>RICH2026</code></p>'
      && reply.ephemeral_message_parameters.replace_callback_query_message === true && replyAt < answerAt && answer && answer.body.text === 'Смотрите ниже' && answer.body.reply === undefined
      && tries === 1 && rec && rec.reply_errors.key.n === 1 && /query is too old/.test(rec.reply_errors.key.error) && /Нажатий: 2 · rich-ответ не дошёл 1 раз/.test(stats || ''),
    JSON.stringify({ shown, issues, passes, summary, reply, replyAt, answerAt, answer: answer && answer.body, tries, reply_errors: rec && rec.reply_errors, stats }));
}
