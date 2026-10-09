// Screenshots of the preview for every example post, light and dark, plus layout checks.
// Pixel-exact comparison is not attempted: fonts differ between machines and CI. Instead every
// post must render without errors, every block must take up space, and nothing may stick out of
// the message bubble. The PNGs land in test-results/preview/ for a person to look at (CI keeps
// them as an artifact). No studio needed.
// Run: node tests/preview_shots.mjs (CHROMIUM=/path/to/chromium to use a preinstalled browser)
/* global RichRender -- defined by render.js inside the page */
import { mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const path = (rel) => fileURLToPath(new URL('../' + rel, import.meta.url));
const OUT = path('test-results/preview');
mkdirSync(OUT, { recursive: true });

const posts = readdirSync(path('examples')).sort().flatMap((f) => {
  const text = readFileSync(path('examples/' + f), 'utf8');
  if (f.endsWith('.json')) return [{ name: f, post: { rich_message: { blocks: JSON.parse(text) } } }];
  if (f.endsWith('.html')) return [{ name: f, post: { rich_message: { html: text } } }];
  return [];
});

const results = [];
const check = (name, ok, info = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !info ? '' : '  ' + info}`); };

const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
// the editor's preview column is about this wide
const page = await browser.newPage({ viewport: { width: 480, height: 800 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
await page.setContent('<!doctype html><meta charset="utf-8"><body style="margin:0"><div id="root" class="tg-preview"></div></body>');
for (const f of ['vendor/katex/katex.min.css', 'tg.css']) await page.addStyleTag({ path: path('app/static/' + f) });
for (const f of ['vendor/markdown-it.min.js', 'vendor/katex/katex.min.js', 'render.js']) await page.addScriptTag({ path: path('app/static/' + f) });

for (const { name, post } of posts) {
  for (const theme of ['light', 'dark']) {
    const layout = await page.evaluate(async ([p, dark]) => {
      const root = /** @type {HTMLElement} */ (document.getElementById('root'));
      root.classList.toggle('tg-dark', dark);
      root.replaceChildren(RichRender.render(p, { channelTitle: 'Канал' }));
      await document.fonts.ready;
      const bubble = /** @type {HTMLElement} */ (root.querySelector('.tg-bubble'));
      const body = /** @type {HTMLElement} */ (root.querySelector('.tg-body'));
      const box = bubble.getBoundingClientRect();
      // a block with no size means its renderer is missing or broken; empty anchors are invisible on purpose
      const empty = [...body.children].filter((c) => !(c.localName === 'a' && !c.textContent?.trim()) && c.getBoundingClientRect().height === 0).map((c) => c.className || c.localName);
      return {
        error: root.querySelector('.tg-error')?.textContent || (root.querySelector('.tg-empty') ? 'пустой пост' : ''),
        overflow: bubble.scrollWidth - bubble.clientWidth,
        width: Math.round(box.width),
        blocks: body.children.length,
        empty,
      };
    }, [post, theme === 'dark']);
    const shot = `${name.replace(/\.\w+$/, '')}-${theme}.png`;
    await page.locator('#root').screenshot({ path: `${OUT}/${shot}` });
    check(`${name} (${theme}) renders inside the bubble`,
      !layout.error && layout.blocks > 0 && layout.overflow <= 1 && layout.width <= 470 && layout.empty.length === 0,
      JSON.stringify(layout));
  }
}

check('No page errors while rendering the examples', errors.length === 0, errors.join(' | '));
await browser.close();
const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed, screenshots in test-results/preview/`);
process.exit(failed ? 1 : 0);
