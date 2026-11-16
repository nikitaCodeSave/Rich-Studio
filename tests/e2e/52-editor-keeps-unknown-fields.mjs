// A top-level field of the post outside models.Post (`foo_bar`, saved by an agent through the API or MCP) is not lost
// when the person opens the draft in the editor, edits it and saves (AGENTS.md rule 1): the editor keeps it as it came,
// the check's warning about it shows, and the JSON tab shows it, so it can be changed or taken out there. Publishing
// sends it with the post as the API would: it stays in the record and never reaches Telegram. The editor puts no
// channel into the post itself: the channels go beside it.
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, until, BASE }) {
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const field = { from: 'агент', n: 1 };
  const d = await api('POST', '/api/drafts', { title: 'Поле вне поста', chat_ids: ['@test'], post: { rich_message: { blocks: [{ type: 'paragraph', text: 'Текст агента' }] }, foo_bar: field } });
  const draft = () => api('GET', '/api/drafts/' + d.id);
  const wait = { timeout: 10000 };
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const warning = 'поле «foo_bar» на верхнем уровне поста не используется';
  // the check under the editor says it (or no longer does)
  const says = (text, on = true) => page.waitForFunction(([w, want]) => (document.querySelector('#validation')?.textContent || '').includes(w) === want, [text, on], wait).then(() => true, () => false);
  const noChannel = (post) => !!post && !('chat_id' in post) && !('chat_ids' in post);
  const save = async (text) => {
    await page.click('#btn-save');
    return until(async () => { const x = await draft(); return text(x.payload) && x; }, 12000);
  };

  await page.goto(`${BASE}/?draft=${d.id}`);
  await page.waitForSelector('.block');
  const shown = await says(warning);
  await page.screenshot({ path: 'test-results/ui/unknown-field-1400.png' }).catch(() => {});
  check('The check\'s warning about a field outside the post shows in the editor', shown,
    JSON.stringify(await page.locator('#validation').textContent()));

  // an edit in the visual mode, then «Сохранить»
  await page.locator('#pane-blocks .rte').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type(' и человека');
  const saved = await save((p) => p?.rich_message?.blocks?.[0]?.text === 'Текст агента и человека');
  check('Opened, edited and saved in the editor, the draft keeps the field outside the post as it came',
    !!saved && same(saved.payload.foo_bar, field) && saved.version > d.version && noChannel(saved.payload) && same(saved.chat_ids, ['@test']),
    JSON.stringify(saved?.payload));

  // the JSON tab shows it, and it is changed there
  await page.click('#mode-tabs [data-mode=json]');
  const json = JSON.parse(await page.inputValue('#src-json'));
  await page.fill('#src-json', JSON.stringify({ ...json, foo_bar: 2 }));
  await page.click('#json-apply');
  const changed = await save((p) => p?.foo_bar === 2);
  check('The JSON tab shows the field and changes it',
    same(json.foo_bar, field) && !!changed && changed.payload.rich_message?.blocks?.[0]?.text === 'Текст агента и человека',
    JSON.stringify({ json, changed: changed?.payload }));

  // publishing: the field goes with the post as the API takes it, the channels beside it; Telegram gets neither
  const before = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  const request = page.waitForRequest((r) => r.method() === 'POST' && new URL(r.url()).pathname === '/api/send', wait).catch(() => null);
  await page.click('#btn-publish');
  const body = (await request)?.postDataJSON();
  const sends = await until(async () => { const s = (await mockLog()).filter((c) => c.method === 'sendRichMessage'); return s.length > before && s; });
  const sent = sends ? sends.at(-1).body : null;
  const row = await until(async () => (await api('GET', '/api/posts')).find((x) => x.draft_id === d.id && x.status === 'sent'));
  const record = row && (await api('GET', '/api/posts/' + row.id));
  check('Published from the editor, the field stays in the record and does not go to Telegram; the channels go beside the post',
    body?.post?.foo_bar === 2 && noChannel(body?.post) && same(body?.chat_ids, ['@test'])
      && !!sent && !JSON.stringify(sent).includes('foo_bar') && record?.payload?.foo_bar === 2,
    JSON.stringify({ body, sent, record: record?.payload }));

  // taken out in the JSON tab, it is gone from the draft and so is the warning
  await page.click('#mode-tabs [data-mode=json]');
  const now = JSON.parse(await page.inputValue('#src-json'));
  delete now.foo_bar;
  await page.fill('#src-json', JSON.stringify(now));
  await page.click('#json-apply');
  const removed = await save((p) => !!p && !('foo_bar' in p) && p.rich_message?.blocks?.[0]?.text === 'Текст агента и человека');
  check('Taken out in the JSON tab, the field leaves the draft and the warning goes', !!removed && (await says(warning, false)),
    JSON.stringify(removed?.payload));
  await page.click('#mode-tabs [data-mode=blocks]');

  // a chat put into the post by someone else is kept too, and the check's error about it shows: the post does not go
  const other = await api('POST', '/api/drafts', { title: 'Канал внутри поста', chat_ids: ['@test'], post: { rich_message: { blocks: [{ type: 'paragraph', text: 'Куда?' }] }, chat_id: '@other' } });
  await page.goto(`${BASE}/?draft=${other.id}`);
  await page.waitForSelector('.block');
  const refused = await says('поле «chat_id» на верхнем уровне поста — ошибка');
  await page.locator('#pane-blocks .rte').first().click();
  await page.keyboard.press('End');
  await page.keyboard.type(' Сюда');
  await page.click('#btn-save');
  const kept = await until(async () => { const x = await api('GET', '/api/drafts/' + other.id); return x.payload?.rich_message?.blocks?.[0]?.text === 'Куда? Сюда' && x; }, 12000);
  const sendsBefore = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  const answer = page.waitForResponse((r) => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/send', wait).catch(() => null);
  await page.click('#btn-publish');
  const status = (await answer)?.status();
  const sendsAfter = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  check('A chat inside the post stays with the draft, the check says it is an error, and publishing is refused before Telegram',
    refused && kept?.payload?.chat_id === '@other' && status === 400 && sendsAfter === sendsBefore,
    JSON.stringify({ refused, kept: kept?.payload, status, sendsBefore, sendsAfter }));
  await page.click('#btn-new'); // leave the draft with the error for the checks after this one
}
