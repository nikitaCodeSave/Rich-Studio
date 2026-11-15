// The draft's own notes the editor does not write (`_studio.source` and `_studio.warnings` of a forwarded post, any
// other key) survive opening, editing, «Применить JSON» and undo; the warnings show above the editor as text and
// fold away; none of it goes to Telegram. Undo in the JSON tab stays in the JSON tab. A post forwarded to the bot
// shows the warnings its import wrote.
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, until, push, BASE }) {
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const source = { forward_origin: { type: 'channel', chat: { id: -1001234, title: 'Чужой канал', type: 'channel' }, message_id: 42, date: 1 }, link: 'https://t.me/other_channel/42' };
  const warnings = ['кнопка «Голосовать» убрана: на неё отвечал бот исходного поста',
    'форматирование «sparkle» перенесено обычным текстом: <img src=x onerror="window.__notes=1">'];
  const notes = { source, warnings, template: { id: 'tpl', fields: { name: 'Аня' } } };
  const d = await api('POST', '/api/drafts', { title: 'Из пересылки', post: { rich_message: { blocks: [{ type: 'paragraph', text: 'Пересланный' }] }, _studio: notes } });
  const draft = () => api('GET', '/api/drafts/' + d.id);
  const kept = (s) => !!s && JSON.stringify([s.source, s.warnings, s.template]) === JSON.stringify([notes.source, notes.warnings, notes.template]);
  const box = page.locator('#draft-notes details');
  const quick = { timeout: 3000 };

  await page.goto(`${BASE}/?draft=${d.id}`);
  await page.waitForSelector('.block');
  await box.waitFor(quick).catch(() => {});
  const shownText = (await box.textContent(quick).catch(() => '')) || '';
  const open = await box.evaluate((el) => /** @type {HTMLDetailsElement} */ (el).open).catch(() => false);
  const asHtml = await page.evaluate(() => !!document.querySelector('#draft-notes').shadowRoot.querySelector('img') || '__notes' in window);
  // for the screenshots: the check pill comes with the first check of the opened draft
  await page.waitForFunction(() => !(/** @type {HTMLElement} */ (document.querySelector('#check-pill'))).hidden, null, quick).catch(() => {});
  await page.screenshot({ path: 'test-results/ui/draft-notes-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/ui/draft-notes-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  check('The draft\'s warnings show above the editor as text',
    open && warnings.every((w) => shownText.includes(w)) && /Замечания к черновику: 2/.test(shownText) && !asHtml,
    JSON.stringify({ open, shownText, asHtml }));

  // folding: a click on the line
  await page.locator('#draft-notes summary').click(quick).catch(() => {});
  const folded = await page.waitForFunction(() => !(/** @type {HTMLDetailsElement} */ (document.querySelector('#draft-notes').shadowRoot.querySelector('details'))).open, null, quick).then(() => true, () => false);
  const listGone = !(await page.locator('#draft-notes li').first().isVisible().catch(() => false));

  // an edit in the visual mode, then the autosave
  await page.locator('#pane-blocks .rte').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type(' мой');
  const typed = await until(async () => { const x = await draft(); return x.payload?.rich_message?.blocks?.[0]?.text === 'Пересланный мой' && x; });
  check('An edit and the autosave keep the draft\'s notes as they came; folding hides the warnings',
    kept(typed?.payload?._studio) && typed?.payload?._studio?.format === 'blocks' && folded && listGone,
    JSON.stringify({ studio: typed?.payload?._studio, folded, listGone }));

  // «Применить JSON» changes the post, not the draft's notes; undo there stays in the JSON tab
  await page.click('#mode-tabs [data-mode=json]');
  await page.fill('#src-json', JSON.stringify({ rich_message: { blocks: [{ type: 'paragraph', text: 'Из JSON' }] } }));
  await page.click('#json-apply');
  const applied = await until(async () => { const x = await draft(); return x.payload?.rich_message?.blocks?.[0]?.text === 'Из JSON' && x; });
  await page.waitForFunction(() => !document.querySelector('#btn-undo').disabled, null, quick).catch(() => {});
  await page.click('#btn-undo');
  const undone = await until(async () => { const x = await draft(); return x.payload?.rich_message?.blocks?.[0]?.text === 'Пересланный мой' && x; });
  const jsonTab = await page.evaluate(() => !document.querySelector('#pane-json').hidden && document.querySelector('#mode-tabs [data-mode=json]').classList.contains('on'));
  const preview = await page.locator('#preview .tg-preview').textContent();
  check('«Применить JSON» and undo keep the notes; undo in the JSON tab stays there',
    kept(applied?.payload?._studio) && kept(undone?.payload?._studio) && jsonTab && /Пересланный мой/.test(preview || ''),
    JSON.stringify({ applied: applied?.payload?._studio, undone: undone?.payload?._studio, jsonTab }));

  // the notes stay in the studio: what goes to Telegram is the post
  const before = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  await page.click('#btn-publish');
  const sends = await until(async () => { const s = (await mockLog()).filter((c) => c.method === 'sendRichMessage'); return s.length > before && s; });
  const sent = sends ? sends.at(-1).body : null;
  const after = await draft();
  check('The notes do not go to Telegram and stay with the draft after publishing',
    !!sent && sent.rich_message?.blocks?.[0]?.text === 'Пересланный мой' && !('_studio' in sent)
      && !JSON.stringify(sent).includes('Чужой канал') && !JSON.stringify(sent).includes('sparkle') && kept(after?.payload?._studio),
    JSON.stringify({ sent }));

  // a real import: the owner (TG_PREVIEW_CHAT) forwards a post with a callback button to the bot
  const owner = { id: 777, is_bot: false, first_name: 'Владелец' };
  await push({ message: { message_id: 9041, date: 1763200000, from: owner, chat: { ...owner, type: 'private' }, text: 'Пост с голосованием',
    forward_origin: { type: 'channel', date: 1763100000, message_id: 7, chat: { id: -1009, type: 'channel', title: 'Канал друга', username: 'friend' } },
    reply_markup: { inline_keyboard: [[{ text: 'Голосовать', callback_data: 'vote' }]] } } });
  const imported = await until(async () => {
    const one = (await api('GET', '/api/drafts')).find((x) => /Пост с голосованием/.test(x.title || ''));
    return one && api('GET', '/api/drafts/' + one.id);
  });
  const said = imported?.payload?._studio?.warnings || [];
  if (imported) await page.goto(`${BASE}/?draft=${imported.id}`);
  await page.waitForSelector('.block');
  const importShown = said.length ? ((await box.textContent(quick).catch(() => '')) || '') : '';
  check('A forwarded post\'s import warnings show in the editor',
    said.length > 0 && said.every((w) => importShown.includes(w)), JSON.stringify({ said, importShown }));
}
