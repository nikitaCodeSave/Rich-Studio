// The studio's health in the header: <rs-health> shows GET /api/health, refreshed every minute while the tab is visible.
import { probe } from './api.js';
import { openDialog } from './dialogs.js';
import { $, h } from './dom.js';

const EVERY = 60e3;
const MARK = { ok: '✓', warn: '!', fail: '✕' };
/** @type {HealthReport | null} */
let last = null;
/** '' | 'down' | 'locked': why `last` is not current */
let state = '';

// a background poll never asks for the key: a prompt every minute after the key changed would be unbearable
async function refresh() {
  const el = /** @type {any} */ ($('#health'));
  try {
    const r = await probe('/api/health');
    state = r.status === 401 ? 'locked' : r.ok ? '' : 'down';
    last = state ? null : await r.json();
  } catch {
    state = 'down'; last = null;
  }
  el.report = last; el.state = state;
}

function showDetails() {
  const r = last;
  if (!r) {
    const why = state === 'locked' ? 'Студия просит ключ API. Его спросит любое действие в редакторе, например «Сохранить».' : 'Студия не ответила на /api/health.';
    openDialog([h('h3', { text: 'Состояние студии' }), h('p', { class: state === 'locked' ? 'w' : 'err', text: why })]);
    return;
  }
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
