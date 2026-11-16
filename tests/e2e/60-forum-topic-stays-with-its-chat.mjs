// A forum topic belongs to the chat it leads to: chosen, typed or loaded there. After an undo, a redo, «Применить» in the
// JSON tab or a topic typed in the field of other parameters, moving the post to another forum drops the topic, and an
// undo in another forum does not bring the old one's topic. A published post's topic belongs to the post's own chat
// whatever the header says. A page of its own: the forums are a faked GET /api/channels.

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ browser, BASE, check }) {
  const forum = (chat_id, title, topics) => ({ chat_id, title, type: 'supergroup', is_forum: true, topics,
    checked_at: '2026-11-16T00:00:00Z', is_default: false, can_post: true, rights: { status: 'administrator', can_post_messages: true } });
  const channels = [forum('@forum_a', 'Форум А', [{ id: 5, name: 'Новости А' }]), forum('@forum_b', 'Форум Б', [{ id: 8, name: 'Новости Б' }]),
    forum('@forum_c', 'Форум без тем', [])];

  /** A fresh page on a draft in a forum (A by default): its open state is the bottom of the undo stack. */
  const open = async (chat = '@forum_a') => {
    const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    const page = await context.newPage();
    // E2E_SLOW slows this page too, as harness.mjs does the shared one: the check waits for events, not pauses
    if (process.env.E2E_SLOW) {
      const cdp = await context.newCDPSession(page);
      await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: Number(process.env.E2E_SLOW), downloadThroughput: -1, uploadThroughput: -1 });
    }
    page.on('dialog', (d) => d.accept());
    await page.addInitScript(() => { localStorage.setItem('rs.plainSource', 'true'); });
    await page.route('**/api/channels', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: channels }) : route.continue()));
    await page.goto(BASE);
    await page.waitForSelector('.block');
    const draft = await page.evaluate((c) => fetch('/api/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Тема и канал', chat_ids: [c], post: { rich_message: { blocks: [{ type: 'paragraph', text: 'В тему' }] } } }) }).then((r) => r.json()), chat);
    await page.goto(BASE + '/?draft=' + draft.id);
    await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.querySelector('#panel-opts')).open = true; });
    if (chat === '@forum_a') await topics(page, 'Новости А');
    else await page.waitForFunction((c) => /** @type {HTMLInputElement} */ (document.querySelector('#chat')).value === c, chat, { timeout: 8000 }).catch(() => {});
    return { context, page };
  };
  /** Wait for the topic list of the forum whose topic is named so. */
  const topics = (page, name) => page.waitForFunction((n) => [.../** @type {HTMLSelectElement} */ (document.querySelector('#opts #topic'))?.options || []]
    .some((o) => o.text === n), name, { timeout: 8000 }).catch(() => {});
  const moveTo = async (page, chat, name) => {
    await page.evaluate((c) => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = c; i.dispatchEvent(new Event('input')); }, chat);
    await topics(page, name);
  };
  /** The header names the chat: its redraw (and the topic check in it) ran. */
  const header = (page, title) => page.waitForFunction((t) => document.querySelector('#btn-check').textContent.includes(t), title, { timeout: 8000 }).catch(() => {});
  // the choice is one undo step once editing pauses: the undo button comes on
  const pick = async (page, value) => {
    await page.selectOption('#opts #topic', value);
    await page.waitForFunction(() => !(/** @type {HTMLButtonElement} */ (document.querySelector('#btn-undo')).disabled), null, { timeout: 8000 }).catch(() => {});
  };
  /** What the post would send, and the topic list as shown. */
  const seen = (page) => page.evaluate(() => {
    /** @type {HTMLElement} */ (document.querySelector('#mode-tabs [data-mode=json]')).click();
    const sel = /** @type {HTMLSelectElement} */ (document.querySelector('#opts #topic'));
    const options = JSON.parse(/** @type {HTMLTextAreaElement} */ (document.querySelector('#src-json')).value).options || {};
    return { thread: options.message_thread_id ?? null, silent: !!options.disable_notification, chosen: sel ? sel.selectedOptions[0]?.text : null };
  });

  // undo and redo in forum A keep its topic there; moved to forum B, the post loses it
  {
    const { context, page } = await open();
    await pick(page, '5');
    await page.click('#btn-undo');
    const undone = await seen(page);
    await page.click('#btn-redo');
    const redone = await seen(page);
    await moveTo(page, '@forum_b', 'Новости Б');
    const moved = await seen(page);
    check('A topic brought back by redo stays in its forum and is dropped in another one',
      undone.thread === null && redone.thread === 5 && redone.chosen === 'Новости А' && moved.thread === null && moved.chosen === 'General',
      JSON.stringify({ undone, redone, moved }));
    await context.close();
  }

  // the topic of forum A was dropped on the way to forum B: undoing that drop in forum B does not bring «№5» there
  {
    const { context, page } = await open();
    await pick(page, '5');
    await moveTo(page, '@forum_b', 'Новости Б');
    const moved = await seen(page);
    await page.click('#btn-undo');
    const undone = await seen(page);
    check('An undo in another forum does not bring the old forum\'s topic',
      moved.thread === null && undone.thread === null && undone.chosen === 'General',
      JSON.stringify({ moved, undone }));
    await context.close();
  }

  // a topic typed in the JSON tab belongs to the forum it is applied in
  {
    const { context, page } = await open();
    await page.click('#mode-tabs [data-mode=json]');
    const post = JSON.parse(await page.inputValue('#src-json'));
    await page.fill('#src-json', JSON.stringify({ ...post, options: { ...post.options, message_thread_id: 5 } }, null, 2));
    await page.click('#json-apply');
    const applied = await seen(page);
    await moveTo(page, '@forum_b', 'Новости Б');
    const moved = await seen(page);
    check('A topic applied from the JSON tab stays in its forum and is dropped in another one',
      applied.thread === 5 && applied.chosen === 'Новости А' && moved.thread === null && moved.chosen === 'General',
      JSON.stringify({ applied, moved }));
    await context.close();
  }

  // a topic typed in the field of other parameters (a forum with no known topics) belongs to that forum
  {
    const { context, page } = await open('@forum_c');
    await page.fill('#opts textarea', '{"message_thread_id": 5}');
    const typed = await seen(page);
    await moveTo(page, '@forum_b', 'Новости Б');
    const moved = await seen(page);
    check('A topic typed in the field of other parameters is dropped in another forum',
      typed.thread === 5 && moved.thread === null && moved.chosen === 'General',
      JSON.stringify({ typed, moved }));
    await context.close();
  }

  // one Ctrl+Z undoes a change: a step that differed only by the topic left behind in forum A is passed over
  {
    const { context, page } = await open();
    await pick(page, '5');
    await page.getByLabel('Без звука').check();
    // undo and redo close the step of «Без звука», so the move to forum B is a step of its own
    await page.click('#btn-undo');
    await page.click('#btn-redo');
    const before = await seen(page);
    await moveTo(page, '@forum_b', 'Новости Б');
    await page.click('#btn-undo');
    const undone = await seen(page);
    check('One undo after the topic was left behind undoes the change before it',
      before.silent && before.thread === 5 && !undone.silent && undone.thread === null,
      JSON.stringify({ before, undone }));
    await context.close();
  }

  // a scheduled post in forum A opened for editing (/?post=) with another chat in the header: the topic list is forum
  // A's, and choosing forum A or B in the header keeps the topic, since «Обновить пост» sends the post to forum A
  {
    const { context, page } = await open('@forum_c');
    const post = await page.evaluate(() => fetch('/api/schedule', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: '@forum_a', at: Math.floor(Date.now() / 1000) + 86400,
        post: { rich_message: { blocks: [{ type: 'paragraph', text: 'Отложенный в тему' }] }, options: { message_thread_id: 5 } } }) }).then((r) => r.json()));
    await page.goto(BASE + '/?post=' + post.id);
    await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.querySelector('#panel-opts')).open = true; });
    await topics(page, 'Новости А');
    const opened = await seen(page);
    await moveTo(page, '@forum_a', 'Новости А');
    await header(page, 'Форум А');
    const own = await seen(page);
    await page.evaluate(() => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = '@forum_b'; i.dispatchEvent(new Event('input')); });
    await header(page, 'Форум Б');
    const other = await seen(page);
    check('A post opened for editing keeps its topic whatever chat the header shows',
      post.id && [opened, own, other].every((s) => s.thread === 5 && s.chosen === 'Новости А'),
      JSON.stringify({ id: post.id, opened, own, other }));
    await context.close();
  }
}
