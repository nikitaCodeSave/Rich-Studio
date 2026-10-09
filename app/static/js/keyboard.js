// Inline keyboard under the post and answers to callback buttons.
import { $, h } from './dom.js';
import { changed, updatesLabel } from './editor.js';
import { objectFields } from './forms.js';
import { S } from './state.js';

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

// the bot gets this when the button is pressed; the person never sees or types it
const newCallbackKey = () => 'b' + Date.now().toString(36).slice(-4) + Math.random().toString(36).slice(2, 6);

/** @param {Json} btn @param {string} kind */
function setKind(btn, kind) {
  if (btn.callback_data !== undefined && kind !== 'callback') delete S.extra.callbacks[btn.callback_data];
  if (kind === 'other') { byHand.add(btn); return; }
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
  return h('div', { class: 'obj kb-btn' },
    h('div', { class: 'row', style: 'margin:0' }, h('span', { class: 'muted small', text: `Ряд ${ri + 1}, кнопка ${bi + 1}` }), h('span', { class: 'grow' }),
      h('button', { class: 'ghost small-btn', text: '✕', title: 'Убрать кнопку', onclick: () => { setKind(btn, 'url'); row.splice(bi, 1); if (!row.length) S.keyboard.splice(ri, 1); renderKeyboard(); changed(); } })),
    h('div', { class: 'fields' }, body), rest, kind === 'callback' || kind === 'other' ? callbackAnswer(btn) : null);
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

// what the bot answers when a callback button is pressed (handled by the studio's update loop)
export function callbackAnswer(btn) {
  const box = h('div', { class: 'cb-answer' });
  const draw = () => {
    box.replaceChildren();
    if (btn.callback_data === undefined) return;
    const key = btn.callback_data;
    const cur = S.extra.callbacks[key];
    const a = typeof cur === 'string' ? { text: cur } : (cur || {});
    const save = (patch) => { const v = { ...a, ...patch }; if (!v.text) delete S.extra.callbacks[key]; else S.extra.callbacks[key] = v.show_alert ? v : v.text; Object.assign(a, patch); changed(); };
    box.append(h('label', { class: 'small muted', text: 'Ответ на нажатие (до 200 символов, видит только нажавший)' }),
      h('div', { class: 'row', style: 'margin:2px 0' },
        h('input', { type: 'text', maxlength: '200', value: a.text || '', placeholder: 'Спасибо, голос учтён', oninput: (e) => save({ text: e.target.value }) }),
        h('label', { class: 'row small', style: 'margin:0;white-space:nowrap' }, h('input', { type: 'checkbox', checked: !!a.show_alert, onchange: (e) => save({ show_alert: e.target.checked || undefined }) }), 'окном')));
    if (S.updates && S.updates.state !== 'ok') box.append(h('p', { class: 'w small', text: 'Приём событий сейчас не работает (' + updatesLabel() + '): кнопка будет «крутиться» без ответа.' }));
  };
  let lastKey = btn.callback_data;
  draw();
  // the button form above may add, rename or remove callback_data: keep the answer attached to it
  setTimeout(() => { const card = box.parentElement; if (card) card.addEventListener('input', (e) => {
    if (box.contains(e.target) || btn.callback_data === lastKey) return;
    if (lastKey !== undefined && S.extra.callbacks[lastKey] !== undefined && btn.callback_data !== undefined) { S.extra.callbacks[btn.callback_data] = S.extra.callbacks[lastKey]; delete S.extra.callbacks[lastKey]; }
    lastKey = btn.callback_data; draw();
  }); });
  return box;
}
