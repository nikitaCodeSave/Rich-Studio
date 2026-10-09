// The studio's health in the header: <rs-health> shows GET /api/health, refreshed every minute while the tab is visible.
import { api } from './api.js';
import { openDialog } from './dialogs.js';
import { $, h } from './dom.js';

const EVERY = 60e3;
const MARK = { ok: '✓', warn: '!', fail: '✕' };
/** @type {HealthReport | null} */
let last = null;

async function refresh() {
  const el = /** @type {any} */ ($('#health'));
  try {
    last = await api('GET', '/api/health');
    el.report = last; el.down = false;
  } catch {
    el.down = true;
  }
}

function showDetails() {
  const r = last;
  if (!r) { openDialog([h('h3', { text: 'Состояние студии' }), h('p', { class: 'err', text: 'Студия не ответила на /api/health.' })]); return; }
  const order = { fail: 0, warn: 1, ok: 2 };
  const checks = [...r.checks].sort((a, b) => order[a.status] - order[b.status]);
  openDialog([
    h('h3', { text: r.status === 'ok' ? 'Студия в порядке' : 'Студия: ' + (r.status === 'fail' ? 'есть сбой' : 'требует внимания') }),
    h('p', { class: 'muted small', text: `Версия ${r.version}, ${r.bot_api}, проверено ${new Date(r.checked_at * 1000).toLocaleTimeString('ru-RU')}. Агент читает то же: /api/health, skill studio-health.` }),
    h('ul', { class: 'changes health' }, checks.map((c) => h('li', {},
      h('span', { class: c.status === 'ok' ? 'ok' : c.status === 'warn' ? 'w' : 'err', text: MARK[c.status] + ' ' }),
      h('b', { text: c.name }), ': ' + c.summary,
      c.action && c.status !== 'ok' ? h('div', { class: 'muted small', text: c.action }) : ''))),
  ]);
}

export function watchHealth() {
  const el = $('#health');
  el.addEventListener('details', () => refresh().then(showDetails));
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, EVERY);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}
