// The editor loses nothing of the post it opens (AGENTS.md rule 1, #196, #199): the answers (`callbacks`) of the buttons
// in the text (a <tg-button> in HTML, a «Кнопки» block) survive the JSON tab and a save; «Кнопки» at a published post
// replaces the answers of the keyboard and leaves those of its text; the fields of the first comment besides its HTML
// stay; a field the editor does not read (a new one of models.Post among them) goes back as it came; JSON that is not
// an object is not applied.
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, push, until, BASE }) {
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const wait = { timeout: 10000 };
  // the same JSON whatever the order of its keys
  const canon = (v) => JSON.stringify(v, (_, x) => (x && typeof x === 'object' && !Array.isArray(x) ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : 1))) : x));
  const same = (a, b) => canon(a) === canon(b);
  const lastDraft = () => page.evaluate(() => JSON.parse(localStorage.getItem('rs.lastDraft') || 'null'));
  /** «Сохранить», then the draft as the server has it once `ok` holds for its payload */
  const save = async (ok, id) => {
    await page.click('#btn-save');
    return until(async () => {
      const d = await api('GET', '/api/drafts/' + (id || (await lastDraft())));
      return d && d.payload && ok(d.payload) ? d : null;
    }, 12000);
  };
  const jsonTab = async () => JSON.parse(await page.inputValue('#src-json'));
  const apply = async (post) => {
    await page.click('#mode-tabs [data-mode=json]');
    await page.fill('#src-json', typeof post === 'string' ? post : JSON.stringify(post));
    await page.click('#json-apply');
  };
  // the check under the editor says it (or no longer does)
  const says = (text, on = true) => page.waitForFunction(([w, want]) => (document.querySelector('#validation')?.textContent || '').includes(w) === want, [text, on], wait).then(() => true, () => false);
  const typeIn = async (selector, text) => { await page.locator(selector).first().click(); await page.keyboard.press('End'); await page.keyboard.type(text); };

  // 1. a <tg-button> with the action callback_data in HTML and its answer
  const html = '<p>Голосуем</p><tg-button-row><tg-button type="callback_data" data="vote">Да</tg-button></tg-button-row>';
  await page.click('#btn-new');
  await apply({ rich_message: { html }, callbacks: { vote: 'Спасибо' } });
  const htmlApplied = await jsonTab();
  await page.click('#mode-tabs [data-mode=html]');
  await page.fill('#src-html', html.replace('Голосуем', 'Голосуем!'));
  const htmlSaved = await save((p) => p.rich_message?.html?.includes('Голосуем!'));
  check('An answer of a <tg-button> in HTML survives «Применить» in the JSON tab and a save',
    htmlApplied.callbacks?.vote === 'Спасибо' && htmlSaved?.payload?.callbacks?.vote === 'Спасибо',
    JSON.stringify({ applied: htmlApplied.callbacks, saved: htmlSaved?.payload }));

  // a button of the keyboard removed in the form takes its answer along, unless the <tg-button> has the same key
  const removeKbButton = async (post) => {
    await apply(post);
    await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-kb')).open = true; });
    await page.locator('#kb .kb-btn button[title="Убрать кнопку"]').first().click();
    return jsonTab();
  };
  const shared = await removeKbButton({ rich_message: { html }, reply_markup: { inline_keyboard: [[{ text: 'За', callback_data: 'vote' }]] }, callbacks: { vote: 'Спасибо' } });
  const alone = await removeKbButton({ rich_message: { html }, reply_markup: { inline_keyboard: [[{ text: '👍', callback_data: 'like' }]] }, callbacks: { vote: 'Спасибо', like: 'Нравится' } });
  check('A button of the keyboard removed in the form takes its answer, but not the answer its key has in the text',
    !shared.reply_markup && same(shared.callbacks, { vote: 'Спасибо' }) && !alone.reply_markup && same(alone.callbacks, { vote: 'Спасибо' }),
    JSON.stringify({ shared, alone }));

  // 2. a «Кнопки» block and its answer, edited in the visual mode
  const blocks = [{ type: 'paragraph', text: 'Голосуем' }, { type: 'buttons', buttons: [{ text: 'Да', callback_data: 'vote' }] }];
  await page.click('#btn-new');
  await apply({ rich_message: { blocks }, callbacks: { vote: 'Спасибо' } });
  const blocksApplied = await jsonTab();
  await page.click('#mode-tabs [data-mode=blocks]');
  await typeIn('#pane-blocks .rte', ' сейчас');
  const blocksSaved = await save((p) => p.rich_message?.blocks?.[0]?.text === 'Голосуем сейчас');
  check('An answer of a button in a «Кнопки» block survives «Применить» and an edit in the visual mode',
    blocksApplied.callbacks?.vote === 'Спасибо' && blocksSaved?.payload?.callbacks?.vote === 'Спасибо',
    JSON.stringify({ applied: blocksApplied.callbacks, saved: blocksSaved?.payload }));

  // an answer no button has stays where it is, and the check says so
  await apply({ rich_message: { blocks }, callbacks: { vote: 'Спасибо', ghost: 'Никто не нажмёт' } });
  const orphanShown = await says('ответ на кнопку «ghost»: ответ без кнопки');
  const orphanSaved = await save((p) => p.callbacks?.ghost === 'Никто не нажмёт');
  check('An answer without a button stays in the post, and the check warns about it',
    orphanShown && !!orphanSaved && orphanSaved.payload.callbacks.vote === 'Спасибо',
    JSON.stringify({ orphanShown, saved: orphanSaved?.payload, validation: await page.locator('#validation').textContent() }));

  // 3. «Кнопки» at a published post: the keyboard's answers are replaced, the <tg-button>'s stay
  const sent = await api('POST', '/api/send', { post: { rich_message: { html }, reply_markup: { inline_keyboard: [[{ text: '👍', callback_data: 'like' }]] }, callbacks: { vote: 'Спасибо', like: 'Нравится' } } });
  const pid = sent.post?.id;
  const mid = sent.message?.message_id;
  await page.goto(`${BASE}/?post=${pid}`);
  await page.waitForSelector('#panel-kb');
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-kb')).open = true; });
  const kbAnswer = page.locator('#kb .kb-btn .cb-answer input[type=text]').first();
  await page.waitForFunction(() => /** @type {HTMLInputElement | null} */ (document.querySelector('#kb .kb-btn .cb-answer input[type=text]'))?.value === 'Нравится', null, wait).catch(() => {});
  await kbAnswer.fill('Очень нравится');
  await page.click('#side-tabs [data-tab=posts]');
  const row = page.locator('#posts li', { hasText: '#' + mid });
  const buttons = row.getByRole('button', { name: 'Кнопки' });
  await buttons.waitFor(wait).catch(() => {});
  const edits = (await mockLog()).filter((c) => c.method === 'editMessageReplyMarkup').length;
  const answer = page.waitForResponse((r) => r.request().method() === 'POST' && new URL(r.url()).pathname === `/api/posts/${pid}/buttons`, wait).catch(() => null);
  await buttons.click().catch(() => {});
  await answer;
  const replaced = await until(async () => {
    const p = await api('GET', '/api/posts/' + pid);
    return p.payload?.callbacks?.like === 'Очень нравится' ? p : null;
  });
  const editedInTelegram = (await mockLog()).filter((c) => c.method === 'editMessageReplyMarkup').length > edits;
  const answers = (await mockLog()).filter((c) => c.method === 'answerCallbackQuery').length;
  await push({ callback_query: { id: 'cq-54-vote', data: 'vote', from: { id: 5 }, message: { message_id: mid, chat: { id: -1001 } } } });
  const pressed = await until(async () => (await mockLog()).filter((c) => c.method === 'answerCallbackQuery').slice(answers).find((c) => c.body.callback_query_id === 'cq-54-vote'), 15000);
  check('«Кнопки» at a published post replaces the keyboard\'s answers and keeps the answer of its <tg-button>',
    editedInTelegram && !!replaced && same(replaced.payload.callbacks, { vote: 'Спасибо', like: 'Очень нравится' }) && pressed?.body?.text === 'Спасибо',
    JSON.stringify({ editedInTelegram, callbacks: replaced?.payload?.callbacks, pressed: pressed?.body }));
  await page.click('#btn-new'); // leave the editing of that post

  // 4. the first comment: the fields besides its HTML stay through an edit of the post and of the comment
  const media = [{ id: 'c1', media: { type: 'photo', media: 'https://example.com/c1.jpg' } }];
  const comment = { html: '<p>Комментарий <img src="tg://photo?id=c1"></p>', skip_entity_detection: true, is_rtl: true, media };
  const d = await api('POST', '/api/drafts', { title: 'Комментарий с полями', post: { rich_message: { blocks: [{ type: 'paragraph', text: 'Пост' }] }, comment } });
  await page.goto(`${BASE}/?draft=${d.id}`);
  await page.waitForSelector('.block');
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-after')).open = true; });
  await typeIn('#pane-blocks .rte', ' с комментарием');
  await page.locator('#comment-src').fill('<p>Комментарий ещё <img src="tg://photo?id=c1"></p>');
  const commentSaved = await save((p) => p.rich_message?.blocks?.[0]?.text === 'Пост с комментарием' && /ещё/.test(p.comment?.html || ''), d.id);
  await page.locator('#after').screenshot({ path: 'test-results/ui/comment-fields-1400.png' }).catch(() => {});
  check('The first comment keeps skip_entity_detection, is_rtl and media through an edit of the post and the comment',
    !!commentSaved && same(commentSaved.payload.comment, { ...comment, html: '<p>Комментарий ещё <img src="tg://photo?id=c1"></p>' }),
    JSON.stringify(commentSaved?.payload));
  // its HTML emptied: the comment does not vanish with its other fields, the check says what is wrong
  await page.locator('#comment-src').fill('');
  const emptied = await save((p) => p.comment?.html === '', d.id);
  const emptyShown = await says('комментарий: нужно ровно одно из html / markdown / blocks');
  check('A comment emptied in the form keeps its other fields, and the check shows it',
    !!emptied && same(emptied.payload.comment, { ...comment, html: '' }) && emptyShown,
    JSON.stringify({ comment: emptied?.payload?.comment, emptyShown }));

  // 5. every field of the post at a value other than its default: opened, edited and saved, each stays as it came. A
  // field of models.Post this list does not know (a new one) gets an object of its own and has to stay too
  const spec = await api('GET', '/api/spec');
  const values = {
    rich_message: { blocks, is_rtl: false, skip_entity_detection: true },
    reply_markup: { inline_keyboard: [[{ text: '👍', callback_data: 'like' }]] },
    options: { disable_notification: true, protect_content: false, message_effect_id: '' },
    pin: true,
    comment,
    callbacks: { like: 'Нравится', vote: 'Спасибо' },
    poll_after: { question: 'Как вам?', options: ['Хорошо', { text: 'Плохо' }], is_anonymous: false },
  };
  const names = spec.post.types[spec.post.root].fields.map((f) => f.name);
  const every = Object.fromEntries(names.map((n) => [n, n in values ? values[n] : { e2e: n }]));
  const all = await api('POST', '/api/drafts', { title: 'Все поля поста', post: { ...every, foo_bar: { from: 'агент' } } });
  await page.goto(`${BASE}/?draft=${all.id}`);
  await page.waitForSelector('.block');
  await typeIn('#pane-blocks .rte', ' сейчас');
  const allSaved = await save((p) => p.rich_message?.blocks?.[0]?.text === 'Голосуем сейчас', all.id);
  const want = { ...every, foo_bar: { from: 'агент' }, rich_message: { ...values.rich_message, blocks: [{ ...blocks[0], text: 'Голосуем сейчас' }, blocks[1]] } };
  const lost = allSaved ? Object.keys(want).filter((k) => !same(allSaved.payload[k], want[k])) : ['(не сохранён)'];
  check('Every field of the post at a value other than its default stays through an edit and a save', !lost.length,
    JSON.stringify({ lost, fields: names, saved: allSaved?.payload }));

  // 6. JSON that is not an object is not applied
  await apply('[1, 2]');
  const refused = await page.waitForFunction(() => /пост — объект JSON/.test(document.querySelector('#json-err')?.textContent || ''), null, wait).then(() => true, () => false);
  const kept = await save((p) => p.rich_message?.blocks?.[0]?.text === 'Голосуем сейчас', all.id);
  check('JSON that is not an object is refused in the JSON tab, the post stays as it was',
    refused && !!kept && !('0' in kept.payload) && !!(await page.locator('#mode-tabs [data-mode=json].pending').count()),
    JSON.stringify({ err: await page.locator('#json-err').textContent(), saved: kept?.payload }));
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.click('#btn-new'); // the shared page leaves that draft, its unapplied JSON stays with it
}
