// small UI bugs from the user review: field names render as text, an unfinished button link is caught, phone width
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  await applyJson({ rich_message: { blocks: [{ type: 'paragraph', text: 'кнопки' }, { type: 'table', cells: [['Время', 'Тема', 'Ведущий', 'Ссылка'], ['19:00', 'Обзор', 'Никита', 't.me']] }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  const opts = await page.locator('#opts').textContent();
  await page.evaluate(() => { for (const id of ['panel-kb', 'panel-opts']) /** @type {HTMLDetailsElement} */ (document.getElementById(id)).open = true; });
  await page.locator('#kb button', { hasText: 'ряд кнопок' }).click();
  await page.waitForSelector('#validation li.e', { timeout: 3000 }).catch(() => null);
  const verdict = await page.locator('#validation').textContent();
  await page.locator('#kb .obj button', { hasText: '✕' }).first().click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  // the table scrolls inside its block instead of pushing the block past the screen
  const wide = await page.evaluate(() => Math.max(...[...document.querySelectorAll('#pane-blocks .block, #pane-blocks .table-ed')].map((b) => Math.round(b.getBoundingClientRect().right))));
  await page.setViewportSize({ width: 1400, height: 900 });
  check('Field names are text, an empty button link is an error, the table fits a phone',
    !opts.includes('[object') && /message_effect_id/.test(opts) && /кнопка «Кнопка».*пустая ссылка/.test(verdict) && !/Пост проходит проверку/.test(verdict) && wide <= 390,
    JSON.stringify({ objectBug: opts.includes('[object'), verdict: verdict.slice(0, 160), wide }));
}
