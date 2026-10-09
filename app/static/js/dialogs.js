// Modal dialogs.
import { $$, h } from './dom.js';

// ---------- dialogs ----------
export const dialogs = [];
export function openDialog(content, actions) {
  const d = h('dialog', { class: 'dlg' });
  const body = h('div', { class: 'dlg-body' }, h('div', {}, content),
    h('div', { class: 'dlg-actions' }, actions || [h('button', { value: 'ok', text: 'Закрыть' })]));
  // value=cancel/ok buttons close this dialog; Enter in a field never submits anything
  body.addEventListener('click', (e) => { const b = e.target.closest('button[value]'); if (b && body.contains(b)) closeDialog(d); });
  body.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') e.preventDefault(); });
  $$('button', body).forEach((b) => { if (!b.getAttribute('type')) b.setAttribute('type', 'button'); });
  d.append(body);
  d.addEventListener('close', () => { const i = dialogs.indexOf(d); if (i >= 0) dialogs.splice(i, 1); d.remove(); });
  document.body.append(d);
  dialogs.push(d);
  d.showModal();
  return d;
}
export function closeDialog(d) { d = d || dialogs[dialogs.length - 1]; if (d && d.open) d.close(); }

export function editJson(value, onSave) {
  const ta = h('textarea', { class: 'code', style: 'min-height:300px', value: JSON.stringify(value, null, 2) });
  const err = h('span', { class: 'err small' });
  openDialog([h('h3', { text: 'JSON' }), h('p', { class: 'muted small', text: 'Любые поля, включая те, которых нет в спецификации студии, уйдут в Telegram как есть.' }), ta, err],
    [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', text: 'Сохранить', onclick: () => { try { const v = JSON.parse(ta.value); closeDialog(); onSave(v); } catch (e) { err.textContent = e.message; } } })]);
}
