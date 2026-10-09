/* Rich Studio editor.
 * The visual editor has no hard-coded list of blocks: the palette, the
 * forms and the inline entity menu are generated from the Bot API spec
 * served by /api/spec, so new block types and fields appear after a spec
 * refresh. Russian labels (labels.js) are cosmetic; unknown names fall back
 * to the spec's own names and descriptions.
 * ES modules without a build step; render.js and snippets.js are classic
 * scripts loaded before and reached through window.RichRender / SNIPPETS.
 */
import { api, setKeyCookie } from './api.js';
import { bindCode, snippetBar } from './code.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, $$, act, debounce, h, store, toast } from './dom.js';
import { loadDrafts, newDraft, openDraft, saveDraft } from './drafts.js';
import { autosave, buildPost, changed, loadPost, renderPreview, setMode } from './editor.js';
import { addMediaEntry, loadUploads, upload } from './media.js';
import { showPoll } from './poll.js';
import { loadPosts, replacePost } from './posts.js';
import { showSpec, updateBadge } from './spec-dialog.js';
import { chat, S, target, targetLabel } from './state.js';

function bindActions() {
  $('#btn-save').onclick = () => act(() => saveDraft());
  $('#btn-new').onclick = () => { if (!S.dirty || confirm('Есть несохранённые изменения. Создать новый черновик?')) newDraft(); };
  $('#title').oninput = () => { S.dirty = true; autosave(); };
  $('#chat').oninput = debounce(() => { store.set('chat', $('#chat').value); renderPreview(); }, 300);
  addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); act(() => saveDraft()); } });
  addEventListener('beforeunload', (e) => { if (S.dirty) { if (S.draftId) saveDraft(true).catch(() => {}); e.preventDefault(); e.returnValue = ''; } });

  $('#btn-check').onclick = () => act(async () => {
    const r = await api('GET', '/api/check' + (chat() ? '?chat_id=' + encodeURIComponent(chat()) : ''));
    openDialog([h('h3', { text: 'Проверка бота' }),
      h('p', {}, `Бот @${r.bot.username} в «${r.chat.title || r.chat.username || r.chat.id}» (${r.chat.type}), статус: `, h('b', { text: r.status })),
      h('p', { class: r.can_post ? 'ok' : 'err', text: r.can_post ? '✓ Может публиковать посты' : '✕ Нет права публиковать. Сделайте бота админом с правом «Публикация сообщений».' }),
      h('pre', { class: 'code', text: JSON.stringify(r.rights, null, 1) })]);
  });
  $('#btn-me').onclick = () => act(() => api('POST', '/api/preview-send', { post: buildPost() }), (r) => 'Отправлено вам в личку (#' + r.message_id + ')');
  $('#btn-publish').onclick = async () => {
    const where = targetLabel();
    if (!where) { toast('Укажите канал (@username или id) в поле справа сверху', 'err'); return; }
    if (!confirm('Опубликовать пост в ' + where + '?')) return;
    await act(async () => { if (S.dirty || !S.draftId) await saveDraft(true); return api('POST', '/api/send', { post: buildPost(), ...target(), draft_id: S.draftId }); },
      (r) => {
        const bad = (r.results || []).filter((x) => !x.ok);
        return (r.results && r.results.length > 1 ? `Опубликовано в ${r.results.length - bad.length} из ${r.results.length}` : 'Опубликовано, message_id ' + r.message.message_id)
          + (bad.length ? '\nНе ушло: ' + bad.map((x) => x.chat_id + ' (' + x.error + ')').join('; ') : '')
          + (r.warnings.length ? '\nПредупреждения: ' + r.warnings.join('; ') : '');
      });
    loadPosts();
  };
  $('#btn-schedule').onclick = () => {
    const d = new Date(Date.now() + 3600e3 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const inp = h('input', { type: 'datetime-local', value: d });
    openDialog([h('h3', { text: 'Отложить публикацию' }), h('p', { class: 'muted small', text: 'Пост отправит сервис в указанное время, поэтому контейнер должен работать в этот момент.' }), inp],
      [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', text: 'Запланировать', onclick: async () => {
        const at = Math.floor(new Date(inp.value).getTime() / 1000);
        closeDialog();
        await act(async () => { if (S.dirty || !S.draftId) await saveDraft(true); return api('POST', '/api/schedule', { post: buildPost(), ...target(), draft_id: S.draftId, at }); }, 'Запланировано на ' + new Date(at * 1000).toLocaleString('ru-RU'));
        loadPosts();
      } })]);
  };
  $('#btn-replace').onclick = async () => {
    await loadPosts();
    const sent = S.posts.filter((p) => ['sent', 'scheduled'].includes(p.status));
    const mid = h('input', { type: 'number', placeholder: 'message_id' });
    openDialog([h('h3', { text: 'Заменить опубликованный пост' }),
      sent.length ? h('ul', { class: 'list' }, sent.slice(0, 15).map((p) => h('li', { onclick: () => { closeDialog(); replacePost(p); } }, h('span', { text: `${p.chat_id} #${p.message_id || '—'} · ${p.status}` }), h('span', { class: 'meta', text: new Date((p.sent_at || p.scheduled_at) * 1000).toLocaleString('ru-RU') })))) : h('p', { class: 'muted', text: 'Через студию ещё ничего не публиковалось.' }),
      h('p', { class: 'muted small', text: 'Или любой пост бота по message_id (видно в ссылке на пост: t.me/канал/ID):' }), mid],
      [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', text: 'Заменить по id', onclick: async () => {
        const id = parseInt(mid.value, 10); if (!id) return;
        closeDialog();
        await act(() => api('POST', '/api/edit', { post: buildPost(), chat_id: chat(), message_id: id }), 'Пост #' + id + ' обновлён');
      } })]);
  };

  $('#theme-toggle').onclick = () => { S.dark = !S.dark; store.set('dark', S.dark); renderPreview(); };
  $('#open-render').onclick = () => act(async () => {
    if (S.dirty || !S.draftId) await saveDraft(true);
    window.open('/render/draft/' + S.draftId + '?theme=' + (S.dark ? 'dark' : 'light'), '_blank', 'noopener');
  });

  $$('#side-tabs button').forEach((b) => b.onclick = () => {
    $$('#side-tabs button').forEach((x) => x.classList.toggle('on', x === b));
    $$('.side-body').forEach((p) => { p.hidden = p.dataset.pane !== b.dataset.tab; });
    if (b.dataset.tab === 'posts') loadPosts();
    if (b.dataset.tab === 'uploads') loadUploads();
  });
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
  $('#btn-poll').onclick = showPoll;
}

// ---------- boot ----------
async function boot() {
  try {
    [S.spec, S.config] = await Promise.all([api('GET', '/api/spec'), api('GET', '/api/config')]);
  } catch (e) { document.body.prepend(h('p', { class: 'err', style: 'padding:12px', text: 'Не удалось загрузить сервис: ' + e.message })); return; }
  S.dark = store.get('dark', S.dark);
  if (store.get('apiKey', '')) setKeyCookie(store.get('apiKey', ''));
  $('#chat').value = store.get('chat', '') || S.config.default_chat || '';
  $('#chat').placeholder = S.config.default_chat || '@канал или id';
  if (!S.config.token_set) toast('TG_BOT_TOKEN не задан: редактор и предпросмотр работают, отправка нет. Добавьте токен в .env и перезапустите.', 'err');
  updateBadge();
  snippetBar('html'); snippetBar('markdown'); bindCode('html'); bindCode('markdown');
  bindActions();
  await Promise.all([loadUploads().catch(() => {}), loadPosts().catch(() => {})]);
  const last = store.get('lastDraft', null);
  await loadDrafts();
  if (last && S.drafts.some((d) => d.id === last)) await openDraft(last); else newDraft();
}

boot();

boot();
