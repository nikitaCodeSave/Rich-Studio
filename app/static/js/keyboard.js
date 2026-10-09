// Inline keyboard under the post and answers to callback buttons.
import { $, h } from './dom.js';
import { changed, updatesLabel } from './editor.js';
import { objectFields } from './forms.js';
import { S } from './state.js';

// ---------- keyboard under the post ----------
export function renderKeyboard() {
  const box = $('#kb');
  box.replaceChildren();
  S.keyboard.forEach((row, ri) => {
    const r = h('div', { class: 'kb-row' });
    row.forEach((btn, bi) => {
      r.append(h('div', { class: 'obj' },
        h('div', { class: 'row', style: 'margin:0' }, h('span', { class: 'muted small', text: `Ряд ${ri + 1}, кнопка ${bi + 1}` }), h('span', { class: 'grow' }),
          h('button', { class: 'ghost small-btn', text: '✕', onclick: () => { row.splice(bi, 1); if (!row.length) S.keyboard.splice(ri, 1); renderKeyboard(); changed(); } })),
        objectFields('InlineKeyboardButton', btn, 3), callbackAnswer(btn)));
    });
    r.append(h('button', { class: 'ghost small-btn', text: '＋ в ряд', onclick: () => { row.push({ text: 'Кнопка', url: 'https://' }); renderKeyboard(); changed(); } }));
    box.append(r);
  });
  box.append(h('button', { class: 'ghost small-btn', text: '＋ ряд кнопок', onclick: () => { S.keyboard.push([{ text: 'Кнопка', url: 'https://' }]); renderKeyboard(); changed(); } }));
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
