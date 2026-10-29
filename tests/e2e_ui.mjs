// Browser checks for the editor. Needs a running studio (BASE, default http://localhost:7424)
// pointed at the fake Bot API from tests/mock_telegram.py (MOCK, default http://localhost:8081),
// and Playwright: `npm i playwright && npx playwright install chromium`.
// Run: node tests/e2e_ui.mjs; only some checks: E2E_ONLY=poll node tests/e2e_ui.mjs (a part of the file name).
// Each check is a file in tests/e2e/ (NN-name.mjs, run in name order on one page): a new check is a new file,
// so parallel PRs never edit the same one.
import { mkdirSync, readdirSync } from 'node:fs';
import { start } from './e2e/harness.mjs';

const ctx = await start();
const { browser, check, results, errors, csp, BASE } = ctx;
const only = process.env.E2E_ONLY || '';
const dir = new URL('./e2e/', import.meta.url);
const files = readdirSync(dir).filter((f) => /^\d+-.+\.mjs$/.test(f) && f.includes(only)).sort();
for (const f of files) {
  try {
    await (await import(new URL(f, dir).href)).default(ctx);
  } catch (e) {
    check(`${f} finished`, false, String(e?.stack || e));
  }
}

check('No CSP violations or JS errors in the editor', errors.length === 0 && csp.length === 0, [...errors, ...csp].join(' | '));
// screenshots of the editor for a person to look at (CI keeps them as an artifact): a PR that changes the UI shows them
mkdirSync('test-results/ui', { recursive: true });
const shot = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await shot.goto(BASE);
await shot.waitForSelector('.block');
await shot.screenshot({ path: 'test-results/ui/editor-1400.png' });
await shot.setViewportSize({ width: 390, height: 844 });
await shot.screenshot({ path: 'test-results/ui/editor-390.png', fullPage: true });

await browser.close();
const failed = results.filter((x) => !x.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
