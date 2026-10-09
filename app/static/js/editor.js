// The post being edited: change tracking, build/load, modes, options, preview, validation.
import { api } from './api.js';
import { $, $$, clone, debounce, h, toast } from './dom.js';
import { saveDraft } from './drafts.js';
import { renderBlocksPane } from './forms.js';
import { recordHistory } from './history.js';
import { renderKeyboard } from './keyboard.js';
import { renderMedia } from './media.js';
import { chats, dark, dirty, S, saveState } from './state.js';

// ---------- change tracking ----------
export const refreshPreview = debounce(() => { renderPreview(); validate(); }, 250);
export const autosave = debounce(() => { if (S.draftId) saveDraft(true).catch((e) => toast('Автосохранение не удалось: ' + e.message, 'err')); }, 2500);
export async function flushSave() { if (dirty.value && S.draftId) await saveDraft(true); }
export function changed() { dirty.value = true; saveState.value = 'есть изменения'; refreshPreview(); autosave(); recordHistory(); }

export function buildPost() {
  const rm = {};
  if (S.format === 'blocks') rm.blocks = S.buf.blocks;
  else rm[S.format] = S.buf[S.format];
  if (S.media.length) rm.media = S.media;
  for (const [k, v] of Object.entries(S.flags)) if (v) rm[k] = v;
  const post = { rich_message: rm, options: {} };
  const kb = S.keyboard.filter((r) => r.length);
  if (kb.length) post.reply_markup = { inline_keyboard: kb };
  for (const [k, v] of Object.entries(S.options)) if (v !== false && v !== '' && v !== undefined) post.options[k] = v;
  if (S.extra.pin) post.pin = true;
  if (S.extra.comment.trim()) post.comment = { html: S.extra.comment };
  else if (S.extra.commentObj) post.comment = S.extra.commentObj;
  const cbs = callbacksFor(kb);
  if (Object.keys(cbs).length) post.callbacks = cbs;
  return post;
}

// answers only for callback buttons that are still on the keyboard
export function callbacksFor(kb) {
  const out = {};
  for (const row of kb) for (const b of row) {
    const a = b.callback_data !== undefined && S.extra.callbacks[b.callback_data];
    if (a && (typeof a === 'string' ? a : a.text)) out[b.callback_data] = a;
  }
  return out;
}

// Drafts keep the text of every mode, not only the one that is sent
export function draftPayload() {
  return { ...buildPost(), _studio: { format: S.format, buffers: { blocks: S.buf.blocks, html: S.buf.html, markdown: S.buf.markdown } } };
}

export function loadPost(post) {
  post = clone(post || {});
  const studio = post._studio;
  delete post._studio;
  const rm = post.rich_message || {};
  S.buf = { blocks: [], html: '', markdown: '' };
  S.format = rm.blocks ? 'blocks' : rm.markdown !== undefined ? 'markdown' : rm.html !== undefined ? 'html' : 'blocks';
  if (rm.blocks) S.buf.blocks = rm.blocks;
  if (rm.html !== undefined) S.buf.html = rm.html;
  if (rm.markdown !== undefined) S.buf.markdown = rm.markdown;
  S.media = rm.media || [];
  S.flags = {};
  for (const [k, v] of Object.entries(rm)) if (!['blocks', 'html', 'markdown', 'media'].includes(k)) S.flags[k] = v;
  S.keyboard = (post.reply_markup && post.reply_markup.inline_keyboard) || [];
  S.options = post.options || {};
  const c = post.comment;
  S.extra = { pin: !!post.pin, comment: c && typeof c.html === 'string' ? c.html : '', commentObj: c && typeof c.html !== 'string' ? c : null, callbacks: post.callbacks || {} };
  if (studio && studio.buffers) {
    const b = studio.buffers;
    if (Array.isArray(b.blocks) && !rm.blocks) S.buf.blocks = b.blocks;
    if (typeof b.html === 'string' && rm.html === undefined) S.buf.html = b.html;
    if (typeof b.markdown === 'string' && rm.markdown === undefined) S.buf.markdown = b.markdown;
  }
  setMode(S.format, true);
  renderAll();
}

export function renderAll() {
  renderBlocksPane();
  $('#src-html').value = S.buf.html;
  $('#src-markdown').value = S.buf.markdown;
  renderMedia();
  renderKeyboard();
  renderOptions();
  renderAfter();
  renderPreview();
  validate();
}

// ---------- modes ----------
export function setMode(mode, silent) {
  S.mode = mode;
  if (mode !== 'json') S.format = mode;
  $$('#mode-tabs button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  ['blocks', 'html', 'markdown', 'json'].forEach((m) => { $('#pane-' + m).hidden = m !== mode; });
  $('#panel-media').hidden = mode === 'blocks';
  if (mode === 'json') { $('#src-json').value = JSON.stringify(buildPost(), null, 2); $('#json-err').textContent = ''; }
  const hints = { blocks: 'отправляется как rich_message.blocks', html: 'отправляется как rich_message.html', markdown: 'отправляется как rich_message.markdown', json: 'активный формат: ' + S.format };
  $('#mode-hint').textContent = hints[mode];
  if (!silent) changed();
}

// ---------- after publishing: pin, first comment ----------
export function renderAfter() {
  const box = $('#after');
  box.replaceChildren(...[
    h('label', { class: 'row', style: 'margin:2px 0' }, h('input', { type: 'checkbox', checked: S.extra.pin, onchange: (e) => { S.extra.pin = e.target.checked; changed(); } }), 'Закрепить пост после публикации (без уведомления)'),
    h('p', { class: 'muted small' }, 'Первый комментарий (HTML, как в посте). Студия ответит им на копию поста в группе обсуждения, когда Telegram её туда перешлёт. Бот должен быть админом группы обсуждения.'),
    S.extra.commentObj ? h('p', { class: 'w small', text: 'Комментарий задан в JSON не как HTML; он сохранится как есть, пока это поле пустое.' }) : null,
    h('textarea', { class: 'code', id: 'comment-src', style: 'min-height:70px', value: S.extra.comment, placeholder: '<p>Ссылки из поста и обсуждение здесь 👇</p>', oninput: (e) => { S.extra.comment = e.target.value; changed(); } })].filter(Boolean));
}

export function updatesLabel() {
  const u = S.updates;
  if (!u) return 'неизвестно';
  return { ok: 'работает', off: 'выключен (TG_UPDATES=off)', starting: 'запускается', no_token: 'нет токена', conflict: 'конфликт: бот уже работает где-то ещё', error: 'ошибка' }[u.state] || u.state;
}

// ---------- options ----------
export function renderOptions() {
  const box = $('#opts');
  box.replaceChildren();
  const flag = (obj, key, label, title) => h('label', { class: 'row', title, style: 'margin:2px 0' }, h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => { if (e.target.checked) obj[key] = true; else delete obj[key]; changed(); } }), label);
  box.append(
    flag(S.options, 'disable_notification', 'Без звука', 'disable_notification'),
    flag(S.options, 'protect_content', 'Запретить пересылку и сохранение', 'protect_content'),
    flag(S.flags, 'skip_entity_detection', 'Не распознавать ссылки, @, #, телефоны автоматически', 'rich_message.skip_entity_detection'),
    flag(S.flags, 'is_rtl', 'Справа налево', 'rich_message.is_rtl'));
  const m = S.spec.methods.sendRichMessage;
  if (m) {
    const known = new Set(['chat_id', 'rich_message', 'reply_markup', 'disable_notification', 'protect_content']);
    const rest = m.fields.filter((f) => !known.has(f.name));
    const extra = {};
    for (const f of rest) if (S.options[f.name] !== undefined) extra[f.name] = S.options[f.name];
    const ta = h('textarea', { class: 'code', style: 'min-height:70px', value: Object.keys(extra).length ? JSON.stringify(extra, null, 1) : '' , placeholder: '{"message_effect_id": "..."}' });
    ta.addEventListener('input', () => {
      try {
        const v = ta.value.trim() ? JSON.parse(ta.value) : {};
        for (const f of rest) delete S.options[f.name];
        Object.assign(S.options, v); ta.style.borderColor = ''; changed();
      } catch { ta.style.borderColor = 'var(--err)'; }
    });
    box.append(h('p', { class: 'muted small' }, 'Другие параметры sendRichMessage (из спецификации): ', rest.map((f, i) => [i ? ', ' : '', h('code', { title: f.description, text: f.name })])), ta);
  }
}

// ---------- preview & validation ----------
export function renderPreview() {
  const p = $('#preview');
  p.classList.toggle('tg-dark', dark.value);
  const post = buildPost();
  p.replaceChildren(RichRender.render(post, { channelTitle: chats()[0] || S.config.default_chat || 'Канал' }));
  if (post.comment) {
    p.append(h('div', { class: 'tg-comment-label', text: 'Первый комментарий в обсуждении' }), RichRender.render({ rich_message: post.comment }, {}));
  }
}

export const validate = debounce(async () => {
  const box = $('#validation');
  try {
    const r = await api('POST', '/api/validate', buildPost());
    const st = r.stats || {};
    const statLine = h('span', { class: 'muted small', text: ` · ${st.chars ?? 0} символов` + (st.blocks ? ` · ${st.blocks} блоков` : '') + (st.media ? ` · ${st.media} медиа` : '') + ` · проверено по ${r.spec_version}` });
    box.replaceChildren(...[
      r.ok ? h('span', { class: 'ok', text: '✓ Пост проходит проверку' }) : h('b', { class: 'e', text: 'Ошибки' }), statLine,
      r.errors.length ? h('ul', {}, r.errors.map((e) => h('li', { class: 'e', text: e }))) : null,
      r.warnings.length ? h('ul', {}, r.warnings.map((e) => h('li', { class: 'w', text: e }))) : null].filter(Boolean));
  } catch (e) { box.replaceChildren(h('span', { class: 'e', text: 'Проверка недоступна: ' + e.message })); }
}, 500);
