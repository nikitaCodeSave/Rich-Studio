// Post templates: the list at the top of «Черновики» (<rs-templates>), the open post saved as a template, a new
// draft from a template's form (<rs-template-form> in a dialog). Filling and escaping happen on the server.
import { api } from './api.js';
import { closeDialog, openDialog } from './dialogs.js';
import { $, h, toast } from './dom.js';
import { openDraft } from './drafts.js';
import { buildPost } from './editor.js';
import { constField } from './spec.js';
import { S } from './state.js';
import './components/rs-templates.js';
import './components/rs-template-form.js';

const list = () => /** @type {any} */ ($('#templates'));

export async function loadTemplates() {
  list().templates = await api('GET', '/api/templates');
}

/** "name=value" pairs the spec fixes (a block's or an entity's type, an enum value): never a template field. */
function fixedValues() {
  const out = new Set();
  for (const [name, t] of Object.entries(S.spec.types)) {
    const cf = constField(name);
    if (cf && t.const) out.add(cf + '=' + t.const);
    for (const f of t.fields) for (const v of f.enum || []) out.add(f.name + '=' + v);
  }
  return out;
}

/** @param {string} mode @param {Json} template */
function form(mode, template) {
  const el = /** @type {any} */ (document.createElement('rs-template-form'));
  el.mode = mode;
  el.fixed = fixedValues();
  el.template = template;
  return el;
}

/** The open post as a new template, or an existing template's title and fields. @param {TemplateRow} [row] */
async function editTemplate(row) {
  const t = row ? await api('GET', '/api/templates/' + row.id) : { title: /** @type {HTMLInputElement} */ ($('#title')).value.trim(), payload: buildPost(), fields: [] };
  const f = form('edit', t);
  const d = openDialog([h('h3', { text: row ? 'Шаблон «' + t.title + '»' : 'Шаблон из текущего поста' }), f],
    [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', class: 'primary', text: 'Сохранить шаблон', onclick: async () => {
      try {
        const v = f.value;
        if (row) await api('PUT', '/api/templates/' + row.id, { ...v, version: t.version }); else await api('POST', '/api/templates', v);
        closeDialog(d);
        toast('Шаблон «' + v.title + '» сохранён', 'ok');
        loadTemplates().catch(() => {});
      } catch (e) { f.error = e.message; }
    } })]);
}

/** A form from the template's fields; the filled post opens as a new draft. @param {TemplateRow} row */
async function fillTemplate(row) {
  const t = await api('GET', '/api/templates/' + row.id);
  const f = form('fill', t);
  const d = openDialog([h('h3', { text: 'Новый пост по шаблону «' + t.title + '»' }), f],
    [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', class: 'primary', text: 'Создать черновик', onclick: async () => {
      try {
        const v = f.value;
        const draft = await api('POST', '/api/templates/' + t.id + '/draft', { version: t.version, values: v.values, title: v.title });
        closeDialog(d);
        await openDraft(draft.id);
        toast('Новый черновик по шаблону «' + t.title + '»', 'ok');
      } catch (e) { f.error = e.message; }
    } })]);
}

/** @param {TemplateRow} row */
async function deleteTemplate(row) {
  if (!confirm('Удалить шаблон «' + row.title + '»? Черновики из него останутся.')) return;
  // 409: someone saved it since the list was loaded; the list shows the newer one, nothing is deleted
  try { await api('DELETE', '/api/templates/' + row.id + '?version=' + row.version); } finally { await loadTemplates().catch(() => {}); }
}

export function bindTemplates() {
  const el = list();
  const on = (name, fn) => el.addEventListener(name, (e) => fn(e.detail).catch((err) => toast(err.message, 'err')));
  on('template-new', () => editTemplate());
  on('template-edit', editTemplate);
  on('template-fill', fillTemplate);
  on('template-delete', deleteTemplate);
  loadTemplates().catch(() => {});
}
