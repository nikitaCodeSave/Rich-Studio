// rich_message.media list and uploaded files.
import { api } from './api.js';
import { insertAtCursor } from './code.js';
import { $, h, toast } from './dom.js';
import { changed, setMode } from './editor.js';
import { renderBlocksPane } from './forms.js';
import { newBlock } from './spec.js';
import { S } from './state.js';

export function renderMedia() {
  const list = $('#media-list');
  list.replaceChildren();
  const kinds = ['photo', 'video', 'animation', 'audio', 'document', 'voice_note'];
  S.media.forEach((item, i) => {
    if (!item.media || typeof item.media !== 'object') item.media = { type: 'photo', media: '' };
    const src = h('input', { type: 'text', value: item.media.media || '', placeholder: 'URL, file_id или upload:имя', oninput: (e) => { item.media.media = e.target.value; changed(); } });
    const pick = h('select', { onchange: (e) => { if (e.target.value) { item.media.media = e.target.value; src.value = e.target.value; const u = S.uploads.find((x) => x.ref === e.target.value); if (u && kinds.includes(u.kind)) { item.media.type = u.kind; typeSel.value = u.kind; } changed(); } } },
      h('option', { value: '', text: '…файл' }), S.uploads.map((u) => h('option', { value: u.ref, text: u.name })));
    const typeSel = h('select', { onchange: (e) => { item.media.type = e.target.value; changed(); } }, kinds.map((k) => h('option', { value: k, text: k, selected: item.media.type === k ? true : null })));
    list.append(h('div', { class: 'media-row' },
      h('input', { type: 'text', value: item.id || '', placeholder: 'id', title: 'id из tg://photo?id=…', oninput: (e) => { item.id = e.target.value.trim(); changed(); } }),
      typeSel, h('div', { class: 'media-pick' }, src, pick),
      h('button', { class: 'ghost small-btn', text: '✕', onclick: () => { S.media.splice(i, 1); renderMedia(); changed(); } })));
  });
  if (!S.media.length) list.append(h('p', { class: 'muted small', text: 'Нет медиа. Вставьте фото через панель или кнопку «в пост» во вкладке «Файлы».' }));
}

export function nextMediaId(kind) {
  let i = 1;
  while (S.media.some((m) => m.id === kind + i)) i++;
  return kind + i;
}

export function addMediaEntry(kind, ref) {
  const id = nextMediaId(kind === 'voice_note' ? 'voice' : kind);
  S.media.push({ id, media: { type: kind, media: ref || '' } });
  renderMedia();
  return id;
}

// insert an uploaded file into the current post
export function insertUpload(u) {
  if (S.mode === 'json') setMode(S.format, true);
  if (S.format === 'blocks') {
    const b = newBlock(u.kind);
    if (b && b[u.kind]) b[u.kind].media = u.ref;
    S.buf.blocks.push(b);
    renderBlocksPane();
  } else {
    const id = addMediaEntry(u.kind, u.ref);
    const tag = u.kind === 'photo' ? `<img src="tg://photo?id=${id}">` : u.kind === 'document' ? `<tg-document src="tg://document?id=${id}"></tg-document>` : u.kind === 'audio' || u.kind === 'voice_note' ? `<audio src="tg://${u.kind === 'audio' ? 'audio' : 'voice_note'}?id=${id}"></audio>` : `<video src="tg://${u.kind}?id=${id}"></video>`;
    insertAtCursor($('#src-' + S.format), S.format === 'markdown' && u.kind === 'photo' ? `![](tg://photo?id=${id})` : tag);
  }
  changed();
  toast('Добавлено в пост: ' + u.name, 'ok');
}

// ---------- uploads ----------
export async function loadUploads() {
  S.uploads = await api('GET', '/api/media');
  const ul = $('#uploads');
  ul.replaceChildren(...S.uploads.map((u) => h('li', {},
    u.kind === 'photo' ? h('img', { class: 'thumb', src: u.url, alt: '' }) : h('span', { class: 'thumb', text: u.kind }),
    h('span', { class: 'name', title: u.name, text: u.name }),
    u.used ? null : h('span', { class: 'unused', title: 'Нет ни в одном черновике и ни в одном живом посте', text: 'не нужен' }),
    h('button', { class: 'ghost small-btn', text: 'в пост', onclick: () => insertUpload(u) }),
    h('button', { class: 'ghost small-btn', text: '✕', title: 'Удалить файл', onclick: () => deleteUpload(u.name) }))));
  if (!S.uploads.length) ul.append(h('li', { class: 'muted small', text: 'Файлов нет.' }));
  const unused = S.uploads.filter((u) => !u.used).length;
  const clean = $('#media-cleanup');
  clean.hidden = !unused;
  clean.textContent = `Удалить ненужные (${unused})`;
  clean.onclick = async () => {
    if (!confirm(`Удалить ${unused} файлов, которых нет ни в одном черновике и ни в одном живом посте?`)) return;
    try { const r = await api('POST', '/api/media/cleanup'); toast('Удалено файлов: ' + r.deleted.length, 'ok'); } catch (e) { toast(e.message, 'err'); }
    loadUploads();
  };
}

export async function upload(files) {
  for (const f of files) {
    const fd = new FormData(); fd.append('file', f);
    try { const u = await api('POST', '/api/media', fd); toast('Загружено: ' + u.name, 'ok'); } catch (e) { toast(e.message, 'err'); }
  }
  await loadUploads();
  if (S.mode === 'blocks') renderBlocksPane();
  renderMedia();
}

// a file a scheduled post or a draft still needs is deleted only after a second, explicit yes
async function deleteUpload(name) {
  if (!confirm('Удалить файл ' + name + '?')) return;
  const path = '/api/media/' + encodeURIComponent(name);
  try {
    await api('DELETE', path);
  } catch (e) {
    if (e.status !== 409) { toast(e.message, 'err'); return; }
    if (!confirm(e.message + '.\nБез него эти посты не отправятся. Всё равно удалить?')) return;
    await api('DELETE', path + '?force=true').catch((err) => toast(err.message, 'err'));
  }
  loadUploads();
}
