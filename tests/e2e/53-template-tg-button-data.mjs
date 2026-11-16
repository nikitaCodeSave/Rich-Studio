// post templates (#191): data="…" of a <tg-button> is the button's callback_data, a plain string, not a link
// (html-export.js writes it so, render.js reads it so). The HTML5 oracle of 40-post-templates.mjs, the browser's own
// parser: a text field in data is accepted and stays inside the attribute, as it was given, for any value; data of
// <object> and url of <tg-button> are links still, and a <tg-button> whose data has an answer in callbacks is refused.
// The template form offers data as a field unless the button has an answer; a button of the first comment or of the
// rich reply has none whatever callbacks say (the studio answers no press there), and the service saves them all
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const quick = { timeout: 8000 };
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const formValue = () => page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form')).value);
  const fieldsAre = (n) => page.waitForFunction((x) => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.fields?.length === x, n, quick).catch(() => {});
  const title = 'Данные кнопки ' + (Date.now() % 100000); // its own template, also on a reused database
  const row = (src) => `<tg-button-row>${src}</tg-button-row>`;

  // 1. the oracle
  const oracle = await page.evaluate(async ([accepted, refused, hostile]) => {
    const call = (method, path, body) => fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).then(async (r) => ({ status: r.status, body: await r.json() }));
    // parsed into an inert <template>, as render.js does: nothing runs and no CSP check fires
    const parse = (src) => { const tpl = document.createElement('template'); tpl.innerHTML = src; return tpl.content; };
    const shape = (src) => {
      const out = [];
      const walk = (n) => n.childNodes.forEach((c) => { if (c.nodeType === 1) { out.push(c.localName + '[' + [...c.attributes].map((a) => a.name).sort().join(',') + ']'); walk(c); } });
      walk(parse(src));
      return out.join(' ');
    };
    const data = (src) => parse(src).querySelector('tg-button')?.getAttribute('data');
    const changed = [];
    let filled = 0;
    for (const src of accepted) {
      const t = await call('POST', '/api/templates', { title: 'oracle', post: { rich_message: { html: src }, callbacks: { vote: 'Спасибо' } }, fields: [{ key: 'x', label: 'x', default: 'like' }] });
      if (t.status !== 200) { changed.push({ src, refused: t.body.detail }); continue; }
      const html = async (x) => {
        const d = await call('POST', `/api/templates/${t.body.id}/draft`, { version: t.body.version, values: { x } });
        await call('DELETE', '/api/drafts/' + d.body.id);
        return d.body.payload.rich_message.html;
      };
      const plain = shape(await html('x'));
      for (const v of hostile) {
        filled++;
        const got = await html(v);
        if (shape(got) !== plain || data(got) !== v) changed.push({ src, v, plain, got: shape(got), data: data(got) });
      }
      await call('DELETE', `/api/templates/${t.body.id}?version=${t.body.version}`);
    }
    const refusals = [];
    for (const src of refused) {
      const r = await call('POST', '/api/templates', { title: 'oracle', post: { rich_message: { html: src }, callbacks: { vote: 'Спасибо' } }, fields: [{ key: 'x', label: 'x', default: 'vote' }] });
      refusals.push({ src, status: r.status, detail: r.body.detail });
      if (r.status === 200) await call('DELETE', `/api/templates/${r.body.id}?version=${r.body.version}`);
    }
    return { changed, filled, refusals };
  }, [
    [
      row('<tg-button type="callback_data" data="{{x}}">Да</tg-button>'),
      row('<tg-button data=\'{{x}}\' style="primary">{{x}}</tg-button>'),
      row('<tg-button type="callback_data" data="{{x}}">Да</tg-button><tg-button data="vote">Голос</tg-button>'),
    ],
    [
      '<object data="{{x}}"></object>',
      row('<tg-button type="url" url="{{x}}">Сайт</tg-button>'),
      row('<tg-button type="callback_data" data="{{x}}">Да</tg-button>'),
    ],
    ['"><b>x</b>', "' onclick='x", 'javascript:alert(1)', 'a&b &amp; <i>', '</tg-button><b>y</b>', 'z data=1 type=url'],
  ]);
  const notRefused = oracle.refusals.filter((r) => r.status !== 422);
  check('HTML5 oracle: a text field in data of a <tg-button> stays inside it for any value; <object data>, <tg-button url> and data with an answer are refused',
    oracle.filled > 0 && oracle.changed.length === 0 && notRefused.length === 0
      && /callbacks/.test(oracle.refusals[2]?.detail || '') && /ссылку/.test(oracle.refusals[0]?.detail || ''),
    JSON.stringify({ filled: oracle.filled, changed: oracle.changed.slice(0, 3), refusals: oracle.refusals }));

  // 2. the template form; a button under the post shares the callback_data of the first <tg-button>
  await page.click('#btn-new');
  await applyJson({
    rich_message: { html: '<p>Голосуем</p>' + row('<tg-button type="callback_data" data="vote">Да</tg-button><tg-button data="free">Нет</tg-button><tg-button type="url" url="https://e.example/">Сайт</tg-button>') },
    reply_markup: { inline_keyboard: [[{ text: 'Голос', callback_data: 'vote' }]] },
    comment: { blocks: [{ type: 'paragraph', text: 'Обсуждение' }, { type: 'buttons', buttons: [{ text: 'Ещё', callback_data: 'vote' }] }] },
    callbacks: { vote: { text: 'Спасибо', reply: { rich_message: { html: '<p>Голос учтён</p>' + row('<tg-button data="vote">Ещё раз</tg-button>') } } } },
  });
  await page.click('#side-tabs [data-tab=drafts]');
  await page.evaluate(() => { /** @type {any} */ (document.querySelector('#templates')).open = true; });
  await page.locator('#templates button', { hasText: 'Текущий пост шаблоном' }).click();
  const form = page.locator('dialog[open] rs-template-form');
  await form.locator('li').first().waitFor(quick);
  const rows = await form.locator('li').evaluateAll((lis) => lis.map((li) => ({
    where: li.querySelector('.where')?.textContent || '', value: li.querySelector('.value')?.textContent || '', field: !!li.querySelector('button'), why: li.querySelector('.why')?.textContent || '',
  })));
  await form.locator('li', { hasText: 'free' }).scrollIntoViewIfNeeded().catch(() => {});
  for (const width of [1400, 390]) {
    await page.setViewportSize({ width, height: width === 1400 ? 900 : 844 });
    await page.screenshot({ path: `test-results/ui/template-button-data-${width}.png` }).catch(() => {});
  }
  await page.setViewportSize({ width: 1400, height: 900 });
  const votes = rows.filter((r) => r.value === 'vote');
  const free = rows.find((r) => r.value === 'free');
  // vote under the post and in its HTML: with why; vote in the comment (JSON) and in the rich reply (HTML): a field
  check('Template form: data of a <tg-button> is offered as a field unless the button has an answer; a comment or rich reply button has none',
    !!free && free.field && free.where === 'Данные для бота' && votes.length === 4
      && votes.filter((r) => !r.field && r.why.includes('связан с ответом на нажатие в callbacks')).length === 2
      && votes.filter((r) => r.field && !r.why).length === 2 && !rows.some((r) => r.value === 'https://e.example/'),
    JSON.stringify(rows));

  // 3. every string it offers becomes a field, and the service takes the template
  const offered = await form.locator('li button').count();
  for (let i = 1; i <= offered; i++) {
    await form.locator('li button').first().click(quick).catch(() => {});
    await fieldsAre(i);
  }
  const made = await formValue();
  await form.locator('input[name=title]').fill(title);
  await page.locator('dialog[open] .dlg-actions button', { hasText: 'Сохранить шаблон' }).click();
  // saved (the row shows up) or refused (the form shows the error)
  await page.waitForFunction((t) => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.error
    || [...(document.querySelector('#templates')?.shadowRoot?.querySelectorAll('li') || [])].some((li) => (li.textContent || '').includes(t)), title, quick).catch(() => {});
  const error = await page.evaluate(() => /** @type {any} */ (document.querySelector('dialog[open] rs-template-form'))?.error || '');
  const saved = (await api('GET', '/api/templates')).find((t) => t.title === title);
  if (saved) await api('DELETE', `/api/templates/${saved.id}?version=${saved.version}`);
  if (await page.locator('dialog[open]').count()) await page.keyboard.press('Escape'); // a refused template leaves it open
  const html = made.post.rich_message.html;
  check('Template form: data with an answer stays as it is, the rest made fields, and the service saves the template',
    offered > 5 && !error && !!saved && saved.fields.length === offered && html.includes('data="vote"') && /data="\{\{\w+\}\}"/.test(html)
      && /\{\{\w+\}\}/.test(made.post.comment.blocks[1].buttons[0].callback_data) && /data="\{\{\w+\}\}"/.test(made.post.callbacks.vote.reply.rich_message.html),
    JSON.stringify({ offered, error, html, fields: saved && saved.fields.map((f) => f.default) }));
}
