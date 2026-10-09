// Drafts list, open, save, new.
import { api } from './api.js';
import { showTarget } from './channels.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, h, store, toast } from './dom.js';
import { changed, draftPayload, flushSave, loadPost } from './editor.js';
import { followDraft } from './editing.js';
import { resetHistory } from './history.js';
import { chatLabel, chats, dirty, editing, S, saveState } from './state.js';

// the draft's own channels; one without them goes to the channel the editor used to remember
// (rs.chat, before drafts kept channels; dropped once a draft saves it) or to the default channel.
// Returns true when that old choice was taken, so the draft saves it.
function setChannels(ids) {
  const legacy = ids && ids.length ? '' : store.get('chat', '');
  /** @type {HTMLInputElement} */ ($('#chat')).value = ids && ids.length ? ids.join(', ') : legacy || S.config.default_chat || '';
  return !!legacy;
}

// ---------- drafts ----------
export async function loadDrafts() {
  S.drafts = await api('GET', '/api/drafts');
  const ul = $('#drafts');
  ul.replaceChildren(...S.drafts.map((d) => h('li', { class: d.id === S.draftId ? 'on' : '', onclick: () => openDraft(d.id) },
    h('span', { text: d.title || 'Без названия' }),
    // where it goes, so drafts for different channels never mix up
    h('span', { class: 'meta chan', text: '→ ' + (d.chat_ids && d.chat_ids.length ? d.chat_ids.map(chatLabel).join(', ') : (S.config.default_chat ? chatLabel(S.config.default_chat) + ' (по умолчанию)' : 'канал не выбран')) }),
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
  const migrated = setChannels(d.chat_ids);
  await followDraft(d);
  loadPost(d.payload);
  showTarget();
  resetHistory();
  dirty.value = false; saveState.value = 'сохранено';
  if (migrated) changed(); // the old remembered channel moves into the draft
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
  const body = { title: $('#title').value.trim() || 'Пост ' + new Date().toLocaleDateString('ru-RU'), post: draftPayload(), post_id: editing.value ? editing.value.id : null, chat_ids: chats() };
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
  if (body.chat_ids.length) store.del('chat'); // the channel lives in the draft now
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
  setChannels(null);
  loadPost({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Заголовок' }, { type: 'paragraph', text: 'Текст поста' }] } });
  showTarget();
  resetHistory();
  saveState.value = '';
  loadDrafts();
}
