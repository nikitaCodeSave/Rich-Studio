// Editing a published or scheduled post: the draft is its working copy, <rs-edit-bar> says which post it is.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, act, h, toast } from './dom.js';
import { openDraft, saveDraft } from './drafts.js';
import { buildPost, changed, flushSave, loadPost } from './editor.js';
import { resetHistory } from './history.js';
import { loadPosts } from './posts.js';
import { chatLabel, dirty, editing, S, saveState } from './state.js';
import { whenPicker } from './when.js';

const when = (t) => new Date(t * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });

/** Open a post for editing: its working-copy draft if there is one, otherwise a new draft from the post. */
export async function editPost(p) {
  await flushSave();
  const own = S.drafts.find((d) => d.post_id === p.id);
  if (own) { await openDraft(own.id); return; }
  const full = await api('GET', '/api/posts/' + p.id);
  S.draftId = null; S.draftVersion = null;
  // the draft keeps the name the post was written under, so the list does not fill with «Правка поста #N»
  const origin = (p.draft_id && S.drafts.find((d) => d.id === p.draft_id)?.title) || p.title || '';
  $('#title').value = 'Правка: ' + (origin || 'пост ' + (p.message_id ? '#' + p.message_id : p.chat_id));
  editing.value = full;
  loadPost(full.payload);
  resetHistory();
  await saveDraft(true);
}

/** After a draft opens: is it the working copy of a post? */
export async function followDraft(d) {
  if (!d.post_id) { editing.value = null; return; }
  editing.value = await api('GET', '/api/posts/' + d.post_id).catch(() => null);
}

export function leaveEditing() {
  editing.value = null;
  dirty.value = true; saveState.value = 'есть изменения';
  saveDraft(true).catch((e) => toast(e.message, 'err'));
}

export async function updatePost() {
  const p = editing.value;
  if (!p) return;
  if (!confirm(`Обновить пост ${p.message_id ? '#' + p.message_id + ' ' : ''}в ${p.chat_id} содержимым редактора?`)) return;
  await act(async () => {
    const r = await api('POST', `/api/posts/${p.id}/edit`, buildPost());
    editing.value = r;
    return r;
  }, p.status === 'scheduled' ? 'Отложенный пост обновлён' : 'Пост обновлён в канале');
  loadPosts();
}

export function reschedulePost(p) {
  const base = p.status === 'scheduled' && p.scheduled_at ? p.scheduled_at * 1000 : Date.now() + 3600e3;
  const pick = whenPicker(base, p.id);
  openDialog([h('h3', { text: p.status === 'missed' ? 'Отложить заново' : 'Перенести публикацию' }),
    h('p', { class: 'small' }, 'Пост в ', h('b', { text: chatLabel(p.chat_id) || p.chat_id }), p.title ? ': ' + p.title : ''), pick.el],
    [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', class: 'primary', text: 'Перенести', onclick: async () => {
      const at = pick.value();
      if (!at || at * 1000 < Date.now()) { toast('Выберите время в будущем', 'err'); return; }
      closeDialog();
      await act(async () => {
        const r = await api('POST', `/api/posts/${p.id}/reschedule`, { at });
        if (editing.value && editing.value.id === p.id) editing.value = r;
        return r;
      }, 'Перенесено на ' + when(at));
      loadPosts();
    } })]);
}

/** Earlier versions of the post: what each one changed, a small preview, and «В редактор» for any of them. */
export async function showHistory(p) {
  const [now, versions] = await Promise.all([api('GET', '/api/posts/' + p.id), api('GET', `/api/posts/${p.id}/versions`)]);
  const load = (payload) => { closeDialog(); loadPost(payload); changed(); toast('Загружено в редактор. «Обновить пост» вернёт его в канал', 'ok'); };
  // newest first: each entry is compared with the one it replaced
  const all = [{ label: now.status === 'sent' ? 'Сейчас в канале' : 'Сейчас в очереди', meta: '', payload: now.payload },
    ...versions.map((v) => ({ label: 'До замены ' + when(v.replaced_at), meta: v.actor && v.actor !== 'ui' ? v.actor : '', payload: v.payload }))];
  const row = (e, i) => {
    const older = all[i + 1];
    const preview = h('div', { class: 'version-preview', hidden: true });
    const toggle = h('button', { class: 'ghost small-btn', text: 'Показать', onclick: () => {
      if (!preview.firstChild) { const pv = /** @type {any} */ (document.createElement('rs-preview')); pv.post = e.payload; preview.append(pv); }
      preview.hidden = !preview.hidden;
      toggle.textContent = preview.hidden ? 'Показать' : 'Скрыть';
    } });
    return h('li', { class: 'version' },
      h('div', { class: 'row' }, h('b', { text: e.label }), h('span', { class: 'meta', text: e.meta }), h('span', { class: 'grow' }), toggle,
        h('button', { class: 'ghost small-btn', text: 'В редактор', onclick: () => load(e.payload) })),
      older ? diffView(postLines(older.payload), postLines(e.payload), JSON.stringify(older.payload) !== JSON.stringify(e.payload))
        : h('p', { class: 'muted small', text: all.length > 1 ? 'Первая версия' : 'Пост ещё не заменяли.' }),
      preview);
  };
  openDialog([h('h3', { text: 'История поста' }), h('ul', { class: 'list history' }, all.map(row))]);
}

/** The post as lines a person reads: each block's text, then the buttons under it and the poll after it. */
export function postLines(payload) {
  const lines = [];
  try {
    const body = RichRender.render(payload, {}).querySelector('.tg-body');
    for (const el of body ? [...body.children] : []) {
      const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
      if (t) lines.push(t);
      else if (el.querySelector('img, video, audio, .tg-media')) lines.push('[медиа]');
    }
  } catch { /* a broken payload shows no lines */ }
  for (const r of payload?.reply_markup?.inline_keyboard || []) for (const b of r || []) lines.push('Кнопка «' + (b?.text || '') + '»' + (b?.url ? ' → ' + b.url : ''));
  if (payload?.poll_after) lines.push('Опрос после поста: ' + (payload.poll_after.question || ''));
  return lines;
}

/** Lines removed and added between two versions (longest common subsequence). */
export function lineDiff(a, b) {
  const L = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ op: '=', t: a[i] }); i++; j++; }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: '-', t: a[i++] });
    else out.push({ op: '+', t: b[j++] });
  }
  while (i < a.length) out.push({ op: '-', t: a[i++] });
  while (j < b.length) out.push({ op: '+', t: b[j++] });
  return out;
}

function diffView(older, newer, payloadChanged) {
  const d = lineDiff(older, newer);
  const changedLines = d.filter((x) => x.op !== '=');
  if (!changedLines.length) return h('p', { class: 'muted small', text: payloadChanged ? 'Текст тот же, изменились оформление или параметры' : 'Без изменений' });
  const same = d.length - changedLines.length;
  return h('div', { class: 'diff' }, changedLines.map((x) => h('div', { class: x.op === '+' ? 'add' : 'del', text: (x.op === '+' ? '+ ' : '− ') + x.t })),
    same ? h('div', { class: 'muted small', text: 'без изменений: ' + same }) : null);
}
