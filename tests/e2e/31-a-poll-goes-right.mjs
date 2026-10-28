// a poll goes right after the post from the draft; history shows what changed; quick times for scheduling
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, setChat, applyJson }) {
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await page.fill('#title', 'Анонс с опросом');
  await setChat('@poll_after');
  await applyJson({ rich_message: { markdown: 'Эфир в четверг\n\nТемы: Bot API' } });
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-after')).open = true; });
  await page.check('#poll-after-on');
  await page.fill('#after input[aria-label="Вопрос опроса"]', 'Придёте?');
  await page.fill('#after input[aria-label="Вариант 1"]', 'Да');
  await page.fill('#after input[aria-label="Вариант 2"]', 'Нет');
  await page.waitForTimeout(400);
  const pollPreview = await page.evaluate(() => document.querySelector('#preview').shadowRoot.querySelector('.tg-poll')?.textContent || '');
  const before = (await mockLog()).length;
  await page.click('#btn-publish');
  await page.waitForTimeout(900);
  const sends = (await mockLog()).slice(before).map((c) => c.method).filter((m) => /^send/.test(m));
  const poll = (await mockLog()).filter((c) => c.method === 'sendPoll').pop()?.body;
  // «Править» keeps the draft's name; the update shows up in the history as lines removed and added
  await page.click('#side-tabs [data-tab=posts]');
  await page.locator('#posts li', { hasText: '@poll_after' }).filter({ hasText: 'Эфир в четверг' }).getByRole('button', { name: 'Править' }).click();
  await page.waitForTimeout(500);
  const title = await page.inputValue('#title');
  await applyJson({ rich_message: { markdown: 'Эфир в пятницу\n\nТемы: Bot API' } });
  await page.click('#edit-bar >> text=Обновить пост');
  await page.waitForTimeout(600);
  await page.click('#edit-bar >> text=История');
  await page.waitForSelector('dialog[open] >> text=История поста');
  const added = await page.locator('dialog[open] .diff .add').allTextContents();
  const removed = await page.locator('dialog[open] .diff .del').allTextContents();
  await page.locator('dialog[open] li').first().getByRole('button', { name: 'Показать' }).click();
  const shown = await page.evaluate(() => document.querySelector('dialog[open] rs-preview')?.shadowRoot?.textContent || '');
  await page.screenshot({ path: 'test-results/ui/history-1400.png' }).catch(() => {});
  await page.keyboard.press('Escape');
  await page.click('#side-tabs [data-tab=drafts]');
  const badge = await page.locator('#drafts li', { hasText: /^Анонс с опросом/ }).first().locator('.badge').textContent().catch(() => '');
  // the schedule dialog: quick choices fill the time, the time zone and the channel are named
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await setChat('@later');
  await page.click('#btn-more');
  await page.click('#btn-schedule');
  const dlg = page.locator('dialog[open]');
  const presetsShown = await dlg.locator('.presets button').allTextContents();
  await dlg.locator('.presets button', { hasText: 'Завтра в 9:00' }).click();
  const picked = await dlg.locator('input[type=datetime-local]').inputValue();
  const dlgText = await dlg.textContent();
  await page.screenshot({ path: 'test-results/ui/schedule-1400.png' }).catch(() => {});
  await page.keyboard.press('Escape');
  check('Poll after the post, history with changes, title kept, schedule presets',
    /Придёте\?/.test(pollPreview) && sends.join() === 'sendRichMessage,sendPoll' && poll?.question === 'Придёте?'
      && title === 'Правка: Анонс с опросом' && added.some((x) => /пятницу/.test(x)) && removed.some((x) => /четверг/.test(x)) && /пятницу/.test(shown)
      && badge === 'опубликован'
      && presetsShown.includes('Завтра в 9:00') && /T09:00$/.test(picked) && /UTC[+−]\d/.test(dlgText || '') && /@later/.test(dlgText || ''),
    JSON.stringify({ pollPreview, sends, poll, title, added, removed, shown: shown.slice(0, 80), badge, presetsShown, picked, dlg: (dlgText || '').slice(0, 200) }));
}
