// The post being edited: change tracking, build/load, modes, options, preview, validation.
import { api } from './api.js';
import { $, $$, clone, debounce, h, toast } from './dom.js';
import { channelTitle } from './channels.js';
import { setSource, showSource } from './code-editor.js';
import { saveDraft } from './drafts.js';
import { renderBlocksPane } from './forms.js';
import { recordHistory } from './history.js';
import { exportToHtml } from './html-export.js';
import { readIssue, reveal } from './issues.js';
import { renderKeyboard } from './keyboard.js';
import { plural } from './labels.js';
import { renderMedia } from './media.js';
import { channelOf, chats, dark, dirty, parsed, S, saveState, showParsed, target, topicHome } from './state.js';

// ---------- change tracking ----------
export const refreshPreview = debounce(() => { renderPreview(); validate(); }, 250);
// a new post becomes a draft on its first change, so a reload or a crashed tab never loses it
export const autosave = debounce(() => { if (dirty.value && !S.conflict) saveDraft(true).catch((e) => toast('Автосохранение не удалось: ' + e.message, 'err')); }, 2500);
export async function flushSave() { if (dirty.value && !S.conflict) await saveDraft(true); }
export function changed() { dirty.value = true; saveState.value = 'есть изменения'; refreshPreview(); autosave(); recordHistory(); if (S.mode === 'json') renderJson(); }

export function buildPost() {
  const rm = {};
  if (S.format === 'blocks') rm.blocks = S.buf.blocks;
  else rm[S.format] = S.buf[S.format];
  // InputRichMessage.media lists the files of tg:// links in html/markdown; blocks hold their files themselves
  if (S.media.length && S.format !== 'blocks') rm.media = S.media;
  // a flag goes as it came, false and "" too (AGENTS.md rule 1): a checkbox the person clears takes its flag out
  for (const [k, v] of Object.entries(S.flags)) if (v !== undefined) rm[k] = v;
  const post = { rich_message: rm, options: {} };
  const kb = S.keyboard.filter((r) => r.length);
  // the rest of reply_markup (a reply keyboard, its options) stays as it came; the form edits inline_keyboard
  if (kb.length || Object.keys(S.extra.markup).length) post.reply_markup = { ...S.extra.markup, ...(kb.length && { inline_keyboard: kb }) };
  for (const [k, v] of Object.entries(S.options)) if (v !== undefined) post.options[k] = v;
  if (S.extra.pin) post.pin = true;
  // the comment's other fields (skip_entity_detection, is_rtl, media…) stay with its HTML; with them it is not dropped
  // when its HTML is emptied: the check says what is missing
  const c = S.extra.comment;
  if (c.trim() || commentFields().length) post.comment = { ...S.extra.commentBase, html: c };
  else if (S.extra.commentObj !== null) post.comment = S.extra.commentObj;
  if (S.extra.poll) post.poll_after = S.extra.poll;
  // every answer as it came: the buttons of the text (a <tg-button>, a «Кнопки» block) have theirs here too. The editor
  // does not read the text for buttons: the check does (services/callbacks.buttons) and warns about an answer no button
  // has; removing a button of the keyboard in the form takes its answer out (keyboard.js)
  if (Object.keys(S.extra.callbacks).length) post.callbacks = S.extra.callbacks;
  // the fields the editor does not read go as they came, last: the check warns about them, as the API would
  return Object.assign(post, S.unknown);
}

/** The answers of the buttons under the post, as they are. «Кнопки» at a published post sends only these: the server
 * replaces only the answers of the keyboard the post had and keeps every other one (posts.set_buttons). */
export function callbacksFor(kb) {
  const out = {};
  for (const row of kb) for (const b of row) {
    const k = b.callback_data;
    if (typeof k === 'string' && Object.hasOwn(S.extra.callbacks, k)) out[k] = S.extra.callbacks[k];
  }
  return out;
}

/** The fields of the first comment besides its HTML: the form shows only the HTML, these go with it as they came. */
const commentFields = () => Object.keys(S.extra.commentBase).filter((k) => k !== 'html');

// Drafts keep the text of every mode, not only the one that is sent, and the JSON not applied yet;
// the rest of `_studio` is someone else's (an imported post's source and warnings) and goes back as it came
export function draftPayload() {
  // a visual post does not carry the media list of the HTML/Markdown tabs, so the draft keeps it with their text
  const media = S.format === 'blocks' && S.media.length ? { media: S.media } : {};
  return { ...buildPost(), _studio: { ...S.notes, format: S.format, buffers: { blocks: S.buf.blocks, html: S.buf.html, markdown: S.buf.markdown, ...media }, ...(S.json && { json: S.json }) } };
}

// the keys of `_studio` the editor writes itself; any other one is a note of the draft
const OWN_STUDIO = ['format', 'buffers', 'json'];

const isObject = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
/** The fields of the post loadPost reads into the form, each with the shape it reads; null is no value. Any other
 * field (one of models.Post this list does not know yet among them), and a value of these the form cannot read, stays
 * as it came in S.unknown and goes back with the post (AGENTS.md rule 1); the check says what is wrong with it, and
 * for a field of this list a note above the editor says its part of the form changes nothing (unreadNote). A field
 * shown in the form is read here, in loadPost and in buildPost. The comment takes any value (commentObj). */
const READ = {
  rich_message: isObject,
  reply_markup: (v) => isObject(v) && (v.inline_keyboard === undefined || (Array.isArray(v.inline_keyboard) && v.inline_keyboard.every((r) => Array.isArray(r) && r.every(isObject)))),
  options: isObject,
  pin: (v) => typeof v === 'boolean',
  comment: () => true,
  callbacks: isObject,
  poll_after: isObject,
};

/** @param {Json} post @param {boolean} [keepNotes] only the post changes (JSON applied, an earlier version):
 * the open draft keeps its notes */
/** @param {string} [topicChat] the chat the post's topic belongs to when it came from elsewhere (a published post's
 * own chat); by default where the topic leads now (topicHome) */
export function loadPost(post, keepNotes = false, topicChat = undefined) {
  post = clone(post || {});
  const studio = post._studio;
  delete post._studio;
  if (!keepNotes) setNotes(studio);
  // a field the form does not read (an agent's, a chat put into the post, a new field of models.Post) stays as it came
  // (AGENTS.md rule 1): the check warns about it or refuses it, and the JSON tab shows it
  S.unknown = {};
  for (const [k, v] of Object.entries(post)) {
    if (Object.hasOwn(READ, k) && (v === null || READ[k](v))) continue;
    S.unknown[k] = v;
    delete post[k];
  }
  const rm = post.rich_message || {};
  S.buf = { blocks: [], html: '', markdown: '' };
  S.format = rm.blocks ? 'blocks' : rm.markdown !== undefined ? 'markdown' : rm.html !== undefined ? 'html' : 'blocks';
  if (rm.blocks) S.buf.blocks = rm.blocks;
  if (rm.html !== undefined) S.buf.html = rm.html;
  if (rm.markdown !== undefined) S.buf.markdown = rm.markdown;
  S.media = rm.media || [];
  S.flags = {};
  for (const [k, v] of Object.entries(rm)) if (!['blocks', 'html', 'markdown', 'media'].includes(k)) S.flags[k] = v;
  const { inline_keyboard: kb, ...markup } = post.reply_markup || {};
  S.keyboard = kb || [];
  S.options = post.options || {};
  // the loaded post's topic belongs to its chat: in another one showTarget drops it
  S.topicChat = S.options.message_thread_id === undefined ? undefined : topicChat ?? topicHome();
  // a comment with its HTML as a string is edited in the form, its other fields kept; any other one stays as it came
  const c = post.comment;
  const html = isObject(c) && typeof c.html === 'string';
  S.extra = { pin: !!post.pin, comment: html ? c.html : '', commentBase: html ? c : {}, commentObj: c !== undefined && c !== null && !html ? c : null, callbacks: post.callbacks || {}, poll: post.poll_after || null, markup };
  if (studio && studio.buffers) {
    const b = studio.buffers;
    if (Array.isArray(b.blocks) && !rm.blocks) S.buf.blocks = b.blocks;
    if (typeof b.html === 'string' && rm.html === undefined) S.buf.html = b.html;
    if (typeof b.markdown === 'string' && rm.markdown === undefined) S.buf.markdown = b.markdown;
    if (Array.isArray(b.media) && !rm.media) S.media = b.media;
  }
  const j = studio && studio.json;
  S.json = j && typeof j.text === 'string' && typeof j.base === 'string' ? { text: j.text, base: j.base } : null;
  setMode(S.format, true);
  renderNotes(); // with the fields of this post the form cannot read
  renderAll();
}

export function renderAll() {
  renderBlocksPane();
  setSource(/** @type {HTMLTextAreaElement} */ ($('#src-html')), S.buf.html);
  setSource(/** @type {HTMLTextAreaElement} */ ($('#src-markdown')), S.buf.markdown);
  renderJson();
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
  $$('#mode-tabs button[data-mode]').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  ['blocks', 'html', 'markdown', 'json'].forEach((m) => { $('#pane-' + m).hidden = m !== mode; });
  $('#panel-media').hidden = mode === 'blocks';
  $('#blocks-tools').hidden = mode !== 'blocks';
  if (mode === 'json') { renderJson(); $('#json-err').textContent = ''; }
  if (mode !== 'blocks') showSource(mode);
  const hints = { blocks: 'отправляется как rich_message.blocks', html: 'отправляется как rich_message.html', markdown: 'отправляется как rich_message.markdown', json: 'активный формат: ' + S.format };
  $('#mode-hint').textContent = hints[mode];
  if (!silent) changed();
}

// ---------- the JSON tab: what is typed there stays with the draft until «Применить» or «Отбросить» ----------
// It is not the post: the preview, the check and the send take buildPost(), never this text.
/** The unapplied text if there is one, else the post as it is; the bar says the post changed after that text was
 * typed, so applying it would undo those changes. */
export function renderJson() {
  const post = buildPost();
  const text = S.json ? S.json.text : JSON.stringify(post, null, 2);
  const ta = /** @type {HTMLTextAreaElement} */ ($('#src-json'));
  setSource(ta, text);
  $('#json-stale').hidden = !S.json || S.json.base === JSON.stringify(post);
  $('#mode-tabs [data-mode=json]').classList.toggle('pending', !!S.json);
}

function applyJson() {
  try {
    const post = JSON.parse($('#src-json').value);
    // an array or a string has no fields: its items would become fields «0», «1»…
    if (!isObject(post)) throw new Error('пост — объект JSON {"rich_message": …}, а здесь ' + (Array.isArray(post) ? 'массив' : post === null ? 'null' : typeof post === 'string' ? 'строка' : typeof post === 'number' ? 'число' : String(post)));
    loadPost(post, true);
  } catch (e) { $('#json-err').textContent = e.message; return; }
  S.json = null;
  setMode('json', true); changed(); toast('JSON применён', 'ok');
}

export function bindJson() {
  const ta = /** @type {HTMLTextAreaElement} */ ($('#src-json'));
  ta.addEventListener('input', () => {
    const post = buildPost();
    // base: the post the text was typed over; typed back to the post as it is, there is nothing to apply
    S.json = ta.value === JSON.stringify(post, null, 2) ? null : { text: ta.value, base: S.json ? S.json.base : JSON.stringify(post) };
    changed();
  });
  $('#json-apply').onclick = applyJson;
  $('#json-mine').onclick = applyJson;
  $('#json-drop').onclick = () => { S.json = null; $('#json-err').textContent = ''; changed(); };
}

// ---------- the draft's notes: what an import (or anything else that made the draft) wants the person to know ----------
/** The notes from a draft's `_studio`: every key the editor does not write itself. */
export function setNotes(studio) {
  S.notes = {};
  if (studio && typeof studio === 'object' && !Array.isArray(studio)) {
    for (const [k, v] of Object.entries(studio)) if (!OWN_STUDIO.includes(k)) S.notes[k] = v;
  }
  renderNotes();
}

// `warnings` is a list of strings; a single string (or anything else) is one item, so nothing is hidden
function renderNotes() {
  const w = S.notes.warnings;
  const list = Array.isArray(w) ? w : w === undefined || w === null || w === '' ? [] : [w];
  /** @type {import('./components/rs-draft-notes.js').RsDraftNotes} */ ($('#draft-notes')).warnings =
    [...list.map((x) => (typeof x === 'string' ? x : JSON.stringify(x))), ...unreadNote()];
}

/** A field the form shows, held in a shape it cannot read (pin: "yes", options: [1]): it goes as it came and its part of
 * the form changes nothing, so the person is told where to change it. Not a note of the draft: never saved. */
function unreadNote() {
  const keys = Object.keys(S.unknown).filter((k) => Object.hasOwn(READ, k));
  if (!keys.length) return [];
  return [keys.length === 1
    ? `Поле ${keys[0]} задано в JSON в виде, который форма не читает: оно уйдёт как есть, и форма его не меняет. Правится во вкладке JSON.`
    : `Поля ${keys.join(', ')} заданы в JSON в виде, который форма не читает: они уйдут как есть, и форма их не меняет. Правятся во вкладке JSON.`];
}

// «В HTML» in the visual mode: the post as HTML source in its tab (html-export.js); the blocks stay in theirs
export function bindHtmlExport() {
  $('#to-html').onclick = () => exportToHtml(() => { setMode('html', true); renderAll(); changed(); });
}

// ---------- after publishing: pin, first comment, poll ----------
export function renderAfter() {
  const box = $('#after');
  box.replaceChildren(...[
    h('label', { class: 'row', style: 'margin:2px 0' }, h('input', { type: 'checkbox', checked: S.extra.pin, onchange: (e) => { S.extra.pin = e.target.checked; changed(); } }), 'Закрепить пост после публикации (без уведомления)'),
    h('p', { class: 'muted small' }, 'Первый комментарий (HTML, как в посте). Студия ответит им на копию поста в группе обсуждения, когда Telegram её туда перешлёт. Бот должен быть админом группы обсуждения.'),
    S.extra.commentObj !== null ? h('p', { class: 'w small', text: 'Комментарий задан в JSON не как HTML; он сохранится как есть, пока это поле пустое.' }) : null,
    h('textarea', { class: 'code', id: 'comment-src', style: 'min-height:70px', value: S.extra.comment, placeholder: '<p>Ссылки из поста и обсуждение здесь 👇</p>', oninput: (e) => { S.extra.comment = e.target.value; changed(); } }),
    commentFields().length ? h('p', { class: 'muted small', id: 'comment-fields' }, 'Ещё в комментарии: ', commentFields().map((k, i) => [i ? ', ' : '', h('code', { text: k })]),
      '. Они уходят вместе с HTML и правятся во вкладке JSON; без HTML комментарий с ними не пройдёт проверку. ',
      h('button', { type: 'button', class: 'ghost small-btn', text: 'Убрать эти поля', title: 'Оставить в комментарии только HTML', onclick: () => { S.extra.commentBase = {}; renderAfter(); changed(); } })) : null,
    pollAfterForm()].filter(Boolean));
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
  box.append(h('div', { id: 'opts-topic' }), h('div', { id: 'opts-rest' }));
  renderTopic();
  renderRest();
}

/** After a change of channel. The topic row follows the channel; the field of other parameters is rebuilt only when
 * the row comes or goes (message_thread_id moves between them) or the topic was dropped with the old channel (the field
 * may show it), so what is typed in it stays, unparsed text too. @param {boolean} dropped */
export function refreshTopic(dropped) {
  if (!$('#opts-topic')) { renderOptions(); return; }
  const had = !!$('#topic');
  if (renderTopic() !== had || dropped) renderRest();
}

function renderTopic() {
  const row = topicChoice();
  $('#opts-topic').replaceChildren(...(row ? [row] : []));
  return !!row;
}

function renderRest() {
  const box = $('#opts-rest');
  box.replaceChildren();
  const m = S.spec.methods.sendRichMessage;
  if (!m) return;
  // with the topic list message_thread_id is the list's: the field neither shows nor sets it, so the two never disagree
  const listed = !!$('#topic');
  const known = new Set(['chat_id', 'rich_message', 'reply_markup', 'disable_notification', 'protect_content', ...(listed ? ['message_thread_id'] : [])]);
  // what options may hold (PostOptions: chat_id, ephemeral_message_parameters… are the studio's); a stored one it
  // may not stays in the field, so the check's error about it can be fixed here
  const allowed = new Set(((S.spec.post && S.spec.post.types.PostOptions) || m).fields.map((f) => f.name));
  const rest = m.fields.filter((f) => !known.has(f.name) && (allowed.has(f.name) || S.options[f.name] !== undefined));
  const extra = {};
  for (const f of rest) if (S.options[f.name] !== undefined) extra[f.name] = S.options[f.name];
  const ta = h('textarea', { class: 'code', style: 'min-height:70px', value: Object.keys(extra).length ? JSON.stringify(extra, null, 1) : '' , placeholder: '{"message_effect_id": "..."}' });
  ta.addEventListener('input', () => {
    try {
      const v = ta.value.trim() ? JSON.parse(ta.value) : {};
      if (listed && v && typeof v === 'object') delete v.message_thread_id;
      const had = S.options.message_thread_id;
      for (const f of rest) delete S.options[f.name];
      Object.assign(S.options, v); ta.style.borderColor = '';
      // a topic typed here belongs to where it leads now, as one chosen in the list
      if (S.options.message_thread_id !== had) S.topicChat = S.options.message_thread_id === undefined ? undefined : topicHome();
      changed();
    } catch { ta.style.borderColor = 'var(--err)'; }
  });
  box.append(h('p', { class: 'muted small' }, 'Другие параметры sendRichMessage (из спецификации): ', rest.filter((f) => allowed.has(f.name)).map((f, i) => [i ? ', ' : '', h('code', { title: f.description, text: f.name })])), ta);
}

/** The forum topic of the post (message_thread_id): only where the topic leads (topicHome) is a forum with something to
 * choose, its known topics or the topic the post already has; without them the post goes to General. */
function topicChoice() {
  const home = topicHome();
  const one = channelOf(home);
  const tid = S.options.message_thread_id;
  const topics = (one && one.is_forum && one.topics) || [];
  if (!one || !one.is_forum || (!topics.length && tid === undefined)) return null;
  const sel = /** @type {HTMLSelectElement} */ (h('select', { id: 'topic', title: 'message_thread_id. Темы добавляются в окне «Каналы» (кнопка канала в шапке)', onchange: () => {
    if (sel.value) { S.options.message_thread_id = Number(sel.value); S.topicChat = home; } else delete S.options.message_thread_id;
    changed();
  } },
  h('option', { value: '', text: 'General' }),
  ...topics.map((t) => h('option', { value: String(t.id), text: t.name, selected: t.id === tid ? true : null })),
  tid !== undefined && !topics.some((t) => t.id === tid) ? h('option', { value: String(tid), text: '№' + tid, selected: true }) : null));
  return h('label', { class: 'row', style: 'margin:2px 0' }, 'Тема форума', sel);
}

// ---------- preview & validation ----------
export function renderPreview() {
  const p = /** @type {import('./components/rs-preview.js').RsPreview} */ ($('#preview'));
  const post = buildPost();
  const fresh = checkParse(post);
  p.dark = dark.value;
  p.channelTitle = channelTitle(chats()[0] || S.config.default_chat) || 'Канал';
  p.post = fresh && showParsed.value ? { ...post, rich_message: telegramsBlocks(fresh.blocks) } : post;
}

/** The blocks Telegram made of the post, to draw as they are: they went through its detection of TON addresses and
 * the like already, so an address it left as text must stay text in the preview too. */
function telegramsBlocks(blocks) {
  return { blocks, skip_entity_detection: true };
}

// ---------- check against Telegram's parser ----------
export const parseKey = (post) => JSON.stringify(post.rich_message);

/** Tell <rs-parse-check> whether the preview agrees with the blocks Telegram made of this post; returns them while the post is unchanged. */
function checkParse(post) {
  const bar = /** @type {import('./components/rs-parse-check.js').RsParseCheck} */ ($('#parse-check'));
  const got = parsed.value;
  const fresh = got && got.key === parseKey(post) ? got : null;
  bar.showing = !!fresh && showParsed.value;
  bar.convertible = !!fresh && S.format !== 'blocks' && fresh.input_blocks.length > 0;
  if (!got) bar.check = null;
  else if (!fresh) bar.check = { status: 'stale' };
  else {
    const mine = RichRender.outline(post);
    const theirs = RichRender.outline({ rich_message: telegramsBlocks(fresh.blocks) });
    bar.check = mine === theirs ? { status: 'same' } : { status: 'diff', ...firstDifference(mine, theirs) };
  }
  return fresh;
}

// both outlines cut around the first place they differ, so the author sees what Telegram did otherwise
function firstDifference(a, b) {
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  const from = Math.max(0, i - 30);
  const cut = (s) => (from ? '…' : '') + s.slice(from, i + 50) + (s.length > i + 50 ? '…' : '');
  return { mine: cut(a), theirs: cut(b) };
}

/** HTML or Markdown becomes visual blocks exactly as Telegram parsed it; the source stays in its own tab. */
export function parsedToBlocks() {
  const got = parsed.value;
  if (!got) return;
  S.buf.blocks = clone(got.input_blocks);
  showParsed.value = false;
  setMode('blocks', true);
  renderBlocksPane();
  // the new post is Telegram's own blocks, so the check stays valid for it
  parsed.value = { ...got, key: parseKey(buildPost()) };
  changed();
  toast('Пост переведён в визуальный режим так, как его разобрал Telegram. Исходный текст остался в своей вкладке.' +
    (JSON.stringify(got.input_blocks).includes('"media"') ? ' Медиа ссылаются на файлы, уже загруженные в Telegram.' : ''), 'ok');
}

export const validate = debounce(async () => {
  const box = $('#validation');
  const pill = $('#check-pill');
  try {
    // the chats it goes to: the check warns about what Telegram refuses in them (web_app in a channel…)
    const where = new URLSearchParams(target().chat_ids.map((c) => ['chat_ids', c])).toString();
    const r = await api('POST', '/api/validate' + (where ? '?' + where : ''), buildPost());
    const st = r.stats || {};
    const statLine = h('span', { class: 'muted small', text: ' · ' + plural(st.chars ?? 0, 'символ', 'символа', 'символов') + (st.blocks ? ' · ' + plural(st.blocks, 'блок', 'блока', 'блоков') : '') + (st.media ? ` · ${st.media} медиа` : '') + ` · проверено по ${r.spec_version}` });
    const item = (msg, cls) => {
      const it = readIssue(msg);
      return h('li', { class: cls }, it.block && S.mode === 'blocks'
        ? h('button', { type: 'button', class: 'link', title: 'Показать в редакторе', text: it.text, onclick: () => reveal(it.block, it.field) })
        : it.text);
    };
    box.replaceChildren(...[
      r.ok ? h('span', { class: 'ok', text: '✓ Пост проходит проверку' }) : h('b', { class: 'e', text: 'Ошибки' }), statLine,
      r.errors.length ? h('ul', {}, r.errors.map((e) => item(e, 'e'))) : null,
      r.warnings.length ? h('ul', {}, r.warnings.map((e) => item(e, 'w'))) : null].filter(Boolean));
    // always in sight next to the mode tabs: the length and whether the post passes
    const chars = plural(st.chars ?? 0, 'символ', 'символа', 'символов');
    pill.className = 'check-pill ' + (r.errors.length ? 'e' : r.warnings.length ? 'w' : 'ok');
    pill.textContent = r.errors.length ? '✕ ' + plural(r.errors.length, 'ошибка', 'ошибки', 'ошибок') + ' · ' + chars : (r.warnings.length ? '⚠ ' : '✓ ') + chars;
    pill.title = r.errors.length || r.warnings.length ? 'Показать замечания проверки' : 'Пост проходит проверку';
    pill.hidden = false;
  } catch (e) { box.replaceChildren(h('span', { class: 'e', text: 'Проверка недоступна: ' + e.message })); pill.hidden = true; }
}, 500);

/** The poll that goes to the same chat right after the post (post.poll_after, sendPoll fields as they are). */
function pollAfterForm() {
  const box = h('div', { class: 'poll-after' });
  const draw = () => {
    const P = S.extra.poll;
    const on = h('label', { class: 'row', style: 'margin:2px 0' }, h('input', { type: 'checkbox', id: 'poll-after-on', checked: !!P, onchange: (e) => {
      S.extra.poll = e.target.checked ? { question: '', options: [{ text: '' }, { text: '' }] } : null;
      draw(); changed();
    } }), 'Сразу после поста отправить опрос');
    if (!P) { box.replaceChildren(on); return; }
    // the options as they came: drawing changes nothing. A string option (sendPoll's older form, the studio sends it as
    // {text}) stays a string when edited; options that are not a list are edited only in the JSON tab
    /** @type {Json[] | null} */
    const list = Array.isArray(P.options) ? P.options : null;
    const opts = (list || []).map((o, i, all) => {
      const text = typeof o === 'string' ? o : isObject(o) ? o.text : o === null || o === undefined ? '' : String(o);
      const set = (v) => { if (typeof all[i] === 'string') all[i] = v; else if (isObject(all[i])) all[i].text = v; else all[i] = { text: v }; };
      return h('div', { class: 'row', style: 'margin:2px 0' },
        P.type === 'quiz' ? h('input', { type: 'radio', name: 'poll-after-correct', title: 'Правильный ответ', checked: (P.correct_option_ids || [])[0] === i, onchange: () => { P.correct_option_ids = [i]; changed(); } }) : null,
        h('input', { type: 'text', value: typeof text === 'string' ? text : '', placeholder: 'Вариант ' + (i + 1), 'aria-label': 'Вариант ' + (i + 1), oninput: (e) => { set(e.target.value); changed(); } }),
        all.length > 2 ? h('button', { type: 'button', class: 'ghost small-btn', text: '✕', title: 'Убрать вариант', onclick: () => { all.splice(i, 1); if (P.correct_option_ids) P.correct_option_ids = [0]; draw(); changed(); } }) : null);
    });
    const flag = (key, label, value) => h('label', { class: 'row', style: 'margin:2px 0' }, h('input', { type: 'checkbox', checked: value === undefined ? !!P[key] : P[key] === value, onchange: (e) => {
      if (value !== undefined) { if (e.target.checked) { P[key] = value; P.correct_option_ids = [0]; delete P.allows_multiple_answers; } else { delete P[key]; delete P.correct_option_ids; delete P.explanation; } }
      else if (e.target.checked) P[key] = true; else delete P[key];
      draw(); changed();
    } }), label);
    box.replaceChildren(on,
      h('input', { type: 'text', value: P.question || '', placeholder: 'Вопрос', 'aria-label': 'Вопрос опроса', maxlength: '300', oninput: (e) => { P.question = e.target.value; changed(); } }),
      ...opts,
      list ? null : h('p', { class: 'w small', text: 'Варианты (poll_after.options) заданы в JSON не списком: они сохранятся как есть, правятся во вкладке JSON.' }),
      list && list.length < 12 ? h('button', { type: 'button', class: 'ghost small-btn', text: '＋ вариант', onclick: () => { list.push({ text: '' }); draw(); changed(); } }) : null,
      flag('type', 'Викторина (один правильный ответ)', 'quiz'),
      P.type === 'quiz' ? h('input', { type: 'text', value: P.explanation || '', placeholder: 'Пояснение после ответа', oninput: (e) => { if (e.target.value) P.explanation = e.target.value; else delete P.explanation; changed(); } }) : flag('allows_multiple_answers', 'Несколько ответов'),
      h('p', { class: 'muted small', text: 'Уйдёт в тот же канал (и тему) отдельным сообщением, когда выйдет пост, в том числе отложенный. Остальные поля sendPoll задаются во вкладке JSON (poll_after).' }));
  };
  draw();
  return box;
}
