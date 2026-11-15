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
}
