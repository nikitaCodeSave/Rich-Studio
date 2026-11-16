// Inline keyboard under the post and answers to callback buttons.
import { api } from './api.js';
import { refusalText, refusals } from './chat-rules.js';
import { $, h } from './dom.js';
import { buildPost, changed, updatesLabel } from './editor.js';
import { objectFields } from './forms.js';
import { openReplyDialog, replySummary } from './reply-dialog.js';
import { editing, S } from './state.js';

// ---------- keyboard under the post ----------
// what a button does: one of these fields (InlineKeyboardButton); a channel post mostly needs the first three
const ACTIONS = ['url', 'callback_data', 'copy_text', 'web_app', 'login_url', 'switch_inline_query', 'switch_inline_query_current_chat', 'switch_inline_query_chosen_chat', 'callback_game', 'pay'];
const KINDS = [['url', 'Ссылка'], ['callback', 'Ответ на нажатие'], ['copy', 'Скопировать текст'], ['other', 'Другое…']];
/** buttons the person switched to «Другое»: all fields by hand, whatever they hold now */
const byHand = new WeakSet();

/** @param {Json} btn */
function kindOf(btn) {
  if (byHand.has(btn)) return 'other';
  const acts = ACTIONS.filter((k) => btn[k] !== undefined);
  if (!acts.length || (acts.length === 1 && acts[0] === 'url')) return 'url';
  if (acts.length === 1 && acts[0] === 'callback_data') return 'callback';
  if (acts.length === 1 && acts[0] === 'copy_text') return 'copy';
  return 'other';
}

/** Whether a key of callbacks may still be pressed elsewhere than on this button: on another button of the keyboard,
 * or in the text of the post. Not the rule of which buttons answer (the server's check has it,
 * services/callbacks.buttons), only a cautious «maybe»: the key written anywhere in the text as it is, or as an
 * attribute of a <tg-button> in html or markdown with its entities decoded (data="a&amp;b"). A key kept by mistake
 * stays with its answer, and the check warns about an answer no button has. @param {string} key @param {Json} btn */
function usedElsewhere(key, btn) {
  if (S.keyboard.some((r) => r.some((b) => b !== btn && b.callback_data === key))) return true;
  const rm = buildPost().rich_message || {};
  if (JSON.stringify(rm).includes(JSON.stringify(String(key)).slice(1, -1))) return true;
  // an inert document: nothing in it runs or loads, it is only read (as paste.js reads the clipboard)
  return [rm.html, rm.markdown].some((src) => typeof src === 'string' && /tg-button/i.test(src)
    && [...new DOMParser().parseFromString(src, 'text/html').querySelectorAll('tg-button')].some((b) => [...b.attributes].some((a) => a.value === key)));
}

// the bot gets this when the button is pressed; the person never sees or types it
const newCallbackKey = () => 'b' + Date.now().toString(36).slice(-4) + Math.random().toString(36).slice(2, 6);

/** @param {Json} btn @param {string} kind */
function setKind(btn, kind) {
  // «Другое» keeps every field, the answer included
  if (kind === 'other') { byHand.add(btn); return; }
  // the answer belongs to the key: another button with the same callback_data keeps it
  if (btn.callback_data !== undefined && kind !== 'callback' && !usedElsewhere(btn.callback_data, btn)) delete S.extra.callbacks[btn.callback_data];
  byHand.delete(btn);
  if (kindOf(btn) === kind) return;
  ACTIONS.forEach((k) => { delete btn[k]; });
  if (kind === 'url') btn.url = '';
  if (kind === 'callback') btn.callback_data = newCallbackKey();
  if (kind === 'copy') btn.copy_text = { text: '' };
}

const field = (label, editor, title) => h('div', { class: 'field' }, h('label', { class: 'req', text: label, title: title || '' }), editor, h('span'));

function buttonCard(row, ri, bi) {
  const btn = row[bi];
  const kind = kindOf(btn);
  const text = h('input', { type: 'text', value: typeof btn.text === 'string' ? btn.text : '', placeholder: 'Подпись на кнопке', oninput: (e) => { btn.text = e.target.value; changed(); } });
  const type = h('select', { 'aria-label': 'Что делает кнопка', onchange: (e) => { setKind(btn, e.target.value); renderKeyboard(); changed(); } },
    KINDS.map(([v, name]) => h('option', { value: v, text: name, selected: v === kind ? true : null })));
  const set = (v) => { changed(); return v; };
  const body = [field('Текст', text), field('Тип', type)];
  if (kind === 'url') body.push(field('Ссылка', h('input', { type: 'url', value: btn.url || '', placeholder: 'https://… или t.me/…', oninput: (e) => { btn.url = set(e.target.value); } })));
  if (kind === 'copy') body.push(field('Что скопировать', h('input', { type: 'text', value: (btn.copy_text && btn.copy_text.text) || '', placeholder: 'Промокод, адрес, ссылка', oninput: (e) => { btn.copy_text = set({ text: e.target.value }); } })));
  // the remaining optional fields (style, emoji…) stay one «＋ поле…» away; «Другое» shows every field
  const rest = objectFields('InlineKeyboardButton', btn, 3, { exclude: kind === 'other' ? ['text'] : ['text', ...ACTIONS] });
  // what Telegram refuses on this button in the chats the post goes to (web_app outside a private chat…), with the way
  // out; redrawn after every edit in the card, as «＋ поле…» and ✕ change the button's fields without a new card
  const refused = h('div', { class: 'kb-refused' });
  const mark = () => refused.replaceChildren(...refusals('InlineKeyboardButton', btn).map((r) => h('p', { class: 'w small', 'data-field': r.field, text: refusalText(r) })));
  mark();
  const card = h('div', { class: 'obj kb-btn' },
    h('div', { class: 'row', style: 'margin:0' }, h('span', { class: 'muted small', text: `Ряд ${ri + 1}, кнопка ${bi + 1}` }), h('span', { class: 'grow' }),
      h('button', { class: 'ghost small-btn', text: '✕', title: 'Убрать кнопку', onclick: () => { setKind(btn, 'url'); row.splice(bi, 1); if (!row.length) S.keyboard.splice(ri, 1); renderKeyboard(); changed(); } })),
    h('div', { class: 'fields' }, body), rest, refused, kind === 'callback' || kind === 'other' ? callbackAnswer(btn) : null);
  for (const ev of ['change', 'click']) card.addEventListener(ev, mark);
  return card;
}

export function renderKeyboard() {
  const box = $('#kb');
  box.replaceChildren();
  S.keyboard.forEach((row, ri) => {
    const r = h('div', { class: 'kb-row' });
    row.forEach((_, bi) => r.append(buttonCard(row, ri, bi)));
    r.append(h('button', { class: 'ghost small-btn', text: '＋ в ряд', onclick: () => { row.push({ text: 'Кнопка', url: '' }); renderKeyboard(); changed(); } }));
    box.append(r);
  });
  box.append(h('button', { class: 'ghost small-btn', text: '＋ ряд кнопок', onclick: () => { S.keyboard.push([{ text: 'Кнопка', url: '' }]); renderKeyboard(); changed(); } }));
}

/** An answer that does something when the button is pressed: a text, a rich reply or a link. */
export const answers = (a) => (typeof a === 'string' ? !!a : !!(a && typeof a === 'object' && (a.text || a.reply || a.url)));

/** Store a button's answer: a bare text stays a string, as before; every other field (a rich reply, url, cache_time)
 * stays in the object; an answer with nothing in it goes. */
function setAnswer(key, v) {
  for (const k of Object.keys(v)) if (v[k] === undefined || v[k] === '') delete v[k];
  if (!answers(v)) delete S.extra.callbacks[key];
  else S.extra.callbacks[key] = Object.keys(v).length === 1 && v.text ? v.text : v;
}

/** The button the check names for this key: the first one with it, as the studio reads the keyboard. */
const labelOf = (key) => {
  const b = S.keyboard.flat().find((x) => x.callback_data === key && typeof x.text === 'string');
  return (b && b.text) || key;
};

/** What the studio's check says about this button's answer, without the «ответ на кнопку «…»:» in front. */
async function issuesFor(key) {
  const r = await api('POST', '/api/validate', buildPost());
  const at = `ответ на кнопку «${labelOf(key)}»: `;
  const mine = (list) => list.filter((m) => m.startsWith(at)).map((m) => m.slice(at.length));
  return { errors: mine(r.errors), warnings: mine(r.warnings) };
}

// what the bot answers when a callback button is pressed (handled by the studio's update loop)
export function callbackAnswer(btn) {
  const box = h('div', { class: 'cb-answer' });
  const draw = () => {
    box.replaceChildren();
    if (btn.callback_data === undefined) return;
    const key = btn.callback_data;
    const cur = S.extra.callbacks[key];
    /** @type {Json} */
    const a = typeof cur === 'string' ? { text: cur } : { ...(cur || {}) };
    const save = (patch) => { Object.assign(a, patch); setAnswer(key, { ...a }); changed(); };
    const reply = a.reply && typeof a.reply === 'object' ? a.reply : null;
    const instead = !!(reply && reply.ephemeral_message_parameters && reply.ephemeral_message_parameters.replace_callback_query_message);
    const edit = () => openReplyDialog({ label: labelOf(key), answer: a, issues: () => issuesFor(key),
      onChange: (next) => { setAnswer(key, { ...next }); changed(); draw(); } });
    box.append(h('label', { class: 'small muted', text: 'Всплывающий ответ (до 200 символов, видит только нажавший)' }),
      h('div', { class: 'row', style: 'margin:2px 0' },
        h('input', { type: 'text', maxlength: '200', value: a.text || '', placeholder: 'Спасибо, голос учтён', oninput: (e) => save({ text: e.target.value }) }),
        h('label', { class: 'row small', style: 'margin:0;white-space:nowrap' }, h('input', { type: 'checkbox', checked: !!a.show_alert, onchange: (e) => save({ show_alert: e.target.checked || undefined }) }), 'окном')),
      // Bot API 10.4: a whole rich message only the person who pressed sees (callbacks[key].reply)
      h('div', { class: 'row cb-reply', style: 'margin:2px 0' },
        h('span', { class: 'small grow' + (reply ? '' : ' muted'), text: reply ? `Rich-ответ${instead ? ' вместо поста' : ''}: ${replySummary(reply) || 'пустой'}` : 'Rich-ответ: нет' }),
        h('button', { type: 'button', class: 'ghost small-btn', text: reply ? 'Изменить…' : 'Rich-ответ…', title: 'Сообщение, которое увидит только нажавший (Bot API 10.4)', onclick: edit })));
    // a published post open for editing: how its presses went, next to the answer they got
    const p = editing.value;
    const clicks = p && p.clicks && p.clicks[key];
    const failed = p && p.reply_errors && p.reply_errors[key];
    if (clicks || failed) box.append(h('p', { class: 'small ' + (failed ? 'w' : 'muted'), text: `Нажатий: ${clicks || 0}` + (failed ? ` · rich-ответ не дошёл ${failed.n} раз, последний: ${failed.error}` : '') }));
    if (S.updates && S.updates.state !== 'ok') box.append(h('p', { class: 'w small', text: 'Приём событий сейчас не работает (' + updatesLabel() + '): кнопка будет «крутиться» без ответа.' }));
  };
  let lastKey = btn.callback_data;
  draw();
  // the button form above may add, rename or remove callback_data: keep the answer attached to it
  setTimeout(() => { const card = box.parentElement; if (card) card.addEventListener('input', (e) => {
    if (box.contains(e.target) || btn.callback_data === lastKey) return;
    // moved to the new key; the old one keeps it too while another button may still be pressed with it
    if (lastKey !== undefined && S.extra.callbacks[lastKey] !== undefined && btn.callback_data !== undefined) { S.extra.callbacks[btn.callback_data] = S.extra.callbacks[lastKey]; if (!usedElsewhere(lastKey, btn)) delete S.extra.callbacks[lastKey]; }
    lastKey = btn.callback_data; draw();
  }); });
  return box;
}
