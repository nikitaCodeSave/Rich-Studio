// Spec version badge and the "what is new in the Bot API" dialog.
import { api } from './api.js';
import { openDialog } from './dialogs.js';
import { $, h, store } from './dom.js';
import { renderAll } from './editor.js';
import { S } from './state.js';

// ---------- spec dialog ----------
export function updateBadge() {
  const b = $('#spec-badge');
  b.textContent = S.spec.version;
  const last = (S.spec.changes[0] || {}).at || 0;
  const fresh = last > store.get('seenChanges', 0);
  b.classList.toggle('new', fresh);
  b.title = 'Редактор построен по ' + S.spec.version + (S.spec.release_date ? ' (' + S.spec.release_date + ')' : '') + (fresh ? '. Есть новое в API!' : '');
}

export function changesList(changes) {
  const kinds = { type_added: 'новый тип', field_added: 'новое поле', field_removed: 'удалено поле', subtype_added: 'новый вариант', method_added: 'новый метод', param_added: 'новый параметр' };
  return changes.map((c) => h('div', {}, h('b', { text: `${c.from || '?'} → ${c.to} · ${new Date(c.at * 1000).toLocaleDateString('ru-RU')}` }),
    h('ul', { class: 'changes' }, c.items.slice(0, 80).map((i) => h('li', {}, h('span', { class: 'muted', text: (kinds[i.kind] || i.kind) + ': ' }), h('code', { text: i.name }), i.description ? ' ' + i.description.slice(0, 160) : '')))));
}

export function showSpec() {
  store.set('seenChanges', (S.spec.changes[0] || {}).at || 0);
  updateBadge();
  const status = h('p', { class: 'muted small' });
  const changesBox = h('div', {}, S.spec.changes.length ? changesList(S.spec.changes) : h('p', { class: 'muted', text: 'Изменений пока не зафиксировано: студия запоминает их при каждом обновлении спецификации.' }));
  openDialog([
    h('h3', { text: 'Спецификация Bot API' }),
    h('p', {}, 'Сейчас: ', h('b', { text: S.spec.version }), S.spec.release_date ? ' от ' + S.spec.release_date : '', ' · источник: ', { remote: 'скачана', cache: 'кэш', bundled: 'встроенная копия' }[S.spec.source] || S.spec.source,
      S.spec.loaded_at ? ', ' + new Date(S.spec.loaded_at * 1000).toLocaleString('ru-RU') : ''),
    h('p', { class: 'muted small' }, 'Палитра блоков, формы, элементы текста и проверка строятся из этой спецификации. Сервис проверяет обновления каждые 12 часов. ',
      S.spec.changelog ? h('a', { href: S.spec.changelog, target: '_blank', text: 'Changelog Bot API' }) : null),
    status, changesBox],
  [h('button', { class: 'ghost', value: 'ok', text: 'Закрыть' }), h('button', { type: 'button', text: 'Проверить обновления', onclick: async () => {
    status.textContent = 'Скачиваю…';
    try {
      const r = await api('POST', '/api/spec/refresh');
      status.textContent = r.error ? 'Не удалось скачать: ' + r.error : r.updated ? `Обновлено до ${r.version}: ${r.changes.length} изменений` : 'Уже последняя версия: ' + r.version;
      if (r.updated) { S.spec = await api('GET', '/api/spec'); updateBadge(); changesBox.replaceChildren(...changesList(S.spec.changes)); renderAll(); }
    } catch (e) { status.textContent = e.message; }
  } })]);
}
