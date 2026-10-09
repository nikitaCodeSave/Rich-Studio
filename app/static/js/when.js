// Picking a publication time: quick choices, the time zone in sight, and what is already queued.
import { h } from './dom.js';
import { chatLabel, S } from './state.js';

const pad = (n) => String(n).padStart(2, '0');
/** value for <input type=datetime-local> in the browser's time zone */
export const localValue = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };

/** «Москва, UTC+3»: the zone the times in the dialog are in */
export function zoneLabel() {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
  const off = -new Date().getTimezoneOffset();
  const utc = 'UTC' + (off >= 0 ? '+' : '−') + Math.floor(Math.abs(off) / 60) + (Math.abs(off) % 60 ? ':' + pad(Math.abs(off) % 60) : '');
  const city = zone.replace(/^.*\//, '').replace(/_/g, ' ');
  return city && !/^(UTC|GMT)/.test(city) ? city + ', ' + utc : utc;
}

/** Quick choices from now: the ones already past today are left out. */
export function presets(now = new Date()) {
  const at = (days, hh, mm = 0) => { const d = new Date(now); d.setDate(d.getDate() + days); d.setHours(hh, mm, 0, 0); return d.getTime(); };
  const hour = new Date(now.getTime() + 3600e3); hour.setSeconds(0, 0);
  const monday = (8 - now.getDay()) % 7 || 7;
  return [
    { label: 'Через час', ms: hour.getTime() },
    { label: 'Сегодня в 19:00', ms: at(0, 19) },
    { label: 'Завтра в 9:00', ms: at(1, 9) },
    { label: 'Завтра в 19:00', ms: at(1, 19) },
    { label: 'В понедельник в 9:00', ms: at(monday, 9) },
  ].filter((p) => p.ms > now.getTime() + 60e3);
}

const fmt = (ms) => new Date(ms).toLocaleString('ru-RU', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

/**
 * Date and time field with quick choices, the time zone, and the posts already queued for the next week.
 * @param {number} initialMs
 * @param {string} [skipId] a post that is being moved, left out of the queue
 */
export function whenPicker(initialMs, skipId) {
  const inp = /** @type {HTMLInputElement} */ (h('input', { type: 'datetime-local', value: localValue(initialMs), 'aria-label': 'Дата и время' }));
  const human = h('span', { class: 'muted small' });
  const show = () => { const ms = new Date(inp.value).getTime(); human.textContent = ms ? fmt(ms) + (ms < Date.now() ? ' — уже прошло' : '') : ''; };
  inp.addEventListener('input', show);
  const quick = h('div', { class: 'row presets' }, presets().map((p) => h('button', { type: 'button', class: 'ghost small-btn', text: p.label, onclick: () => { inp.value = localValue(p.ms); show(); } })));
  show();
  const week = Date.now() + 7 * 86400e3;
  const queued = S.posts.filter((p) => p.status === 'scheduled' && p.scheduled_at && p.scheduled_at * 1000 < week && p.id !== skipId)
    .sort((a, b) => (a.scheduled_at || 0) - (b.scheduled_at || 0));
  const el = h('div', { class: 'when' }, quick, h('div', { class: 'row' }, inp, human),
    h('p', { class: 'muted small', text: 'Время по часовому поясу этого браузера: ' + zoneLabel() + '.' }),
    queued.length
      ? h('details', { class: 'queue', open: queued.length <= 5 }, h('summary', { class: 'small', text: 'Уже в очереди на неделю: ' + queued.length }),
        h('ul', { class: 'list small' }, queued.slice(0, 20).map((p) => h('li', {}, h('span', { text: fmt((p.scheduled_at || 0) * 1000) }), h('span', { class: 'meta', text: (chatLabel(p.chat_id) || p.chat_id) + ' · ' + (p.title || p.kind) })))))
      : h('p', { class: 'muted small', text: 'На ближайшую неделю в очереди ничего нет.' }));
  return { el, input: inp, value: () => Math.floor(new Date(inp.value).getTime() / 1000) };
}
