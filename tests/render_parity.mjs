// Preview parity with the real Telegram parser.
// tests/telegram_parse/captured.json holds HTML and Markdown sources together with the blocks the real
// Bot API returned for them (scripts/capture_telegram_parse.py). For every case the preview of the
// source and the preview of Telegram's blocks must describe the same post: same blocks, same inline
// formatting, same text. A mismatch means render.js shows the author something Telegram will not.
// Run: node tests/render_parity.mjs (CHROMIUM=/path/to/chromium to use a preinstalled browser)
/* global RichRender -- defined by render.js inside the page */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = (f) => fileURLToPath(new URL(f, import.meta.url));
const captured = JSON.parse(readFileSync(here('telegram_parse/captured.json'), 'utf8'));
// differences we accept on purpose, with the reason (cases.json: "known_gap")
const known = Object.fromEntries(JSON.parse(readFileSync(here('telegram_parse/cases.json'), 'utf8')).filter((c) => c.known_gap).map((c) => [c.name, c.known_gap]));

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
const page = await browser.newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message));
await page.setContent('<!doctype html><meta charset="utf-8"><body><div id="out"></div></body>');
for (const f of ['vendor/markdown-it.min.js', 'vendor/katex/katex.min.js', 'render.js']) await page.addScriptTag({ path: here('../app/static/' + f) });

const outline = (rich_message) => page.evaluate((rm) => RichRender.outline({ rich_message: rm }), rich_message);

let fails = 0;
let gaps = 0;
for (const [name, c] of Object.entries(captured)) {
  if (!c.blocks) { console.log(`SKIP  ${name}: Telegram отклонил (${c.error})`); continue; }
  const gap = known[name];
  const mine = await outline({ [c.format]: c.source });
  const theirs = await outline({ blocks: c.blocks });
  const ok = mine === theirs;
  if (!ok && !gap) fails++;
  if (!ok && gap) gaps++;
  console.log(`${ok ? 'PASS' : gap ? 'KNOWN' : 'FAIL'}  ${name}${!ok && gap ? ': ' + gap : ''}`);
  if (ok && gap) console.log(`      расхождение «${gap}» исчезло: уберите known_gap из cases.json`);
  if (!ok) console.log(`      предпросмотр: ${mine}\n      Telegram:     ${theirs}`);
}
if (pageErrors.length) { fails++; console.log('Ошибки страницы: ' + pageErrors.join('; ')); }
await browser.close();
console.log(`\n${Object.keys(captured).length - fails - gaps}/${Object.keys(captured).length} совпадают с Telegram` + (gaps ? `, известных расхождений: ${gaps}` : ''));
process.exit(fails ? 1 : 0);
