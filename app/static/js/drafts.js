// Drafts list, open, save, new.
import { api } from './api.js';
import { $, h, store, toast } from './dom.js';
import { draftPayload, flushSave, loadPost } from './editor.js';
import { S } from './state.js';

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
  S.draftId = d.id;
  $('#title').value = d.title || '';
  loadPost(d.payload);
  S.dirty = false; $('#save-state').textContent = 'сохранено';
  store.set('lastDraft', id);
  loadDrafts();
}

export async function saveDraft(silent) {
  const body = { title: $('#title').value.trim() || 'Пост ' + new Date().toLocaleDateString('ru-RU'), post: draftPayload() };
  const d = S.draftId ? await api('PUT', '/api/drafts/' + S.draftId, body) : await api('POST', '/api/drafts', body);
  S.draftId = d.id; $('#title').value = d.title;
  S.dirty = false; $('#save-state').textContent = 'сохранено ' + new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  store.set('lastDraft', d.id);
  if (!silent) toast('Черновик сохранён', 'ok');
  loadDrafts();
  return d;
}

export async function newDraft() {
  await flushSave().catch(() => {});
  S.draftId = null;
  $('#title').value = '';
  loadPost({ rich_message: { blocks: [{ type: 'heading', size: 1, text: 'Заголовок' }, { type: 'paragraph', text: 'Текст поста' }] } });
  $('#save-state').textContent = '';
  loadDrafts();
}
