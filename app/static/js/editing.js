// Editing a published or scheduled post: the draft is its working copy, <rs-edit-bar> says which post it is.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, act, h, toast } from './dom.js';
import { openDraft, saveDraft } from './drafts.js';
import { buildPost, changed, flushSave, loadPost } from './editor.js';
import { resetHistory } from './history.js';
import { loadPosts } from './posts.js';
import { dirty, editing, S, saveState } from './state.js';

const when = (t) => new Date(t * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });

/** Open a post for editing: its working-copy draft if there is one, otherwise a new draft from the post. */
export async function editPost(p) {
  await flushSave();
  const own = S.drafts.find((d) => d.post_id === p.id);
  if (own) { await openDraft(own.id); return; }
  const full = await api('GET', '/api/posts/' + p.id);
  S.draftId = null; S.draftVersion = null;
  $('#title').value = 'Правка поста ' + (p.message_id ? '#' + p.message_id : p.chat_id);
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
  const inp = h('input', { type: 'datetime-local', value: new Date(base - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16) });
  openDialog([h('h3', { text: p.status === 'missed' ? 'Отложить заново' : 'Перенести публикацию' }), inp],
    [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', text: 'Перенести', onclick: async () => {
      const at = Math.floor(new Date(inp.value).getTime() / 1000);
      closeDialog();
      await act(async () => {
        const r = await api('POST', `/api/posts/${p.id}/reschedule`, { at });
        if (editing.value && editing.value.id === p.id) editing.value = r;
        return r;
      }, 'Перенесено на ' + when(at));
      loadPosts();
    } })]);
}

/** Earlier versions of the post; any of them (or what is in the channel now) can be loaded into the editor. */
export async function showHistory(p) {
  const [now, versions] = await Promise.all([api('GET', '/api/posts/' + p.id), api('GET', `/api/posts/${p.id}/versions`)]);
  const load = (payload) => { closeDialog(); loadPost(payload); changed(); toast('Загружено в редактор. «Обновить пост» вернёт его в канал', 'ok'); };
  const row = (label, meta, payload) => h('li', {}, h('span', { text: label }), h('span', { class: 'meta', text: meta }),
    h('button', { class: 'ghost small-btn', text: 'В редактор', onclick: () => load(payload) }));
  openDialog([h('h3', { text: 'История поста' }),
    h('ul', { class: 'list' },
      row(now.status === 'sent' ? 'Сейчас в канале' : 'Сейчас в очереди', '', now.payload),
      ...versions.map((v) => row('До замены ' + when(v.replaced_at), v.actor && v.actor !== 'ui' ? v.actor : '', v.payload))),
    versions.length ? null : h('p', { class: 'muted small', text: 'Пост ещё не заменяли.' })]);
}
