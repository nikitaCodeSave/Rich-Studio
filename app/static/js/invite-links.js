// Named invite links of a channel: one per place it is advertised, with how many came through each.
import { api } from './api.js';
import { openDialog } from './dialogs.js';
import { act, h, toast } from './dom.js';

const day = (s) => new Date(s * 1000).toLocaleString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });

/** @param {InviteLink} l */
function terms(l) {
  const t = [];
  if (l.kind === 'subscription') t.push(`подписка ⭐ ${l.subscription_price} в месяц`);
  if (l.creates_join_request) t.push('по заявке');
  if (l.member_limit) t.push('до ' + l.member_limit + ' человек');
  if (l.expire_date) t.push((l.expire_date * 1000 < Date.now() ? 'истекла ' : 'до ') + day(l.expire_date));
  return t.join(' · ');
}

/** @param {Channel} c */
export function showInviteLinks(c) {
  const box = h('div', { class: 'links' });
  const load = () => act(async () => draw(/** @type {InviteLink[]} */ (await api('GET', '/api/invite-links?chat_id=' + encodeURIComponent(c.chat_id)))));
  const copy = (l) => act(async () => { await navigator.clipboard.writeText(l.invite_link); }, 'Ссылка скопирована');
  const revoke = (l) => {
    if (!confirm(`Отозвать ссылку «${l.name || l.invite_link}»? По ней больше нельзя будет вступить.`)) return;
    act(async () => { await api('POST', '/api/invite-links/revoke', { invite_link: l.invite_link }); await load(); }, 'Ссылка отозвана');
  };
  /** @param {InviteLink[]} links */
  const draw = (links) => {
    box.replaceChildren(...links.map((l) => h('div', { class: 'invite-link' + (l.is_revoked ? ' revoked' : '') },
      h('div', { class: 'row', style: 'margin:0' },
        h('b', { text: l.name || 'Без названия' }),
        h('span', { class: 'small', text: `вступили: ${l.joined}` + (l.creates_join_request ? ` · заявок: ${l.requests}` : '') }),
        h('span', { class: 'grow' }),
        l.is_revoked ? h('span', { class: 'muted small', text: 'отозвана' }) : h('button', { class: 'ghost small-btn', text: 'Копировать', onclick: () => copy(l) }),
        l.is_revoked ? null : h('button', { class: 'ghost small-btn', text: 'Отозвать', onclick: () => revoke(l) })),
      h('div', { class: 'muted small' }, h('code', { text: l.invite_link }), terms(l) ? ' · ' + terms(l) : ''))));
    if (!links.length) box.append(h('p', { class: 'muted', text: 'Ссылок пока нет. Сделайте отдельную для каждой площадки, где будете рассказывать о канале.' }));
  };
  const name = /** @type {HTMLInputElement} */ (h('input', { type: 'text', maxlength: 32, placeholder: 'Название: где разместите (Хабр, YouTube…)', style: 'width:100%' }));
  const kind = /** @type {HTMLSelectElement} */ (h('select', { 'aria-label': 'Вид ссылки' },
    h('option', { value: 'invite', text: 'Обычная' }), h('option', { value: 'request', text: 'По заявке' }), h('option', { value: 'subscription', text: 'Платная подписка' })));
  const until = /** @type {HTMLInputElement} */ (h('input', { type: 'datetime-local', 'aria-label': 'Работает до' }));
  const limit = /** @type {HTMLInputElement} */ (h('input', { type: 'number', min: 1, max: 99999, placeholder: 'предел, чел.', 'aria-label': 'Предел вступлений', style: 'width:130px' }));
  const price = /** @type {HTMLInputElement} */ (h('input', { type: 'number', min: 1, max: 10000, placeholder: 'звёзд в месяц', 'aria-label': 'Цена подписки', style: 'width:140px' }));
  const limits = h('div', { class: 'row' }, h('label', { class: 'small', text: 'Работает до' }), until, limit);
  const paid = h('div', { class: 'row' }, price, h('span', { class: 'muted small', text: 'подписка на 30 дней, продлевается сама' }));
  // .row is display:flex, which beats the hidden attribute
  const show = (el, on) => { el.style.display = on ? '' : 'none'; };
  const showKind = () => { show(paid, kind.value === 'subscription'); show(limits, kind.value !== 'subscription'); show(limit, kind.value === 'invite'); };
  kind.onchange = showKind;
  showKind();
  const create = () => {
    const body = { chat_id: c.chat_id, name: name.value.trim() || null };
    if (kind.value === 'subscription') {
      if (!(Number(price.value) >= 1)) { toast('Цена подписки — от 1 звезды в месяц', 'err'); return; }
      body.subscription_price = Number(price.value);
    } else {
      if (until.value) body.expire_date = Math.floor(new Date(until.value).getTime() / 1000);
      if (kind.value === 'request') body.creates_join_request = true;
      else if (limit.value) body.member_limit = Number(limit.value);
    }
    act(async () => { await api('POST', '/api/invite-links', body); name.value = ''; await load(); }, 'Ссылка создана');
  };
  openDialog([h('h3', { text: 'Пригласительные ссылки · ' + (c.title || c.chat_id) }),
    h('p', { class: 'muted small', text: 'Своя ссылка на каждую площадку: видно, сколько людей пришло откуда. Вступления считает студия, пока она запущена. Бот должен быть админом с правом «Пригласительные ссылки».' }),
    box,
    h('h4', { text: 'Новая ссылка' }),
    name, h('div', { class: 'row' }, kind), limits, paid,
    h('div', { class: 'row' }, h('button', { text: 'Создать ссылку', onclick: create }))]);
  load();
}
