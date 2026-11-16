// A number field with a range in the spec («Relative size of the text font; 1-6»): the form's input has that range,
// and a value outside it is a warning of the check, not only a refusal of «В HTML» much later
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  await page.click('#btn-new');
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Заголовок' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  const size = page.locator('#pane-blocks .block').first().locator('input[type=number]').first();
  await size.waitFor({ timeout: 8000 }).catch(() => {});
  const bounds = await size.evaluate((i) => ({ min: i.getAttribute('min'), max: i.getAttribute('max') })).catch(() => null);
  await size.fill('16556');
  await page.waitForFunction(() => /16556 вне 1–6/.test(document.querySelector('#validation')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
  const out = await page.evaluate(() => ({
    warning: [...document.querySelectorAll('#validation li.w')].map((li) => li.textContent).find((t) => /16556/.test(t)) ?? null,
    pill: document.querySelector('#check-pill')?.className,
  }));
  check('A number field takes its range from the spec, and a value outside it is a warning of the check',
    bounds?.min === '1' && bounds?.max === '6' && /Заголовок №1 › Размер.*: 16556 вне 1–6 из спецификации/.test(out.warning || '') && /\bw\b/.test(out.pill || ''),
    JSON.stringify({ bounds, ...out }));
  await size.fill('1');
}
