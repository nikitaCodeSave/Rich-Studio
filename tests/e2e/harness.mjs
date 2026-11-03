// What every browser check gets: one editor page open on the studio, the fake Telegram's log, small helpers
// and check(). The checks share the page and run in file order, like one long session.
import { chromium } from 'playwright';

export const BASE = process.env.BASE || 'http://localhost:7424';
export const MOCK = process.env.MOCK || 'http://localhost:8081';

/** @typedef {Awaited<ReturnType<typeof start>>} Ctx */

// The checks publish posts. A working studio on the same port talks to the real Telegram and would post them to the
// channel, so the run starts only on a stand whose Bot API is the mock at `mock` (make e2e-local makes one).
/** @param {string} base @param {string} mock */
export async function assertMockStand(base, mock) {
  const cfg = await fetch(base + '/api/config').then((r) => (r.ok ? r.json() : null), () => null);
  const api = cfg && cfg.api_base ? new URL(cfg.api_base) : null;
  if (!api || !['127.0.0.1', 'localhost'].includes(api.hostname) || api.port !== new URL(mock).port) {
    throw new Error(`${base} is not a test stand: its Bot API is ${(cfg && cfg.api_base) || 'unknown'}, not the mock at ${mock}. Run make e2e-local.`);
  }
}

export async function start() {
  await assertMockStand(BASE, MOCK);
  const results = [];
  const check = (name, ok, info = '') => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${info ? '  ' + info : ''}`); };
  const mockLog = async () => (await fetch(MOCK + '/log')).json();
  const push = (u) => fetch(MOCK + '/push', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(u) }).then((r) => r.json());

  const browser = await chromium.launch(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {});
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
  const errors = [];
  const csp = [];
  let alerts = 0;
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('console', (m) => {
    const t = m.text();
    if (/Content Security Policy/i.test(t)) csp.push(t);
    else if (m.type() === 'error' && !/Failed to load resource|tile\.openstreetmap/.test(t)) errors.push(t);
  });
  page.on('dialog', (d) => { if (d.type() === 'alert') alerts++; d.accept(); });
  await page.goto(BASE);
  await page.waitForSelector('.block');

  const state = () => page.evaluate(() => JSON.parse(document.querySelector('#src-json').value));
  const showJson = () => page.click('#mode-tabs [data-mode=json]');
  // the draft's channels live in a hidden field; the header button and the «Каналы» dialog edit it
  const setChat = (v) => page.evaluate((x) => { const i = /** @type {HTMLInputElement} */ (document.querySelector('#chat')); i.value = x; i.dispatchEvent(new Event('input')); }, v);
  const applyJson = async (post) => { await showJson(); await page.fill('#src-json', JSON.stringify(post)); await page.click('#json-apply'); await page.waitForTimeout(400); };

  return { BASE, MOCK, browser, page, check, results, errors, csp, alerts: () => alerts, mockLog, push, state, showJson, setChat, applyJson };
}
