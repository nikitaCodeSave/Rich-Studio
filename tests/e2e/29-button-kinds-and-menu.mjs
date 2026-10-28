// a button's kind is a switch (link / answer / copy); «Опубликовать ▾» holds the rarer sends; a phone has tabs
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check }) {
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  await page.fill('#title', 'Кнопки по типам');
  await page.evaluate(() => { /** @type {HTMLDetailsElement} */ (document.getElementById('panel-kb')).open = true; });
  await page.locator('#kb button', { hasText: 'ряд кнопок' }).click();
  await page.locator('#kb button', { hasText: 'в ряд' }).click();
  await page.locator('#kb button', { hasText: 'в ряд' }).click();
  const cards = page.locator('#kb .kb-btn');
  await cards.nth(0).locator('input[type=url]').fill('https://t.me/telegram');
  await cards.nth(1).locator('select[aria-label="Что делает кнопка"]').selectOption('callback');
  await cards.nth(1).locator('input[type=text]').first().fill('Нравится');
  await cards.nth(1).locator('.cb-answer input[type=text]').fill('Спасибо!');
  await cards.nth(2).locator('select[aria-label="Что делает кнопка"]').selectOption('copy');
  await cards.nth(2).locator('input[placeholder="Промокод, адрес, ссылка"]').fill('PROMO2026');
  await page.click('#btn-save');
  // the save is async: wait until the stored draft is this one with its buttons, not a fixed pause
  let d = null;
  for (let i = 0; i < 50 && !d; i++) {
    d = await page.evaluate(async () => {
      const x = JSON.parse(localStorage.getItem('rs.lastDraft') || 'null');
      const r = x && (await fetch('/api/drafts/' + x).then((res) => res.json()));
      return r && r.title === 'Кнопки по типам' && r.payload?.reply_markup ? r : null;
    });
    if (!d) await page.waitForTimeout(200);
  }
  if (!d) throw new Error('draft «Кнопки по типам» with buttons was not saved in 10 s');
  const [b1, b2, b3] = d.payload.reply_markup.inline_keyboard[0];
  const fieldsShown = await cards.nth(1).textContent();
  await page.click('#btn-more');
  const menuOpen = await page.locator('#publish-menu').isVisible();
  const items = await page.locator('#publish-menu button').allTextContents();
  await page.keyboard.press('Escape');
  const menuClosed = await page.locator('#publish-menu').isHidden();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  const editorFirst = (await page.locator('.editor').isVisible()) && (await page.locator('.preview-col').isHidden());
  await page.click('#view-tabs [data-view=preview]');
  const previewTab = (await page.locator('.editor').isHidden()) && (await page.locator('#preview').isVisible());
  await page.click('#view-tabs [data-view=edit]');
  await page.screenshot({ path: 'test-results/ui/buttons-390.png', fullPage: true }).catch(() => {});
  await page.setViewportSize({ width: 1400, height: 900 });
  check('Button kinds are a switch, «Опубликовать ▾» menu, phone tabs',
    b1.url === 'https://t.me/telegram' && b2.text === 'Нравится' && /^b\w+$/.test(b2.callback_data) && b2.url === undefined && d.payload.callbacks[b2.callback_data] === 'Спасибо!'
      && JSON.stringify(b3.copy_text) === '{"text":"PROMO2026"}' && !/callback_data/.test(fieldsShown)
      && menuOpen && JSON.stringify(items) === '["Отложить…","Опрос…","Live Photo, платное…","Заменить пост…"]' && menuClosed && editorFirst && previewTab,
    JSON.stringify({ b1, b2, b3, callbacks: d.payload.callbacks, menuOpen, items, menuClosed, editorFirst, previewTab }));
}
