// What Telegram refuses in the chat the post goes to (#166): a web_app button in a checked channel gets a mark on its
// card with the direct link to use instead, and the check below the editor says it too; a chat nobody checked has no
// type and no mark. ephemeral_message_parameters in options is refused: nothing goes to Telegram
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson, setChat, mockLog }) {
  const quick = { timeout: 8000 };
  const before = await page.inputValue('#chat');
  await page.click('#btn-new');
  await page.fill('#title', 'Кнопка Mini App');
  // the studio knows a chat's type once it checked it (getChat): the mock answers «channel»
  await page.click('#btn-check');
  await page.locator('dialog[open] .row input[placeholder="@канал или -100…"]').fill('@app_news');
  await page.locator('dialog[open] button', { hasText: 'Добавить и проверить' }).click();
  await page.waitForFunction(() => /@app_news/.test(document.querySelector('dialog[open] .channels')?.textContent || ''), null, quick).catch(() => {});
  await page.keyboard.press('Escape');
  await setChat('@app_news');
  await applyJson({
    rich_message: { html: '<p>Откройте приложение</p>' },
    reply_markup: { inline_keyboard: [[{ text: 'Приложение', web_app: { url: 'https://example.com/app' } }, { text: 'Сайт', url: 'https://example.com' }]] },
  });
  await page.click('#mode-tabs [data-mode=html]');
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-kb')).open = true; });
  const marked = () => page.locator('#kb .kb-btn').nth(0).locator('.kb-refused p[data-field=web_app]');
  await marked().waitFor(quick).catch(() => {});
  const mark = (await marked().count()) ? await marked().textContent() : '';
  const plain = await page.locator('#kb .kb-btn').nth(1).locator('.kb-refused p').count();
  await page.waitForFunction(() => /web_app: не работает в канале/.test(document.querySelector('#validation')?.textContent || ''), null, quick).catch(() => {});
  const listed = await page.locator('#validation li.w', { hasText: 'web_app' }).allTextContents();
  // the screenshots show the card, not the toasts of the steps above: they go by themselves
  const noToasts = () => page.waitForFunction(() => !document.querySelector('#toasts > *'), null, { timeout: 12000 }).catch(() => {});
  await noToasts();
  await page.locator('#kb .kb-btn').nth(0).scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'test-results/ui/chat-rules-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => innerWidth === 390, null, quick).catch(() => {});
  await page.locator('#kb .kb-btn').nth(0).scrollIntoViewIfNeeded();
  await noToasts();
  await page.screenshot({ path: 'test-results/ui/chat-rules-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  // another chat, never checked: no type, so nothing is marked; back to the channel, the mark is back
  await setChat('@never_checked');
  await page.waitForFunction(() => !document.querySelector('#kb .kb-refused p'), null, quick).catch(() => {});
  const unknown = await page.locator('#kb .kb-refused p').count();
  await setChat('@app_news');
  await marked().waitFor(quick).catch(() => {});
  const back = await marked().count();
  check('A web_app button in a checked channel is marked, with the direct link instead; an unchecked chat is not',
    /web_app не работает в канале «Тест»/.test(mark) && /Available only in private chats/.test(mark) && /BUTTON_TYPE_INVALID/.test(mark)
      && mark.includes('https://t.me/<бот>/<приложение>?startapp=')
      && plain === 0 && listed.length === 1 && /Приложение/.test(listed[0]) && unknown === 0 && back === 1,
    JSON.stringify({ mark, plain, listed, unknown, back }));

  const sends = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  const refused = await page.evaluate(async () => {
    const post = { rich_message: { html: '<p>x</p>' }, options: { ephemeral_message_parameters: { callback_query_id: '1' } } };
    const r = await fetch('/api/send', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ post, chat_id: '@app_news' }) });
    return { status: r.status, text: await r.text() };
  });
  const after = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  check('ephemeral_message_parameters in options is refused, nothing goes to Telegram',
    refused.status === 400 && /ephemeral_message_parameters/.test(refused.text) && after === sends, JSON.stringify({ refused, sends, after }));
  await setChat(before);
}
