// Drafts list, open, save, new.
import { api } from './api.js';
import { showTarget } from './channels.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, h, store, toast } from './dom.js';
import { autosave, changed, draftPayload, flushSave, loadPost } from './editor.js';
import { followDraft } from './editing.js';
import { pushHistory, resetHistory } from './history.js';
import { chatLabel, chats, dirty, editing, kindOf, S, saveState } from './state.js';

// the draft's own channels; one without them goes to the channel the editor used to remember
// (rs.chat, before drafts kept channels; dropped once a draft saves it) or to the default channel.
// Returns true when that old choice was taken, so the draft saves it.
function setChannels(ids) {
  const legacy = ids && ids.length ? '' : store.get('chat', '');
  /** @type {HTMLInputElement} */ ($('#chat')).value = ids && ids.length ? ids.join(', ') : legacy || S.config.default_chat || '';
  return !!legacy;
}

// ---------- drafts ----------
/** Whether the draft already went out: «правка» for a post's working copy, else what its posts are now. */
function draftBadge(d) {
  if (d.post_id) return h('span', { class: 'badge', text: 'правка', title: 'Рабочая копия опубликованного поста' });
  const own = S.posts.filter((p) => p.draft_id === d.id && kindOf(p).editable);
  if (own.some((p) => p.status === 'sent')) return h('span', { class: 'badge ok', text: 'опубликован', title: 'Пост из этого черновика уже вышел' });
  if (own.some((p) => p.status === 'scheduled')) return h('span', { class: 'badge w', text: 'отложен', title: 'Пост из этого черновика ждёт своего времени' });
  return null;
}

export async function loadDrafts() {
  showDrafts(await api('GET', '/api/drafts'));
}

/** @param {DraftRow[]} list */
function showDrafts(list) {
  S.drafts = list;
  renderDrafts();
}

/** The list from S.drafts as it is; posts changing redraw it so «опубликован» follows them. */
export function renderDrafts() {
  const ul = $('#drafts');
  ul.replaceChildren(...S.drafts.map((d) => h('li', { class: d.id === S.draftId ? 'on' : '', onclick: () => openDraft(d.id) },
    h('span', {}, d.title || 'Без названия', draftBadge(d)),
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
  await followDraft(d);
  showDraft(d, false);
}

/** Put the draft as the server has it into the editor; synchronous, so nothing typed in between is lost.
 * @param {Json} d @param {boolean} keepHistory an outside change becomes one more undo step */
function showDraft(d, keepHistory) {
  setDraft(d.id, d.version);
  $('#title').value = d.title || '';
  const migrated = setChannels(d.chat_ids);
  loadPost(d.payload);
  showTarget();
  if (keepHistory) pushHistory(); else resetHistory();
  dirty.value = false; saveState.value = 'сохранено';
  if (migrated) changed(); // the old remembered channel moves into the draft
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
// the draft the editor shows, as far as the saves of it know: a new object each time the editor switches drafts,
// so a save of the draft left behind never touches the editor, and the saves still in line for it share its id and version
/** @type {{ id: string | null, version: number | null }} */
let shown = { id: null, version: null };

/** The editor switches to another draft (or to a new one, id null). */
export function setDraft(id, version) {
  shown = { id, version };
  S.draftId = id; S.draftVersion = version;
}

/** What a save sends, taken when it is asked for: what is typed while it waits in line or is on the way is the next save's. */
function snapshot() {
  return { title: $('#title').value, post: draftPayload(), post_id: editing.value ? editing.value.id : null, chat_ids: chats() };
}

export function saveDraft(silent, overwrite) {
  // the draft on screen may have been opened or reloaded since its last save: its id and version are the editor's
  shown.id = S.draftId; shown.version = S.draftVersion;
  const snap = { slot: shown, body: snapshot() };
  pending++;
  const run = saving.then(() => doSave(snap, silent, overwrite)).finally(() => { pending--; });
  saving = run.catch(() => {});
  return run;
}

async function doSave(snap, silent, overwrite) {
  const { slot } = snap;
  // still the draft on screen: what the editor knows (a conflict may have asked to save as new); otherwise
  // the left-behind draft as its earlier saves left it, so a second one neither POSTs again nor sends a stale version
  if (slot === shown) { slot.id = S.draftId; slot.version = S.draftVersion; }
  const { id, version } = slot;
  const body = { ...snap.body, title: snap.body.title.trim() || 'Пост ' + new Date().toLocaleDateString('ru-RU') };
  let d;
  try {
    // the version this tab started from: if another tab or an agent saved since, the server answers 409
    d = id ? await api('PUT', '/api/drafts/' + id, { ...body, version: overwrite ? null : version }) : await api('POST', '/api/drafts', body);
  } catch (e) {
    if (e.status !== 409) throw e;
    if (slot === shown) draftConflict(e.detail);
    else toast(`Черновик «${body.title}» не сохранён: его изменили в другом месте. Откройте его из списка`, 'err');
    return null;
  }
  slot.id = d.id; slot.version = d.version;
  // checked after the request: «Новый пост» or another draft may have been opened while it was on the way
  if (slot !== shown) { loadDrafts(); return d; }
  S.draftId = d.id; S.draftVersion = d.version;
  const sameTitle = $('#title').value === snap.body.title;
  if (sameTitle) $('#title').value = d.title;
  // only what was sent is saved: a change made while the request was on the way stays unsaved and saves next
  const rest = (b) => JSON.stringify({ ...b, title: '' });
  if (sameTitle && rest(snapshot()) === rest(snap.body)) {
    dirty.value = false; saveState.value = 'сохранено ' + new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  } else {
    dirty.value = true; saveState.value = 'есть изменения';
    autosave();
  }
  store.set('lastDraft', d.id);
  showAddress(d.id);
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
  // the draft left behind saves as it is now; the editor switches at once, so what is typed next is the new post's
  const left = dirty.value && !S.conflict ? saveDraft(true).catch(() => {}) : null;
  setDraft(null, null); editing.value = null;
  $('#title').value = '';
  setChannels(null);
  loadPost({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Заголовок' }, { type: 'paragraph', text: 'Текст поста' }] } });
  showTarget();
  resetHistory();
  dirty.value = false; saveState.value = '';
  showAddress(null);
  loadDrafts();
  await left;
}
