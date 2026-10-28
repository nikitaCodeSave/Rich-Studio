// pin + first comment + callback answer, then a click arrives through getUpdates
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push, applyJson }) {
  await applyJson({ rich_message: { html: '<p>Голосуем</p>' }, reply_markup: { inline_keyboard: [[{ text: '👍', callback_data: 'like' }]] } });
  await page.click('#panel-kb summary');
  await page.fill('.cb-answer input[type=text]', 'Спасибо, учтено');
  await page.click('#panel-after summary');
  await page.check('#after input[type=checkbox]');
  await page.fill('#comment-src', '<p>Обсуждение тут 👇</p>');
  await page.waitForTimeout(400);
  const commentShown = await page.locator('#preview .tg-comment-label').count();
  await page.click('#btn-publish');
  await page.waitForTimeout(800);
  let log = await mockLog();
  const sentRich = log.filter((c) => c.method === 'sendRichMessage').pop();
  const pinned = log.filter((c) => c.method === 'pinChatMessage').pop();
  await push({ callback_query: { id: 'cq-e2e', data: 'like', message: { message_id: 1, chat: { id: -1001 } } } }); // unknown message: answered, not counted
  const posts = await page.evaluate(() => fetch('/api/posts').then((r) => r.json()));
  const mine = posts.find((p) => p.comment_status === 'waiting');
  await push({ callback_query: { id: 'cq-e2e2', data: 'like', message: { message_id: mine && (await page.evaluate((id) => fetch('/api/posts/' + id).then((r) => r.json()), mine.id)).message_id, chat: { id: -1001 } } } });
  await page.waitForTimeout(3500);
  log = await mockLog();
  const answer = log.filter((c) => c.method === 'answerCallbackQuery').pop();
  await page.click('#side-tabs [data-tab=posts]');
  await page.waitForTimeout(500);
  const statsText = await page.locator('#posts li').first().textContent();
  check('Pin, first comment and callback answer work end to end',
    commentShown === 1 && !sentRich.body.pin && !sentRich.body.comment && !!pinned && answer && answer.body.text === 'Спасибо, учтено' && /нажатия: 👍 1/.test(statsText) && /ждёт пересылки/.test(statsText),
    JSON.stringify({ commentShown, pinned: !!pinned, answer: answer && answer.body, statsText }));
}
