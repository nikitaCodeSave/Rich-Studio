/* Rich Studio editor.
 * The visual editor has no hard-coded list of blocks: the palette, the
 * forms and the inline entity menu are generated from the Bot API spec
 * served by /api/spec, so new block types and fields appear after a spec
 * refresh. Russian labels (labels.js) are cosmetic; unknown names fall back
 * to the spec's own names and descriptions.
 * ES modules without a build step; render.js and snippets.js are classic
 * scripts loaded before and reached through window.RichRender / SNIPPETS.
 */
import { api, migrateStoredKey } from './api.js';
import { bindCode, snippetBar } from './code.js';
import { bindTarget, loadChannels, showTarget } from './channels.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, $$, act, debounce, h, store, toast } from './dom.js';
import { effect } from '../vendor/signals-core.js';
import './components/rs-preview.js';
import './components/rs-edit-bar.js';
import './components/rs-health.js';
import './components/rs-parse-check.js';
import { editPost, leaveEditing, reschedulePost, showHistory, updatePost } from './editing.js';
import { loadDrafts, newDraft, openDraft, renderDrafts, saveDraft, watchDrafts } from './drafts.js';
import { autosave, buildPost, changed, loadPost, parsedToBlocks, parseKey, renderPreview, setMode } from './editor.js';
import { watchHealth } from './health.js';
import { canRedo, canUndo, redo, undo } from './history.js';
import { STATUS_LABELS } from './labels.js';
import { addMediaEntry, loadUploads, upload } from './media.js';
import { showPoll } from './poll.js';
import { loadPosts, replacePost } from './posts.js';
import { refreshSpec, showSpec, updateBadge } from './spec-dialog.js';
import { chat, chatLabel, chats, dark, dirty, editing, parsed, S, saveState, showParsed, target, targetLabel } from './state.js';
import { whenPicker } from './when.js';

const isTextField = (el) => el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName));

// what the page shows follows the signals in state.js and history.js
function bindEffects() {
  effect(() => { $('#save-state').textContent = saveState.value; });
  effect(() => { $('#btn-undo').disabled = !canUndo.value; $('#btn-redo').disabled = !canRedo.value; });
  effect(() => { store.set('dark', dark.value); parsed.value; showParsed.value; renderPreview(); });
  effect(() => { /** @type {any} */ ($('#edit-bar')).post = editing.value; });
  // «Кнопки» in «Посты» shows only for the post being edited
  let wasEditing = editing.peek()?.id;
  effect(() => { const id = editing.value?.id; if (id !== wasEditing) { wasEditing = id; loadPosts().catch(() => {}); } });
}

// «Опубликовать ▾»: Отложить, Опрос, Заменить пост; closes on a choice, a click elsewhere or Escape
function bindPublishMenu() {
  const menu = $('#publish-menu');
  const more = $('#btn-more');
  const show = (on) => { menu.hidden = !on; more.setAttribute('aria-expanded', String(on)); if (on) /** @type {HTMLElement} */ (menu.querySelector('button')).focus(); };
  more.onclick = (e) => { e.stopPropagation(); show(menu.hidden); };
  menu.addEventListener('click', () => show(false)); // the item's own handler runs first
  document.addEventListener('click', (e) => { if (!menu.hidden && !$('#publish-split').contains(/** @type {Node} */ (e.target))) show(false); });
  menu.addEventListener('keydown', (e) => {
    const items = [...menu.querySelectorAll('button')];
    const i = items.indexOf(/** @type {HTMLButtonElement} */ (document.activeElement));
    if (e.key === 'Escape') { show(false); more.focus(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus(); }
  });
}

// phone: «Редактор» and «Предпросмотр» are tabs (the layout is one column there)
function bindViewTabs() {
  const tabs = $('#view-tabs');
  tabs.onclick = (e) => {
    const b = /** @type {HTMLElement} */ (e.target).closest('button');
    if (!b) return;
    tabs.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    document.body.classList.toggle('view-preview', b.dataset.view === 'preview');
    if (b.dataset.view === 'preview') renderPreview();
    scrollTo(0, 0);
  };
}

function bindActions() {
  bindPublishMenu();
  bindViewTabs();
  $('#check-pill').onclick = () => $('#validation').scrollIntoView({ block: 'center', behavior: 'smooth' });
  $('#btn-save').onclick = () => act(() => saveDraft());
  $('#btn-undo').onclick = undo;
  $('#btn-redo').onclick = redo;
  $('#btn-new').onclick = () => { if (!dirty.value || confirm('Есть несохранённые изменения. Создать новый черновик?')) newDraft(); };
  $('#title').oninput = () => { dirty.value = true; autosave(); };
  // the channel belongs to the draft: changing it is a change of the draft
  // dirty at once: a publish right after the change must not go by the old saved channels; only the redraw waits
  const redrawTarget = debounce(() => { showTarget(); renderPreview(); }, 300);
  $('#chat').oninput = () => { dirty.value = true; saveState.value = 'есть изменения'; autosave(); redrawTarget(); };
  bindTarget();
  addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    const key = e.key.toLowerCase();
    if (key === 's') { e.preventDefault(); act(() => saveDraft()); return; }
    // text fields keep their own undo; the post-wide one works everywhere else
    if (isTextField(e.target)) return;
    if (key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); } else if ((key === 'z' && e.shiftKey) || key === 'y') { e.preventDefault(); redo(); }
  });
  addEventListener('beforeunload', (e) => { if (dirty.value) { if (S.draftId) saveDraft(true).catch(() => {}); e.preventDefault(); e.returnValue = ''; } });

  $('#btn-me').onclick = () => act(async () => {
    const post = buildPost();
    const r = await api('POST', '/api/preview-send', { post });
    // Telegram's answer holds the post as its parser understood it: the preview is checked against it
    if (r.parsed) parsed.value = { ...r.parsed, key: parseKey(post) };
    return r;
  }, (r) => 'Отправлено вам в личку (#' + r.message_id + ')');
  const check = $('#parse-check');
  check.addEventListener('toggle', () => { showParsed.value = !showParsed.value; });
  check.addEventListener('to-blocks', parsedToBlocks);
  $('#btn-publish').onclick = async () => {
    const where = targetLabel();
    if (!where) { toast('Укажите канал (@username или id) в поле справа сверху', 'err'); return; }
    const e = editing.value;
    if (!confirm((e ? `Это опубликует НОВЫЙ пост. Чтобы изменить пост ${e.message_id ? '#' + e.message_id : ''} в ${e.chat_id}, нажмите «Обновить пост» над редактором.\n\n` : '') + 'Опубликовать пост в ' + where + '?')) return;
    await act(async () => { if (dirty.value || !S.draftId) await saveDraft(true); return api('POST', '/api/send', { post: buildPost(), ...target(), draft_id: S.draftId, as_new: true }); },
      (r) => {
        const bad = (r.results || []).filter((x) => !x.ok);
        return (r.results && r.results.length > 1 ? `Опубликовано в ${r.results.length - bad.length} из ${r.results.length}` : 'Опубликовано, message_id ' + r.message.message_id)
          + (bad.length ? '\nНе ушло: ' + bad.map((x) => x.chat_id + ' (' + x.error + ')').join('; ') : '')
          + (r.warnings.length ? '\nПредупреждения: ' + r.warnings.join('; ') : '');
      });
    loadPosts();
  };
  $('#btn-schedule').onclick = () => {
    const pick = whenPicker(Date.now() + 3600e3);
    const e = editing.value;
    openDialog([h('h3', { text: 'Отложить публикацию' }),
      h('p', { class: 'small' }, 'Куда: ', h('b', { text: targetLabel() || 'канал не указан' })),
      ...(e ? [h('p', { class: 'err small', text: `Это отложит НОВЫЙ пост. Чтобы изменить пост ${e.message_id ? '#' + e.message_id : ''} в ${e.chat_id}, нажмите «Обновить пост» над редактором.` })] : []),
      pick.el,
      h('p', { class: 'muted small', text: 'Пост отправит сервис в указанное время, поэтому контейнер должен работать в этот момент.' })],
      [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', class: 'primary', text: 'Запланировать', onclick: async () => {
        const at = pick.value();
        if (!at || at * 1000 < Date.now()) { toast('Выберите время в будущем', 'err'); return; }
        closeDialog();
        await act(async () => { if (dirty.value || !S.draftId) await saveDraft(true); return api('POST', '/api/schedule', { post: buildPost(), ...target(), draft_id: S.draftId, at, as_new: true }); }, 'Запланировано на ' + new Date(at * 1000).toLocaleString('ru-RU'));
        loadPosts();
      } })]);
  };
  $('#btn-replace').onclick = async () => {
    await loadPosts();
    const sent = S.posts.filter((p) => ['sent', 'scheduled'].includes(p.status));
    const mid = h('input', { type: 'number', placeholder: 'message_id' });
    const where = chats();
    openDialog([h('h3', { text: 'Заменить опубликованный пост' }),
      sent.length ? h('ul', { class: 'list' }, sent.slice(0, 15).map((p) => h('li', { onclick: () => { closeDialog(); replacePost(p); } }, h('span', { text: `${chatLabel(p.chat_id)} #${p.message_id || '—'} · ${STATUS_LABELS[p.status] || p.status}` }), h('span', { class: 'meta', text: new Date((p.sent_at || p.scheduled_at || p.created_at) * 1000).toLocaleString('ru-RU') })))) : h('p', { class: 'muted', text: 'Через студию ещё ничего не публиковалось.' }),
      h('p', { class: 'muted small', text: where.length > 1
        ? 'Пост по message_id ищется в одном канале, а у черновика их ' + where.length + ': оставьте один в окне «Каналы».'
        : 'Или любой пост бота в ' + chatLabel(where[0] || S.config.default_chat || '') + ' по message_id (видно в ссылке на пост: t.me/канал/ID):' }), where.length > 1 ? null : mid],
      [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', text: 'Заменить по id', onclick: async () => {
        const id = parseInt(mid.value, 10); if (!id || where.length > 1) return;
        closeDialog();
        await act(() => api('POST', '/api/edit', { post: buildPost(), chat_id: where[0] || chat(), message_id: id }), 'Пост #' + id + ' обновлён');
      } })]);
  };

  const bar = $('#edit-bar');
  bar.addEventListener('update', () => updatePost());
  bar.addEventListener('reschedule', (e) => reschedulePost(/** @type {CustomEvent} */ (e).detail));
  bar.addEventListener('history', (e) => showHistory(/** @type {CustomEvent} */ (e).detail).catch((err) => toast(err.message, 'err')));
  bar.addEventListener('leave', () => leaveEditing());

  $('#theme-toggle').onclick = () => { dark.value = !dark.value; };
  $('#open-render').onclick = () => act(async () => {
    if (dirty.value || !S.draftId) await saveDraft(true);
    window.open('/render/draft/' + S.draftId + '?theme=' + (dark.value ? 'dark' : 'light'), '_blank', 'noopener');
  });

  $$('#side-tabs button').forEach((b) => b.onclick = () => {
    $$('#side-tabs button').forEach((x) => x.classList.toggle('on', x === b));
    $$('.side-body').forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.tab; });
    if (b.dataset.tab === 'posts') loadPosts();
    if (b.dataset.tab === 'uploads') loadUploads();
  });
  $('#posts-filter').onchange = () => loadPosts().catch((e) => toast(e.message, 'err'));
  $$('#mode-tabs button').forEach((b) => b.onclick = () => setMode(b.dataset.mode));
  $('#json-apply').onclick = () => {
    try { const v = JSON.parse($('#src-json').value); loadPost(v); setMode('json', true); changed(); toast('JSON применён', 'ok'); } catch (e) { $('#json-err').textContent = e.message; }
  };
  $('#media-add').onclick = () => { addMediaEntry('photo', ''); changed(); };
  $('#upload').onchange = (e) => upload([...e.target.files]);
  const side = $('.side');
  side.addEventListener('dragover', (e) => e.preventDefault());
  side.addEventListener('drop', (e) => { e.preventDefault(); if (e.dataTransfer.files.length) upload([...e.dataTransfer.files]); });
  $('#spec-badge').onclick = showSpec;
  $('#spec-refresh').onclick = async () => {
    const b = $('#spec-refresh');
    b.disabled = true; b.classList.add('spin');
    try { const r = await refreshSpec(); toast(r.text, r.ok ? 'ok' : 'err'); } catch (e) { toast(e.message, 'err'); } finally { b.disabled = false; b.classList.remove('spin'); }
  };
  $('#btn-poll').onclick = showPoll;
}

// ---------- boot ----------
async function boot() {
  try {
    await migrateStoredKey();
    [S.spec, S.config] = await Promise.all([api('GET', '/api/spec'), api('GET', '/api/config')]);
  } catch (e) { document.body.prepend(h('p', { class: 'err', style: 'padding:12px', text: 'Не удалось загрузить сервис: ' + e.message })); return; }
  dark.value = store.get('dark', dark.value);
  $('#chat').value = S.config.default_chat || '';
  $('#chat').placeholder = S.config.default_chat || '@канал или id';
  if (!S.config.token_set) toast('TG_BOT_TOKEN не задан: редактор и предпросмотр работают, отправка нет. Добавьте токен в .env и перезапустите.', 'err');
  updateBadge();
  snippetBar('html'); snippetBar('markdown'); bindCode('html'); bindCode('markdown');
  bindEffects();
  bindActions();
  document.addEventListener('rs-uploads-changed', () => loadUploads().catch(() => {}));
  document.addEventListener('rs-posts-changed', () => renderDrafts());
  watchHealth();
  await loadChannels().catch(() => {}); // the lists below name channels by title
  await Promise.all([loadUploads().catch(() => {}), loadPosts().catch(() => {})]);
  await loadDrafts();
  await openFromAddress();
  setInterval(() => { watchDrafts().catch(() => {}); }, 4000);
}

// /?draft=<id> opens that draft, /?post=<id> opens a published post for editing (links agents give);
// otherwise the draft open last time
async function openFromAddress() {
  const q = new URLSearchParams(location.search);
  const draft = q.get('draft');
  const post = q.get('post');
  if (draft && S.drafts.some((d) => d.id === draft)) { await openDraft(draft); return; }
  if (post) {
    const p = await api('GET', '/api/posts/' + encodeURIComponent(post)).catch(() => null);
    if (p) { await editPost(p).catch((e) => toast(e.message, 'err')); return; }
  }
  if (draft || post) toast((draft ? 'Черновика ' + draft : 'Поста ' + post) + ' нет: открыт последний черновик', 'err');
  const last = store.get('lastDraft', null);
  if (last && S.drafts.some((d) => d.id === last)) await openDraft(last); else newDraft();
}

boot();
