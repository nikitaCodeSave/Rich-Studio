// Spec version badge and the "what is new in the Bot API" dialog.
import { api } from './api.js';
import { openDialog } from './dialogs.js';
import { $, h, store } from './dom.js';
import { renderAll } from './editor.js';
import { S } from './state.js';

// ---------- spec dialog ----------
const when = (unix) => new Date(unix * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });

export function updateBadge() {
  const b = $('#spec-badge');
  b.textContent = S.spec.version;
  const last = (S.spec.changes[0] || {}).at || 0;
  const fresh = last > store.get('seenChanges', 0);
  const check = S.spec.last_check;
  b.classList.toggle('new', fresh);
  b.classList.toggle('warn', !fresh && (S.spec.gaps.length > 0 || !!(check && check.error)));
  b.title = 'Редактор построен по ' + S.spec.version + (S.spec.release_date ? ' (' + S.spec.release_date + ')' : '')
    + (fresh ? '. Есть новое в API!' : '')
    + (S.spec.gaps.length ? `. Редактору не хватает подписей или предпросмотра для ${S.spec.gaps.length} типов` : '')
    + (check ? '. Проверено ' + when(check.at) + (check.error ? ': не удалось скачать' : '') : '');
}

export function changesList(changes) {
  const kinds = { type_added: 'новый тип', field_added: 'новое поле', field_removed: 'удалено поле', subtype_added: 'новый вариант', method_added: 'новый метод', param_added: 'новый параметр' };
  return changes.map((c) => h('div', {}, h('b', { text: `${c.from || '?'} → ${c.to} · ${new Date(c.at * 1000).toLocaleDateString('ru-RU')}` }),
    h('ul', { class: 'changes' }, c.items.slice(0, 80).map((i) => h('li', {}, h('span', { class: 'muted', text: (kinds[i.kind] || i.kind) + ': ' }), h('code', { text: i.name }), i.description ? ' ' + i.description.slice(0, 160) : '')))));
}

// what labels.js and render.js do not know yet: the editor still works, it only looks rougher
export function gapsList(gaps) {
  if (!gaps.length) return [h('p', { class: 'ok small', text: '✓ Подписи и предпросмотр знают все блоки и элементы текста этой версии.' })];
  const part = { labels: 'нет русской подписи (в палитре видно имя из спецификации)', preview: 'нет отрисовки (в предпросмотре заглушка с JSON, в Telegram уйдёт как есть)' };
  return [h('p', { class: 'w small', text: 'Чего пока не хватает редактору. Пост с этими блоками всё равно отправится; поправить можно в коде или дождаться еженедельного PR.' }),
    h('ul', { class: 'changes' }, gaps.map((g) => h('li', {}, h('code', { text: g.name }), ` (${g.kind === 'block' ? 'блок' : 'элемент текста'} ${g.type}): `, h('span', { class: 'muted', text: part[g.part] || g.part }))))];
}

/** Download the newest spec now; the badge, the palette and the forms follow. Returns a one-line summary. */
export async function refreshSpec() {
  const r = await api('POST', '/api/spec/refresh');
  const before = S.spec.version;
  S.spec = await api('GET', '/api/spec');
  updateBadge();
  if (r.updated) renderAll();
  const gaps = S.spec.gaps.length ? ` Не хватает подписей или предпросмотра: ${S.spec.gaps.length}.` : '';
  if (r.error) return { ok: false, text: 'Не удалось скачать спецификацию: ' + r.error + '. Работаю по ' + S.spec.version + '.' };
  if (r.updated) return { ok: true, text: `Обновлено: ${before} → ${S.spec.version}, изменений: ${(r.changes || []).length}.` + gaps };
  return { ok: true, text: 'Уже последняя версия: ' + S.spec.version + '.' + gaps };
}

export function showSpec() {
  store.set('seenChanges', (S.spec.changes[0] || {}).at || 0);
  updateBadge();
  const status = h('p', { class: 'muted small' });
  const changesBox = h('div', {}, S.spec.changes.length ? changesList(S.spec.changes) : h('p', { class: 'muted', text: 'Изменений пока не зафиксировано: студия запоминает их при каждом обновлении спецификации.' }));
  const gapsBox = h('div', {}, gapsList(S.spec.gaps));
  const check = S.spec.last_check;
  openDialog([
    h('h3', { text: 'Спецификация Bot API' }),
    h('p', {}, 'Сейчас: ', h('b', { text: S.spec.version }), S.spec.release_date ? ' от ' + S.spec.release_date : '', ' · источник: ', { remote: 'скачана', cache: 'кэш', bundled: 'встроенная копия' }[S.spec.source] || S.spec.source,
      S.spec.loaded_at ? ', ' + new Date(S.spec.loaded_at * 1000).toLocaleString('ru-RU') : ''),
    check ? h('p', { class: check.error ? 'w small' : 'muted small', text: 'Последняя проверка: ' + when(check.at) + (check.error ? ', скачать не удалось: ' + check.error : ', успешно') }) : null,
    h('p', { class: 'muted small' }, 'Палитра блоков, формы, элементы текста и проверка строятся из этой спецификации. Студия проверяет обновления при запуске и каждые 12 часов, а кнопка ниже (или ↻ рядом с версией) проверяет сразу. ',
      S.spec.changelog ? h('a', { href: S.spec.changelog, target: '_blank', text: 'Changelog Bot API' }) : null),
    status, gapsBox, changesBox],
  [h('button', { class: 'ghost', value: 'ok', text: 'Закрыть' }), h('button', { type: 'button', text: 'Проверить обновления', onclick: async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    status.textContent = 'Скачиваю…';
    try {
      status.textContent = (await refreshSpec()).text;
      gapsBox.replaceChildren(...gapsList(S.spec.gaps));
      changesBox.replaceChildren(...(S.spec.changes.length ? changesList(S.spec.changes) : []));
    } catch (err) { status.textContent = err.message; } finally { btn.disabled = false; }
  } })]);
}
