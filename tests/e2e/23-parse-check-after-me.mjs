// after «Мне в личку» the preview is checked against the blocks Telegram made of the post
import { readFileSync } from 'node:fs';

/** @param {import('./harness.mjs').Ctx} ctx */
export default async function ({ page, check, state, showJson }) {
  const captured = JSON.parse(readFileSync(new URL('../telegram_parse/captured.json', import.meta.url), 'utf8'));
  const barText = () => page.evaluate(() => document.querySelector('#parse-check').shadowRoot.textContent.replace(/\s+/g, ' ').trim());
  await page.click('#btn-new');
  await page.waitForTimeout(300);
  // the mock answers with the blocks the real Telegram made of this source
  await page.click('#mode-tabs [data-mode=markdown]');
  await page.fill('#src-markdown', captured['md-math'].source);
  await page.waitForTimeout(400);
  await page.click('#btn-me');
  await page.waitForSelector('#parse-check >> text=так же', { timeout: 5000 }).catch(() => {});
  const same = await barText();
  await page.click('#parse-check >> text=Как разобрал Telegram');
  await page.waitForTimeout(300);
  const showsTelegram = await page.locator('#parse-check button[aria-pressed=true]').count();
  await page.screenshot({ path: 'test-results/ui/parse-check-1400.png' });
  await page.click('#parse-check >> text=В визуальный');
  await page.waitForTimeout(500);
  const visual = await page.locator('#mode-tabs [data-mode=blocks].on').count();
  await page.screenshot({ path: 'test-results/ui/parse-to-blocks-1400.png' });
  await showJson();
  const blocks = (await state()).rich_message.blocks;
  await page.click('#mode-tabs [data-mode=markdown]');
  const sourceKept = await page.inputValue('#src-markdown');
  // a source the mock does not know comes back as one plain paragraph: Telegram "parsed it otherwise"
  await page.fill('#src-markdown', '**жирный** текст');
  await page.waitForTimeout(400);
  const stale = await barText();
  await page.click('#btn-me');
  await page.waitForSelector('#parse-check >> text=иначе', { timeout: 5000 }).catch(() => {});
  const diff = await barText();
  await page.screenshot({ path: 'test-results/ui/parse-check-diff-1400.png' });
  check('«Мне в личку» checks the preview against Telegram and turns Markdown into visual blocks',
    /так же/.test(same) && showsTelegram === 1 && visual === 1 && Array.isArray(blocks) && blocks.some((b) => b.type === 'mathematical_expression') && sourceKept === captured['md-math'].source
      && /изменился/.test(stale) && /иначе/.test(diff) && /Telegram:/.test(diff),
    JSON.stringify({ same, showsTelegram, visual, blocks, sourceKept, stale, diff }).slice(0, 900));
}
