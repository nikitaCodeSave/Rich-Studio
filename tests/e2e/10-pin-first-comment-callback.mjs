// pin + first comment + callback answer, then a click arrives through getUpdates
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push, until, applyJson }) {
  const calls = async (method) => (await mockLog()).filter((c) => c.method === method);
  const posts = () => page.evaluate(() => fetch('/api/posts').then((r) => r.json()));
  await applyJson({ rich_message: { html: '<p>Голосуем</p>' }, reply_markup: { inline_keyboard: [[{ text: '👍', callback_data: 'like' }]] } });
  await page.click('#panel-kb summary');
  await page.fill('.cb-answer input[type=text]', 'Спасибо, учтено');
  await page.click('#panel-after summary');
  await page.check('#after input[type=checkbox]');
  await page.fill('#comment-src', '<p>Обсуждение тут 👇</p>');
  await page.waitForSelector('#preview .tg-comment-label', { state: 'attached', timeout: 5000 }).catch(() => {});
  const commentShown = await page.locator('#preview .tg-comment-label').count();
  const pinsBefore = (await calls('pinChatMessage')).length;
  await page.click('#btn-publish');
  // the pin goes right after the send: once it is in the mock's log, the post is stored
  await until(async () => (await calls('pinChatMessage')).length > pinsBefore);
  const sentRich = (await calls('sendRichMessage')).pop();
  const pinned = (await calls('pinChatMessage')).slice(pinsBefore).pop();
  await push({ callback_query: { id: 'cq-e2e', data: 'like', message: { message_id: 1, chat: { id: -1001 } } } }); // unknown message: answered, not counted
  const mine = await until(async () => (await posts()).find((p) => p.comment_status === 'waiting'));
  await push({ callback_query: { id: 'cq-e2e2', data: 'like', message: { message_id: mine && (await page.evaluate((id) => fetch('/api/posts/' + id).then((r) => r.json()), mine.id)).message_id, chat: { id: -1001 } } } });
  // the click is counted before it is answered, so the answer in the log means the count is stored
  const answer = await until(async () => (await calls('answerCallbackQuery')).find((c) => c.body.callback_query_id === 'cq-e2e2'), 15000);
  await page.click('#side-tabs [data-tab=posts]');
  await page.waitForFunction(() => { const t = document.querySelector('#posts li')?.textContent || ''; return /нажатия: 👍 1/.test(t) && /ждёт пересылки/.test(t); }, null, { timeout: 8000 }).catch(() => {});
  const statsText = await page.locator('#posts li').first().textContent();
  check('Pin, first comment and callback answer work end to end',
    commentShown === 1 && !sentRich.body.pin && !sentRich.body.comment && !!pinned && answer && answer.body.text === 'Спасибо, учтено' && /нажатия: 👍 1/.test(statsText) && /ждёт пересылки/.test(statsText),
    JSON.stringify({ commentShown, pinned: !!pinned, answer: answer && answer.body, statsText }));
}
