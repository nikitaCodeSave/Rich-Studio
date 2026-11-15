// JSON typed without «Применить» survives a tab switch and a reload, never goes to Telegram, and the JSON tab says
// when the post changed after it, with «Применить мои» and «Отбросить»
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, mockLog, until, showJson, applyJson }) {
  const draft = () => page.evaluate(async () => {
    const id = new URLSearchParams(location.search).get('draft');
    return id ? (await fetch('/api/drafts/' + id)).json() : null;
  });
  // the exact text, as the draft keeps it: a string nobody else writes, so it is the buffer and not the post
  const keeps = async (text) => JSON.stringify((await draft())?.payload ?? null).includes(JSON.stringify(text));
  const saved = (text, yes = true) => until(async () => (await keeps(text)) === yes);
  const json = () => page.inputValue('#src-json');
  const bar = page.locator('#json-stale');
  const shown = () => bar.isVisible();
  const preview = () => page.locator('#preview .tg-preview').textContent();
  const visual = () => page.click('#mode-tabs [data-mode=blocks]');
  const typeInPost = async (s) => {
    await visual();
    await page.locator('#pane-blocks .rte').first().click();
    await page.keyboard.press('End');
    await page.keyboard.type(s);
  };
  const quick = { timeout: 3000 };

  await page.click('#btn-new');
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'Пост' }] } });
  await until(async () => (await draft())?.payload?.rich_message?.blocks?.[0]?.text === 'Пост');

  // invalid JSON, not applied: through Визуально → JSON and a reload of the page
  const broken = '{"rich_message": {"blocks": [{"type": "paragraph", "text": "Неприменённый';
  await page.fill('#src-json', broken);
  await visual();
  await showJson();
  const afterTabs = await json();
  const barAfterTabs = await shown();
  await saved(broken);
  await page.reload();
  await page.waitForSelector('.block');
  await showJson();
  const afterReload = await json();
  const d = await draft();
  check('Unapplied invalid JSON survives a tab switch and a reload, the post stays as it was',
    afterTabs === broken && afterReload === broken && !barAfterTabs
      && JSON.stringify(d?.payload?.rich_message) === JSON.stringify({ blocks: [{ type: 'paragraph', text: 'Пост' }] }) && /Пост/.test(await preview()),
    JSON.stringify({ afterTabs, afterReload, barAfterTabs, rich_message: d?.payload?.rich_message }));

  // the post changed in the visual mode after the JSON was typed
  await typeInPost('!');
  await showJson();
  const barText = await bar.textContent(quick).catch(() => '');
  const stale = await shown();
  // for the screenshots: the formatting toolbar goes once the block loses focus, the preview catches up
  await page.waitForFunction(() => !document.querySelector('.toolbar.on') && /Пост!/.test(document.querySelector('#preview').shadowRoot.textContent), null, quick).catch(() => {});
  await page.screenshot({ path: 'test-results/ui/json-stale-1400.png' }).catch(() => {});
  await page.setViewportSize({ width: 390, height: 844 });
  await bar.scrollIntoViewIfNeeded(quick).catch(() => {});
  await page.screenshot({ path: 'test-results/ui/json-stale-390.png' }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  await bar.getByRole('button', { name: 'Отбросить' }).click(quick).catch(() => {});
  const dropped = await json().then((t) => { try { return JSON.parse(t).rich_message; } catch { return t; } });
  await saved(broken, false);
  check('After a visual change the JSON tab says so; «Отбросить» shows the post as it is now',
    stale && /Пост изменился после ваших правок JSON/.test(barText) && /Применить мои/.test(barText)
      && JSON.stringify(dropped) === JSON.stringify({ blocks: [{ type: 'paragraph', text: 'Пост!' }] }) && !(await shown()) && !(await keeps(broken)),
    JSON.stringify({ stale, barText, dropped }));

  // «Применить мои»: the JSON wins over the change made after it
  const mine = '{"rich_message": {"blocks": [{"type": "paragraph", "text": "Мой JSON"}]}}';
  await page.fill('#src-json', mine);
  await typeInPost('?');
  await showJson();
  const staleAgain = await shown();
  await bar.getByRole('button', { name: 'Применить мои' }).click(quick).catch(() => {});
  await until(async () => (await draft())?.payload?.rich_message?.blocks?.[0]?.text === 'Мой JSON');
  const applied = await draft();
  check('«Применить мои» makes the JSON the post and drops it from the draft',
    staleAgain && !(await shown()) && /Мой JSON/.test(await preview())
      && applied?.payload?.rich_message?.blocks?.[0]?.text === 'Мой JSON' && !(await keeps(mine)),
    JSON.stringify({ staleAgain, rich_message: applied?.payload?.rich_message }));

  // what is not applied never goes out
  const later = '{"rich_message": {"markdown": "Не уйдёт"}}';
  await page.fill('#src-json', later);
  await saved(later);
  const before = (await mockLog()).filter((c) => c.method === 'sendRichMessage').length;
  await page.click('#btn-publish');
  const sends = await until(async () => { const s = (await mockLog()).filter((c) => c.method === 'sendRichMessage'); return s.length > before && s; });
  const sent = sends ? sends.at(-1).body : null;
  check('Unapplied JSON does not go to Telegram',
    !!sent && sent.rich_message?.blocks?.[0]?.text === 'Мой JSON' && !JSON.stringify(sent).includes('Не уйдёт') && !('_studio' in sent),
    JSON.stringify(sent));

  // «Применить JSON» applies it and drops it from the draft
  await showJson();
  await page.click('#json-apply');
  await saved(later, false);
  const after = await draft();
  check('«Применить JSON» drops the applied text from the draft',
    after?.payload?.rich_message?.markdown === 'Не уйдёт' && !(await keeps(later)) && (await json()) !== later,
    JSON.stringify({ rich_message: after?.payload?.rich_message }));
}
