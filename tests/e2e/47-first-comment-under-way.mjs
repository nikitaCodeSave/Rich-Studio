// the first comment between its forward and Telegram's answer: «отправляется» while the request is under way (the
// mock holds the answer back), «опубликован» once it is in, never the raw «sending». A comment nobody sends any more
// (e.g. Telegram took it and the database refused the record) is «не подтверждён», and a standby process cannot tell:
// the studio says so with comment_in_flight false or null (tests/test_poll_comment_before_request.py), here those
// answers are routed in
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, until, mockLog, push, MOCK }) {
  const mock = (path, body) => fetch(MOCK + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
  const title = `Пост с первым комментарием ${Date.now() % 100000}`; // its own row, also on a reused database
  const sent = await page.evaluate((t) => fetch('/api/send', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chat_id: '@comment_state', post: { rich_message: { blocks: [{ type: 'heading', size: 1, text: t }] }, comment: { html: '<p>Ссылки из эфира</p>' } } }),
  }).then((r) => r.json()), title);
  const pid = sent.post && sent.post.id;
  const listed = () => page.evaluate((id) => fetch('/api/posts').then((r) => r.json()).then((ps) => ps.find((p) => p.id === id)), pid);
  const comments = async () => (await mockLog()).filter((c) => c.method === 'sendRichMessage' && c.body.reply_parameters).length;
  const row = page.locator('#posts li.post-row', { hasText: title });
  // the list is read when the tab opens: open it, then wait until the row says what is expected
  const shown = async (re) => {
    await page.click('#side-tabs [data-tab=posts]');
    await page.waitForFunction(([t, src]) => {
      const li = [...document.querySelectorAll('#posts li.post-row')].find((r) => (r.textContent || '').includes(t));
      return !!li && new RegExp(src).test(li.textContent || '');
    }, [title, re.source], { timeout: 8000 }).catch(() => {});
    return (await row.locator('.stats').textContent().catch(() => '')) || '';
  };

  const before = await comments();
  await mock('/hold', { method: 'sendRichMessage' });
  let underWay, flying;
  try {
    const forward = { message_id: 9000 + (Date.now() % 1000), chat: { id: -2002, type: 'supergroup' }, is_automatic_forward: true };
    await push({ message: { ...forward, forward_origin: { type: 'channel', chat: { id: -1001 }, message_id: sent.message && sent.message.message_id } } });
    // the comment is in the mock's log: taken as "sending" and its request waits for the answer
    await until(async () => (await comments()) > before);
    flying = await listed();
    underWay = await shown(/комментарий: отправляется/);
  } finally {
    await mock('/release', { method: 'sendRichMessage' });
  }
  const answered = await until(async () => ((await listed()) || {}).comment_status === 'sent');
  const done = await shown(/комментарий: опубликован/);
  check('A first comment under way says «отправляется», then «опубликован», never the raw status',
    !!flying && flying.comment_status === 'sending' && flying.comment_in_flight === true && /комментарий: отправляется/.test(underWay)
      && !!answered && /комментарий: опубликован/.test(done) && !/sending/.test(underWay + done),
    JSON.stringify({ flying: flying && { status: flying.comment_status, in_flight: flying.comment_in_flight }, underWay, done }));

  // what the studio answers for a comment nobody sends any more (false) and from a standby process (null); the row
  // has reactions too, so the hint must sit on the comment's own note, not on the whole line
  let inFlight = /** @type {boolean | null} */ (false);
  const routed = async (route) => {
    const res = await route.fetch().catch(() => null);
    if (!res) return;
    const rows = await res.json();
    for (const p of rows) if (p.id === pid) Object.assign(p, { comment_status: 'sending', comment_in_flight: inFlight, reactions: { '👍': 3 } });
    await route.fulfill({ json: rows }).catch(() => {}); // a read still in flight when the check unroutes
  };
  const looks = () => row.locator('.stats [title]').evaluate((el) => {
    const probe = document.createElement('span');
    probe.style.color = 'var(--warn)';
    el.append(probe);
    const warn = getComputedStyle(probe).color;
    probe.remove();
    return { warn: getComputedStyle(el).color === warn, title: el.getAttribute('title') || '', whole: !!el.closest('.stats')?.hasAttribute('title') };
  }).catch(() => null);
  await page.route('**/api/posts', routed);
  const text = await shown(/не подтверждён, мог уйти/);
  const unconfirmed = await looks();
  await row.screenshot({ path: 'test-results/ui/comment-unconfirmed-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await row.scrollIntoViewIfNeeded().catch(() => {});
  await page.screenshot({ path: 'test-results/ui/comment-unconfirmed-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  inFlight = null;
  const standbyText = await shown(/отправляется или не подтверждён/);
  const standby = await looks();
  await page.unroute('**/api/posts', routed);
  const after = await shown(/комментарий: опубликован/);
  await page.click('#side-tabs [data-tab=drafts]');
  check('A comment nobody sends any more: «не подтверждён», not to post it again, in the warning colour; the hint on the note only',
    /👍 3 · комментарий: не подтверждён, мог уйти; не публикуйте заново, проверьте обсуждение/.test(text) && !/sending|отправляется/.test(text)
      && !!unconfirmed && unconfirmed.warn && !unconfirmed.whole && /send\.unrecorded/.test(unconfirmed.title) && /could not record/.test(unconfirmed.title)
      && /комментарий: опубликован/.test(after),
    JSON.stringify({ text, unconfirmed: unconfirmed && { ...unconfirmed, title: unconfirmed.title.slice(0, 80) }, after }));
  check('From a standby process the comment is «отправляется или не подтверждён», without a promise about a restart',
    /комментарий: отправляется или не подтверждён/.test(standbyText) && !!standby && !standby.warn && !standby.whole
      && /резерв/.test(standby.title) && !/перезапуск/.test(standbyText + standby.title),
    JSON.stringify({ standbyText, standby: standby && { ...standby, title: standby.title.slice(0, 80) } }));
}
