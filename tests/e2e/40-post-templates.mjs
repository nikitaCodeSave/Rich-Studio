// post templates: the open post becomes a template with a field, its form makes a new draft where the value is text
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  const api = (method, path, body) => page.evaluate(([m, p, b]) => fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined }).then((r) => r.json()), [method, path, body]);
  const shots = async (name) => {
    await page.screenshot({ path: `test-results/ui/${name}-1400.png` }).catch(() => {});
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `test-results/ui/${name}-390.png` }).catch(() => {});
    await page.setViewportSize({ width: 1400, height: 900 });
  };
  const title = 'Дайджест ' + (Date.now() % 100000); // its own template, also on a reused database
  const value = '<b>Новости</b> & «всё»';
  await page.click('#btn-new');
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Дайджест недели' }, { type: 'paragraph', text: 'Главная новость' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  await page.click('#side-tabs [data-tab=drafts]');
  const before = await page.evaluate(() => new URLSearchParams(location.search).get('draft'));

  await page.locator('#templates summary').click();
  await page.locator('#templates button', { hasText: 'Текущий пост шаблоном' }).click();
  const form = page.locator('dialog[open] rs-template-form');
  await form.locator('input[name=title]').fill(title);
  await form.locator('li', { hasText: 'Главная новость' }).locator('button').click();
  await form.locator('input[name=label-text]').fill('Главное');
  await shots('template-edit');
  await page.locator('dialog[open] .dlg-actions button', { hasText: 'Сохранить шаблон' }).click();
  const row = page.locator('#templates li', { hasText: title });
  await row.waitFor({ timeout: 8000 });
  const saved = (await api('GET', '/api/templates')).find((t) => t.title === title);

  await row.locator('button', { hasText: 'Новый пост' }).click();
  const fill = page.locator('dialog[open] rs-template-form');
  const prefilled = await fill.locator('input[name=text]').inputValue();
  await fill.locator('input[name=text]').fill(value);
  await shots('template-fill');
  await page.locator('dialog[open] .dlg-actions button', { hasText: 'Создать черновик' }).click();
  await page.waitForFunction((v) => (document.querySelector('#preview')?.shadowRoot?.textContent || '').includes(v), value, { timeout: 8000 }).catch(() => {});
  const preview = await page.evaluate(() => {
    const root = /** @type {ShadowRoot} */ (document.querySelector('#preview')?.shadowRoot);
    return { text: root.textContent || '', bold: [...root.querySelectorAll('b, strong')].map((b) => b.textContent || '') };
  });
  const id = await page.evaluate(() => new URLSearchParams(location.search).get('draft'));
  const draft = id ? await api('GET', '/api/drafts/' + id) : null;
  const blocks = draft ? draft.payload.rich_message.blocks : [];
  check('A post becomes a template with a field; its form makes a new draft with the value as text in the preview',
    !!saved && saved.fields.length === 1 && saved.fields[0].label === 'Главное' && prefilled === 'Главная новость'
      && !!id && id !== before && !!draft && draft.title === title && !draft.chat_ids
      && blocks[0].text === 'Дайджест недели' && blocks[1].text === value
      && preview.text.includes('Дайджест недели') && preview.text.includes(value) && !preview.bold.some((b) => b.includes('Новости'))
      && (await page.inputValue('#title')) === title,
    JSON.stringify({ saved: saved && saved.fields, prefilled, id, before, title: draft && draft.title, chats: draft && draft.chat_ids, blocks, bold: preview.bold }));

  // The oracle: the browser's own HTML5 parser (as render.js) reads every HTML template the studio accepts with the same
  // tags and attributes whatever the value is; and the templates it refuses would really let a value add attributes.
  const oracle = await page.evaluate(async ([accepted, refused, hostile, naive]) => {
    const call = (method, path, body) => fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined }).then(async (r) => ({ status: r.status, body: await r.json() }));
    // tags and attribute names in document order: what a value must never change. Parsed into an inert <template>,
    // as render.js does: nothing runs and no CSP check fires
    const shape = (src) => {
      const tpl = document.createElement('template');
      tpl.innerHTML = src;
      const out = [];
      const walk = (n) => n.childNodes.forEach((c) => {
        if (c.nodeType === 1) { out.push(c.localName + '[' + [...c.attributes].map((a) => a.name).sort().join(',') + ']'); walk(c); } else if (c.nodeType === 8) out.push('#comment');
      });
      walk(tpl.content);
      return out.join(' ');
    };
    const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
    const field = [{ key: 'x', label: 'x' }];
    const changed = [];
    let filled = 0;
    for (const src of accepted) {
      const t = await call('POST', '/api/templates', { title: 'oracle', post: { rich_message: { html: src } }, fields: field });
      if (t.status !== 200) { changed.push({ src, refused: t.body.detail }); continue; }
      const html = async (x) => {
        const d = await call('POST', `/api/templates/${t.body.id}/draft`, { version: t.body.version, values: { x } });
        await call('DELETE', '/api/drafts/' + d.body.id);
        return d.body.payload.rich_message.html;
      };
      const plain = shape(await html('x'));
      for (const v of hostile) { filled++; const got = shape(await html(v)); if (got !== plain) changed.push({ src, v, plain, got }); }
      await call('DELETE', `/api/templates/${t.body.id}?version=${t.body.version}`);
    }
    // a refused template filled naively (escaped value put in place) would change the shape for some value
    const refusals = [];
    for (const src of refused) {
      const r = await call('POST', '/api/templates', { title: 'oracle', post: { rich_message: { html: src } }, fields: field });
      const plain = shape(src.replaceAll('{{x}}', 'x'));
      refusals.push({ src, status: r.status, dangerous: naive.some((v) => shape(src.replaceAll('{{x}}', esc(v))) !== plain) });
    }
    return { changed, filled, refusals };
  }, [
    [
      '<p title = \'{{x}}\' dir="ltr">{{x}} &amp; <code>{{x}}</code></p>',
      '<p title\n=\r\n"{{x}}">x</p>',
      "<p\ttitle='{{x}}'/>",
      '<!---->{{x}}<!-- <b> -- > --><br/>',
      '<a href="#top" title="{{x}}">x</a> &amp; {{x}} > 1',
      '<tg-spoiler>{{x}}</tg-spoiler><blockquote cite="a">{{x}}</blockquote>',
      '<table><tr><td title="{{x}}">{{x}}</td></tr></table>',
      '<details><summary title="{{x}}">{{x}}</summary><p>{{x}}</p></details>',
    ],
    [
      '<span title=\v"{{x}}">x</span>',
      '<span title=\u00a0"{{x}}">x</span>',
      '<a ="x>T<a title=">" {{x}}>link</a>',
      '<i=" x>T<b title=">" {{x}}>y</b>',
      '<p title="a"dir="{{x}}">x</p>',
      '<xmp><p title="</xmp><b {{x}}>">y</p>',
      '<p>a <{{x}}</p>',
    ],
    ['"><b>x</b>', "' onclick='x", 'z href=https://evil.example onclick=alert(1)', '</p><i>', '&lt;b&gt;', '<!--', '<script>alert(1)</script>', '\v"x', 'b>y'],
    // what a refused template would turn into attributes; no on* here: Chromium reports a CSP block as soon as it parses
    // a handler, even into an inert <template> (see 01-hostile-html.mjs)
    ['z href=https://evil.example data-x=1', '"><b>x</b>', 'b>y'],
  ]);
  const notRefused = oracle.refusals.filter((r) => r.status !== 422);
  const harmless = oracle.refusals.filter((r) => !r.dangerous).map((r) => r.src);
  check('HTML5 oracle: an accepted template keeps its tags and attributes for any value; the refused ones would not',
    oracle.filled > 0 && oracle.changed.length === 0 && notRefused.length === 0 && oracle.refusals.slice(0, 4).every((r) => r.dangerous),
    JSON.stringify({ filled: oracle.filled, changed: oracle.changed.slice(0, 3), notRefused, harmless }));
}
