// Drafts list, open, save, new.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, h, store, toast } from './dom.js';
import { draftPayload, flushSave, loadPost } from './editor.js';
import { followDraft } from './editing.js';
import { resetHistory } from './history.js';
import { dirty, editing, S, saveState } from './state.js';

// ---------- drafts ----------
export async function loadDrafts() {
  S.drafts = await api('GET', '/api/drafts');
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
  S.draftId = d.id; S.draftVersion = d.version;
  $('#title').value = d.title || '';
  await followDraft(d);
  loadPost(d.payload);
  resetHistory();
  dirty.value = false; saveState.value = 'сохранено';
  store.set('lastDraft', id);
  loadDrafts();
}

// one save at a time: a second one sent before the first returns would carry a stale version and get 409
let saving = Promise.resolve();
export function saveDraft(silent, overwrite) {
  const run = saving.then(() => doSave(silent, overwrite));
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
  loadDrafts();
}
