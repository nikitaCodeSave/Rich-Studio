// A forum topic belongs to the chat it was chosen or loaded in: after an undo, a redo or «Применить» in the JSON tab,
// moving the post to another forum drops the topic, and an undo in another forum does not bring the old one's topic.
// A page of its own: the two forums are a faked GET /api/channels.

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ browser, BASE, check }) {
  const forum = (chat_id, title, topics) => ({ chat_id, title, type: 'supergroup', is_forum: true, topics,
    checked_at: '2026-11-16T00:00:00Z', is_default: false, can_post: true, rights: { status: 'administrator', can_post_messages: true } });
  const channels = [forum('@forum_a', 'Форум А', [{ id: 5, name: 'Новости А' }]), forum('@forum_b', 'Форум Б', [{ id: 8, name: 'Новости Б' }])];

  /** A fresh page on a draft in forum A: its open state is the bottom of the undo stack. */
  const open = async () => {
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
    const draft = await page.evaluate(() => fetch('/api/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Тема и канал', chat_ids: ['@forum_a'], post: { rich_message: { blocks: [{ type: 'paragraph', text: 'В тему' }] } } }) }).then((r) => r.json()));
    await page.goto(BASE + '/?draft=' + draft.id);
    await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.querySelector('#panel-opts')).open = true; });
    await topics(page, 'Новости А');
    return { context, page };
  };
  /** Wait for the topic list of the forum whose topic is named so. */
  const topics = (page, name) => page.waitForFunction((n) => [.../** @type {HTMLSelectElement} */ (document.querySelector('#opts #topic'))?.options || []]
    .some((o) => o.text === n), name, { timeout: 8000 }).catch(() => {});
  const moveTo = async (page, chat, name) => {
    await page.evaluate((c) => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = c; i.dispatchEvent(new Event('input')); }, chat);
    await topics(page, name);
  };
  // the choice is one undo step once editing pauses: the undo button comes on
  const pick = async (page, value) => {
    await page.selectOption('#opts #topic', value);
    await page.waitForFunction(() => !(/** @type {HTMLButtonElement} */ (document.querySelector('#btn-undo')).disabled), null, { timeout: 8000 }).catch(() => {});
  };
  /** What the post would send, and the topic list as shown. */
  const seen = (page) => page.evaluate(() => {
    /** @type {HTMLElement} */ (document.querySelector('#mode-tabs [data-mode=json]')).click();
    const sel = /** @type {HTMLSelectElement} */ (document.querySelector('#opts #topic'));
    return { thread: JSON.parse(/** @type {HTMLTextAreaElement} */ (document.querySelector('#src-json')).value).options?.message_thread_id ?? null,
      chosen: sel ? sel.selectedOptions[0]?.text : null };
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
}
