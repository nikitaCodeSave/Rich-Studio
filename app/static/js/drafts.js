// Drafts list, open, save, new.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, h, store, toast } from './dom.js';
import { draftPayload, flushSave, loadPost } from './editor.js';
import { followDraft } from './editing.js';
import { pushHistory, resetHistory } from './history.js';
import { dirty, editing, S, saveState } from './state.js';

// ---------- drafts ----------
export async function loadDrafts() {
  showDrafts(await api('GET', '/api/drafts'));
}

/** @param {DraftRow[]} list */
function showDrafts(list) {
  S.drafts = list;
  const ul = $('#drafts');
  ul.replaceChildren(...S.drafts.map((d) => h('li', { class: d.id === S.draftId ? 'on' : '', onclick: () => openDraft(d.id) },
    h('span', { text: d.title || 'Без названия' }),
    h('span', { class: 'meta' }, new Date(d.updated_at * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' }),
      h('a', { href: '/render/draft/' + d.id, target: '_blank', title: 'Рендер отдельной страницей', text: '↗', onclick: (e) => e.stopPropagation() }),
      h('a', { href: '#', title: 'Удалить черновик', text: '✕', onclick: async (e) => { e.preventDefault(); e.stopPropagation(); if (confirm('Удалить черновик «' + (d.title || 'Без названия') + '»?')) { await api('DELETE', '/api/drafts/' + d.id); if (S.draftId === d.id) newDraft(); loadDrafts(); } } })))));
  if (!S.drafts.length) ul.append(h('li', { class: 'muted small', text: 'Черновиков нет. Нажмите «Сохранить».' }));
}

export async function openDraft(id) {
  await flushSave();
  const d = await api('GET', '/api/drafts/' + id);
  await followDraft(d);
  showDraft(d, false);
}

/** Put the draft as the server has it into the editor; synchronous, so nothing typed in between is lost.
 * @param {Json} d @param {boolean} keepHistory an outside change becomes one more undo step */
function showDraft(d, keepHistory) {
  S.draftId = d.id; S.draftVersion = d.version;
  $('#title').value = d.title || '';
  loadPost(d.payload);
  if (keepHistory) pushHistory(); else resetHistory();
  dirty.value = false; saveState.value = 'сохранено';
  store.set('lastDraft', d.id);
  showAddress(d.id);
  loadDrafts();
}

// the address bar names the open draft: the link can be bookmarked or given by an agent (/?draft=<id>)
function showAddress(id) {
  const url = id ? '?draft=' + encodeURIComponent(id) : location.pathname;
  if (location.search !== (id ? url : '')) history.replaceState(null, '', url);
}

// ---------- the open draft changed elsewhere (an agent, another tab) ----------
let lastSeen = '';
/** Every few seconds while the tab is visible: a newer version of the open draft reloads it when nothing here
 * is unsaved, otherwise the conflict dialog asks; the list on the left follows new drafts by itself. */
export async function watchDrafts() {
  if (document.visibilityState !== 'visible' || S.conflict || pending) return;
  const list = await api('GET', '/api/drafts').catch(() => null);
  if (!list || pending) return;
  const key = JSON.stringify(list.map((d) => [d.id, d.version, d.title]));
  if (key !== lastSeen) { lastSeen = key; showDrafts(list); }
  const mine = S.draftId && list.find((d) => d.id === S.draftId);
  if (!mine || S.draftVersion === null || mine.version <= S.draftVersion) return;
  const current = await api('GET', '/api/drafts/' + mine.id).catch(() => null);
  if (!current || pending || S.draftId !== current.id) return;
  const conflict = () => draftConflict({ message: 'черновик изменили в другом месте (агент или другая вкладка), пока вы печатали', current });
  if (dirty.value) { conflict(); return; }
  await followDraft(current);
  // typed something while the requests were on the way: ask instead of overwriting it
  if (pending || S.draftId !== current.id) return;
  if (dirty.value) { conflict(); return; }
  showDraft(current, true);
  toast('Черновик изменили в другом месте (агент или другая вкладка): показана новая версия. Ctrl+Z вернёт прежнюю', 'ok');
}

// one save at a time: a second one sent before the first returns would carry a stale version and get 409
let saving = Promise.resolve();
let pending = 0; // saves in flight: their new version is ours, not someone else's
export function saveDraft(silent, overwrite) {
  pending++;
  const run = saving.then(() => doSave(silent, overwrite)).finally(() => { pending--; });
  saving = run.catch(() => {});
  return run;
}

async function doSave(silent, overwrite) {
  const body = { title: $('#title').value.trim() || 'Пост ' + new Date().toLocaleDateString('ru-RU'), post: draftPayload(), post_id: editing.value ? editing.value.id : null };
  let d;
  try {
    // the version this tab started from: if another tab or an agent saved since, the server answers 409
    d = S.draftId ? await api('PUT', '/api/drafts/' + S.draftId, { ...body, version: overwrite ? null : S.draftVersion }) : await api('POST', '/api/drafts', body);
  } catch (e) {
    if (e.status !== 409) throw e;
    draftConflict(e.detail);
    return null;
  }
  S.draftId = d.id; S.draftVersion = d.version; $('#title').value = d.title;
  dirty.value = false; saveState.value = 'сохранено ' + new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  store.set('lastDraft', d.id);
  showAddress(d.id);
  if (!silent) toast('Черновик сохранён', 'ok');
  loadDrafts();
  return d;
}

const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// the same draft was saved elsewhere (another tab, an agent) or deleted: the person decides, nothing is overwritten
function draftConflict(detail) {
  if (S.conflict) return;
  S.conflict = true;
  const theirs = detail && detail.current;
  const d = openDialog([h('h3', { text: 'Черновик изменился' }),
    h('p', { text: capitalize((detail && detail.message) || 'черновик изменили в другом месте') + '. Ваши правки в этой вкладке пока не сохранены.' })],
  [theirs ? h('button', { class: 'ghost', text: 'Открыть их версию', onclick: () => { closeDialog(d); openDraft(theirs.id); } }) : null,
    h('button', { text: theirs ? 'Сохранить мою поверх' : 'Сохранить мою как новый', onclick: () => {
      closeDialog(d);
      if (!theirs) S.draftId = null;
      saveDraft(false, true).catch((e) => toast(e.message, 'err'));
    } })]);
  d.addEventListener('close', () => { S.conflict = false; });
}

export async function newDraft() {
  await flushSave().catch(() => {});
  S.draftId = null; S.draftVersion = null; editing.value = null;
  $('#title').value = '';
  loadPost({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Заголовок' }, { type: 'paragraph', text: 'Текст поста' }] } });
  resetHistory();
  saveState.value = '';
  showAddress(null);
  loadDrafts();
}
