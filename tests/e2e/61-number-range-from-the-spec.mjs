// A number field with a range in the spec («Relative size of the text font; 1-6»): the form's input has that range
// and outlines a value outside it, and such a value is a warning of the check, not only a refusal of «В HTML» later
/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, applyJson }) {
  await page.click('#btn-new');
  await applyJson({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Заголовок' }] } });
  await page.click('#mode-tabs [data-mode=blocks]');
  const size = page.locator('#pane-blocks .block').first().locator('input[type=number]').first();
  await size.waitFor({ timeout: 8000 }).catch(() => {});
  const look = () => size.evaluate((i) => ({ min: i.getAttribute('min'), max: i.getAttribute('max'), invalid: i.matches(':invalid'), border: getComputedStyle(i).borderColor })).catch(() => null);
  const valid = await look();
  await size.fill('16556');
  await page.waitForFunction(() => /16556 вне 1–6/.test(document.querySelector('#validation')?.textContent || ''), null, { timeout: 8000 }).catch(() => {});
  const outside = await look();
  const out = await page.evaluate(() => ({
    warning: [...document.querySelectorAll('#validation li.w')].map((li) => li.textContent).find((t) => /16556/.test(t)) ?? null,
    pill: document.querySelector('#check-pill')?.className,
  }));
  check('A number field takes its range from the spec, outlines a value outside it, and the check warns about it',
    valid?.min === '1' && valid?.max === '6' && !valid.invalid && outside?.invalid && outside.border !== valid.border
    && /Заголовок №1 › Размер.*: 16556 вне 1–6 из спецификации/.test(out.warning || '') && /\bw\b/.test(out.pill || ''),
    JSON.stringify({ valid, outside, ...out }));
  await size.fill('1');
}
