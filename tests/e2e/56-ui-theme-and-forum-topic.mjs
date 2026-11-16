// The studio's own light or dark theme from the header (#ui-theme): the system's until chosen, kept over a reload,
// apart from the Telegram preview's ☾. The forum topic list (#topic) says «Тема форума» and opens no dialog.
// Pages of their own: the system theme is the context's, the forum channel is a faked GET /api/channels.

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ browser, BASE, check }) {
  /** @param {'light' | 'dark'} colorScheme */
  const open = async (colorScheme) => {
    const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme });
    const page = await context.newPage();
    // E2E_SLOW slows these pages too, as harness.mjs does the shared one: the check waits for events, not pauses
    if (process.env.E2E_SLOW) {
      const cdp = await context.newCDPSession(page);
      await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: Number(process.env.E2E_SLOW), downloadThroughput: -1, uploadThroughput: -1 });
    }
    page.on('dialog', (d) => d.accept());
    await page.addInitScript(() => { localStorage.setItem('rs.plainSource', 'true'); });
    return { context, page };
  };
  const look = (page) => page.evaluate(() => ({
    ui: /** @type {HTMLSelectElement} */ (document.querySelector('#ui-theme'))?.value,
    options: [.../** @type {HTMLSelectElement} */ (document.querySelector('#ui-theme'))?.options || []].map((o) => `${o.value}:${o.text}`),
    attr: document.documentElement.dataset.theme ?? null,
    bg: getComputedStyle(document.body).backgroundColor,
    preview: /** @type {any} */ (document.querySelector('#preview')).dark,
  }));
  const LIGHT = 'rgb(244, 246, 248)';
  const DARK = 'rgb(14, 22, 33)';

  // a dark system: the list shows «Тёмная» until something is chosen; «Светлая» wins over the system and a reload
  {
    const { context, page } = await open('dark');
    await page.goto(BASE);
    await page.waitForSelector('.block');
    const first = await look(page);
    await page.selectOption('#ui-theme', 'light');
    const chosen = await look(page);
    await page.reload();
    await page.waitForSelector('.block');
    const reloaded = await look(page);
    await page.click('#theme-toggle');
    const toggled = await look(page);
    check('The studio theme: two choices, the system one until chosen, kept over a reload, apart from ☾',
      JSON.stringify(first.options) === '["light:Светлая","dark:Тёмная"]' && first.ui === 'dark' && first.attr === null && first.bg === DARK
      && chosen.ui === 'light' && chosen.attr === 'light' && chosen.bg === LIGHT && chosen.preview === first.preview
      && reloaded.ui === 'light' && reloaded.attr === 'light' && reloaded.bg === LIGHT
      && toggled.preview === !reloaded.preview && toggled.ui === 'light' && toggled.attr === 'light',
      JSON.stringify({ first, chosen, reloaded, toggled }));
    await context.close();
  }

  // a light system: «Тёмная» makes the studio dark
  {
    const { context, page } = await open('light');
    await page.goto(BASE);
    await page.waitForSelector('.block');
    const first = await look(page);
    await page.selectOption('#ui-theme', 'dark');
    const chosen = await look(page);
    check('The studio theme «Тёмная» over a light system',
      first.ui === 'light' && first.bg === LIGHT && chosen.ui === 'dark' && chosen.attr === 'dark' && chosen.bg === DARK,
      JSON.stringify({ first, chosen }));
    await context.close();
  }

  // a forum: «Тема форума: …» items, none of them opens a dialog, a chosen one is the post's message_thread_id
  {
    const { context, page } = await open('light');
    const forum = { chat_id: '@forum_ui', title: 'Форум', type: 'supergroup', is_forum: true, topics: [{ id: 5, name: 'Новости' }],
      checked_at: '2026-11-16T00:00:00Z', is_default: false, can_post: true, rights: { status: 'administrator', can_post_messages: true } };
    await page.route('**/api/channels', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: [forum] }) : route.continue()));
    await page.goto(BASE);
    await page.waitForSelector('.block');
    await page.evaluate(() => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = '@forum_ui'; i.dispatchEvent(new Event('input')); });
    await page.waitForFunction(() => !(/** @type {HTMLSelectElement} */ (document.querySelector('#topic')).hidden), null, { timeout: 8000 }).catch(() => {});
    const items = () => page.evaluate(() => [.../** @type {HTMLSelectElement} */ (document.querySelector('#topic')).options].map((o) => o.text));
    const listed = await items();
    const dialogs = [];
    for (const value of await page.evaluate(() => [.../** @type {HTMLSelectElement} */ (document.querySelector('#topic')).options].map((o) => o.value))) {
      await page.selectOption('#topic', value);
      dialogs.push(await page.evaluate(() => !!document.querySelector('dialog[open]')));
    }
    await page.selectOption('#topic', '5');
    await page.click('#mode-tabs [data-mode=json]');
    const thread = await page.evaluate(() => JSON.parse(/** @type {HTMLTextAreaElement} */ (document.querySelector('#src-json')).value).options?.message_thread_id);
    // a topic the studio does not know is shown by its number
    const draft = await page.evaluate(() => fetch('/api/drafts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Тема №9', chat_ids: ['@forum_ui'], post: { rich_message: { blocks: [{ type: 'paragraph', text: 'В тему' }] }, options: { message_thread_id: 9 } } }) }).then((r) => r.json()));
    await page.goto(BASE + '/?draft=' + draft.id);
    await page.waitForFunction(() => [.../** @type {HTMLSelectElement} */ (document.querySelector('#topic')).options].some((o) => o.value === '9'), null, { timeout: 8000 }).catch(() => {});
    const unknown = await items();
    check('The forum topic list says «Тема форума», opens no dialog and sets message_thread_id',
      JSON.stringify(listed) === '["Тема форума: General","Тема форума: Новости"]' && dialogs.length === 2 && dialogs.every((d) => !d)
      && thread === 5 && JSON.stringify(unknown) === '["Тема форума: General","Тема форума: Новости","Тема форума №9"]',
      JSON.stringify({ listed, dialogs, thread, unknown }));
    await context.close();
  }

  // a forum with a real-looking title, a -100… id and a long topic: the header has #ui-theme, the channel button and
  // the topic list all at once, and from 1280 px it is one row, so they shrink instead of pushing «▾» off the screen
  for (const theme of /** @type {const} */ (['light', 'dark'])) {
    const { context, page } = await open(theme);
    const forum = { chat_id: '-1001234567890', title: 'Нейросети и разработка', type: 'supergroup', is_forum: true,
      topics: [{ id: 7, name: 'Анонсы и релизы' }], checked_at: '2026-11-16T00:00:00Z', is_default: false, can_post: true,
      rights: { status: 'administrator', can_post_messages: true } };
    await page.route('**/api/channels', (route) => (route.request().method() === 'GET' ? route.fulfill({ json: [forum] }) : route.continue()));
    await page.goto(BASE);
    await page.waitForSelector('.block');
    await page.selectOption('#ui-theme', theme);
    await page.fill('#title', 'Довольно длинное название черновика');
    await page.evaluate(() => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = '-1001234567890'; i.dispatchEvent(new Event('input')); });
    await page.waitForFunction(() => !(/** @type {HTMLSelectElement} */ (document.querySelector('#topic')).hidden), null, { timeout: 8000 }).catch(() => {});
    await page.selectOption('#topic', '7').catch(() => {});
    const fits = [];
    for (const width of [1280, 1366, 1400, 1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      fits.push(await page.evaluate((w) => {
        const at = (sel) => document.querySelector(sel).getBoundingClientRect();
        const el = document.documentElement;
        const topic = /** @type {HTMLSelectElement} */ (document.querySelector('#topic'));
        return { width: w, scroll: el.scrollWidth, client: el.clientWidth, more: Math.round(at('#btn-more').right), inner: innerWidth,
          oneRow: Math.abs(at('#btn-publish').top - at('.brand').top) < 12, topic: topic.hidden ? null : topic.selectedOptions[0]?.text,
          chat: Math.round(at('#btn-check').width), topicWidth: Math.round(at('#topic').width), attr: el.dataset.theme };
      }, width));
      if (width === 1280 || width === 1400 || width === 390) {
        await page.locator('header.top').screenshot({ path: `test-results/ui/header-forum-${width}-${theme}.png` }).catch(() => {});
      }
    }
    check(`The header with a forum and its topic stays on the screen at 1280–1440 and 390 px («${theme === 'dark' ? 'Тёмная' : 'Светлая'}»)`,
      fits.every((f) => f.scroll <= f.client && f.more <= f.inner && f.topic === 'Тема форума: Анонсы и релизы' && f.attr === theme)
      && fits.filter((f) => f.width >= 1280).every((f) => f.oneRow),
      JSON.stringify(fits));
    await context.close();
  }
}
