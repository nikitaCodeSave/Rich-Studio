// Forms generated from the spec: block list, object fields, tables, media fields.
import { closeDialog, editJson, openDialog } from './dialogs.js';
import { $, clone, h } from './dom.js';
import { changed } from './editor.js';
import { BLOCK_LABELS, FIELD_LABELS, IGNORED_MEDIA_FIELDS } from './labels.js';
import { richTextEditor } from './rich-text.js';
import { blockTypeName, blockTypes, cell, constField, constOf, defaultFor, defaultObject, isNewType, newBlock, T } from './spec.js';
import { S } from './state.js';

// ---------- block editor ----------
export function renderBlocksPane() {
  const pane = $('#pane-blocks');
  pane.replaceChildren(blockList(S.buf.blocks, 0));
}

export function blockList(arr, depth) {
  const box = h('div', { class: 'blocks' + (depth ? ' nested' : '') });
  const redraw = () => box.replaceWith(blockList(arr, depth));
  arr.forEach((b, i) => box.append(blockCard(arr, i, depth, redraw)));
  box.append(h('button', { class: 'ghost add-block small-btn', text: '＋ блок', onclick: () => pickBlock((c) => { arr.push(newBlock(c)); redraw(); changed(); }) }));
  return box;
}

export function blockCard(arr, i, depth, redraw) {
  const b = arr[i];
  const name = blockTypeName(b.type);
  const label = BLOCK_LABELS[b.type] || b.type;
  const card = h('div', { class: 'block' });
  const move = (d) => { const j = i + d; if (j < 0 || j >= arr.length) return; [arr[i], arr[j]] = [arr[j], arr[i]]; redraw(); changed(); };
  const head = h('div', { class: 'block-head' },
    h('span', { class: 'label', text: label, title: name ? T(name).description : 'Тип не найден в спецификации', onclick: () => card.classList.toggle('collapsed') }),
    h('span', { class: 'type', text: b.type }),
    h('span', { class: 'tools' },
      h('button', { title: 'Выше', text: '↑', onclick: () => move(-1) }),
      h('button', { title: 'Ниже', text: '↓', onclick: () => move(1) }),
      h('button', { title: 'Вставить блок ниже', text: '＋', onclick: () => pickBlock((c) => { arr.splice(i + 1, 0, newBlock(c)); redraw(); changed(); }) }),
      h('button', { title: 'Дублировать', text: '⧉', onclick: () => { arr.splice(i + 1, 0, clone(b)); redraw(); changed(); } }),
      h('button', { title: 'Редактировать JSON', text: '{}', onclick: () => editJson(b, (v) => { arr[i] = v; redraw(); changed(); }) }),
      h('button', { title: 'Удалить', text: '✕', onclick: () => { arr.splice(i, 1); redraw(); changed(); } })));
  const body = h('div', { class: 'block-body' });
  if (name) body.append(objectFields(name, b, depth));
  else body.append(h('p', { class: 'muted small', text: 'Тип «' + b.type + '» не найден в спецификации ' + S.spec.version + '. Блок уйдёт в Telegram как есть; правьте через {}.' }));
  card.append(head, body);
  return card;
}

export function pickBlock(cb) {
  const items = blockTypes().map((b) => h('button', {
    class: isNewType(b.name) ? 'new' : '', title: b.desc,
    onclick: () => { closeDialog(); cb(b.const); },
  }, h('b', { text: BLOCK_LABELS[b.const] || b.const }), h('small', { text: b.const + (isNewType(b.name) ? ' · новое' : '') })));
  openDialog([h('h3', { text: 'Добавить блок' }), h('p', { class: 'muted small', text: 'Список блоков взят из спецификации ' + S.spec.version + '. Наведите, чтобы увидеть описание.' }), h('div', { class: 'palette' }, items)], [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' })]);
}

// fields of an object type, editing obj in place
export function objectFields(typeName, obj, depth, opts) {
  opts = opts || {};
  const t = T(typeName);
  const box = h('div', { class: 'fields' });
  const cf = constField(typeName);
  if (cf && obj[cf] === undefined) obj[cf] = t.const;
  const fields = t.fields.filter((f) => f.name !== cf && !(opts.exclude || []).includes(f.name) && !(typeName.startsWith('InputMedia') && IGNORED_MEDIA_FIELDS.has(f.name)));
  const redraw = () => box.replaceWith(objectFields(typeName, obj, depth, opts));
  const shown = fields.filter((f) => f.required || obj[f.name] !== undefined);
  const hidden = fields.filter((f) => !f.required && obj[f.name] === undefined);
  shown.forEach((f) => box.append(fieldRow(typeName, f, obj, depth, redraw)));
  if (hidden.length) {
    const sel = h('select', { class: 'add-field', onchange: (e) => { const f = hidden.find((x) => x.name === e.target.value); if (!f) return; obj[f.name] = f.enum ? (f.enum.includes('primary') ? 'primary' : f.enum[0]) : defaultFor(f.types, f.name); if (f.types[0] === 'Boolean') obj[f.name] = true; redraw(); changed(); } },
      h('option', { value: '', text: '＋ поле…' }), hidden.map((f) => h('option', { value: f.name, text: (FIELD_LABELS[f.name] ? FIELD_LABELS[f.name] + ' · ' : '') + f.name, title: f.description })));
    box.append(sel);
  }
  return box;
}

export function fieldRow(typeName, f, obj, depth, redraw) {
  const label = h('label', { class: f.required ? 'req' : '', title: f.description, text: FIELD_LABELS[f.name] || f.name });
  const wide = /^Array of (InputRichBlock|InputRichBlockListItem|Array of)/.test(f.types[0]) || f.types[0] === 'Array of RichMessageButton';
  const rm = f.required ? h('span') : h('button', { class: 'rm', title: 'Убрать поле', text: '✕', onclick: () => { delete obj[f.name]; redraw(); changed(); } });
  return h('div', { class: 'field' + (wide ? ' wide' : '') }, label, fieldEditor(f, obj, f.name, depth, redraw), rm);
}

// editor for obj[key] according to spec field types
export function fieldEditor(f, obj, key, depth, redraw) {
  const types = f.types;
  const t = types[0];
  const set = (v) => { obj[key] = v; changed(); };
  if (types.length === 1) {
    if (t === 'RichText') return richTextEditor(obj, key, { placeholder: f.description });
    if (t === 'String' && f.enum && f.enum.length > 1) {
      return h('select', { onchange: (e) => set(e.target.value) }, f.enum.map((v) => h('option', { value: v, text: v, selected: obj[key] === v ? true : null })));
    }
    if (t === 'String') {
      const long = ['expression', 'text'].includes(key);
      const inp = h(long ? 'textarea' : 'input', { type: 'text', value: obj[key] ?? '', placeholder: f.description.replace(/^Optional\.\s*/, '').slice(0, 90), oninput: (e) => set(e.target.value) });
      return inp;
    }
    if (t === 'Integer' || t === 'Float') {
      const wrap = h('div', { class: 'row' });
      const inp = h('input', { type: 'number', step: t === 'Float' ? 'any' : '1', value: obj[key] ?? '', oninput: (e) => set(e.target.value === '' ? undefined : Number(e.target.value)) });
      wrap.append(inp);
      if (key === 'unix_time') {
        const dt = h('input', { type: 'datetime-local', oninput: (e) => { const v = Math.floor(new Date(e.target.value).getTime() / 1000); if (v) { inp.value = v; set(v); } } });
        if (obj[key]) dt.value = new Date(obj[key] * 1000 - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
        wrap.append(dt);
      }
      return wrap;
    }
    if (t === 'Boolean' || t === 'True') return h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => set(e.target.checked) });
    if (t === 'Array of InputRichBlock') { if (!Array.isArray(obj[key])) obj[key] = []; return blockList(obj[key], depth + 1); }
    if (t === 'Array of Array of RichBlockTableCell') { if (!Array.isArray(obj[key])) obj[key] = defaultFor(types); return tableEditor(obj[key]); }
    if (/^InputMedia/.test(t)) return mediaPicker(obj, key, t);
    if (t.startsWith('Array of ') && T(t.slice(9)) && !T(t.slice(9)).subtypes.length) return objectArray(obj, key, t.slice(9), depth);
    const st = T(t);
    if (st && !st.subtypes.length) {
      if (!st.fields.length) return h('input', { type: 'checkbox', checked: obj[key] !== undefined, title: st.description, onchange: (e) => { if (e.target.checked) obj[key] = {}; else delete obj[key]; changed(); } });
      if (typeof obj[key] !== 'object' || obj[key] === null) obj[key] = defaultObject(t);
      return h('div', { class: 'obj' }, objectFields(t, obj[key], depth));
    }
  }
  if (types.every((x) => ['Integer', 'String'].includes(x))) {
    return h('input', { type: 'text', value: obj[key] ?? '', oninput: (e) => set(/^-?\d+$/.test(e.target.value) ? Number(e.target.value) : e.target.value) });
  }
  return jsonField(obj, key);
}

export function jsonField(obj, key) {
  const ta = h('textarea', { value: obj[key] === undefined ? '' : JSON.stringify(obj[key], null, 1) });
  ta.addEventListener('input', () => { try { obj[key] = JSON.parse(ta.value); ta.style.borderColor = ''; changed(); } catch { ta.style.borderColor = 'var(--err)'; } });
  return ta;
}

export function objectArray(obj, key, itemType, depth) {
  if (!Array.isArray(obj[key])) obj[key] = [];
  const arr = obj[key];
  const box = h('div', { class: 'blocks nested' });
  const redraw = () => box.replaceWith(objectArray(obj, key, itemType, depth));
  arr.forEach((it, i) => {
    if (typeof it !== 'object' || it === null) arr[i] = it = defaultObject(itemType);
    box.append(h('div', { class: 'obj' },
      h('div', { class: 'row', style: 'margin:0' }, h('span', { class: 'muted small', text: (FIELD_LABELS[key] || itemType) + ' ' + (i + 1) }), h('span', { class: 'grow' }),
        h('button', { class: 'ghost small-btn', text: '↑', onclick: () => { if (i) { [arr[i - 1], arr[i]] = [arr[i], arr[i - 1]]; redraw(); changed(); } } }),
        h('button', { class: 'ghost small-btn', text: '✕', onclick: () => { arr.splice(i, 1); redraw(); changed(); } })),
      objectFields(itemType, it, depth + 1)));
  });
  box.append(h('button', { class: 'ghost small-btn add-block', text: '＋ ' + (itemType === 'InputRichBlockListItem' ? 'пункт' : itemType === 'RichMessageButton' ? 'кнопка' : 'элемент'), onclick: () => {
    const it = itemType === 'InputRichBlockListItem' ? { blocks: [{ type: 'paragraph', text: '' }] } : itemType === 'RichMessageButton' ? { text: 'Кнопка', url: 'https://' } : defaultObject(itemType);
    arr.push(it); redraw(); changed();
  } }));
  return box;
}

// ---------- table ----------
export function tableEditor(cells) {
  const box = h('div', { class: 'table-ed' });
  const redraw = () => box.replaceWith(tableEditor(cells));
  const width = (r) => (r || []).reduce((n, c) => n + Math.max(1, (c && c.colspan) || 1), 0);
  const cols = Math.max(1, ...cells.map(width));
  const tbl = h('table');
  cells.forEach((row, ri) => {
    const tr = h('tr');
    for (let ci = 0; ci < row.length; ci++) {
      if (!row[ci] || typeof row[ci] !== 'object') row[ci] = cell('');
      const c = row[ci];
      const td = h('td', { class: c.is_header ? 'hdr' : '', colspan: c.colspan > 1 ? c.colspan : null, rowspan: c.rowspan > 1 ? c.rowspan : null },
        richTextEditor(c, 'text', { placeholder: 'пусто = невидимая ячейка', compact: true }),
        h('div', { class: 'cell-tools' },
          h('label', { class: 'small', title: 'Заголовок' }, h('input', { type: 'checkbox', checked: !!c.is_header, onchange: (e) => { if (e.target.checked) c.is_header = true; else delete c.is_header; td.classList.toggle('hdr', e.target.checked); changed(); } }), 'th'),
          h('select', { title: 'align', onchange: (e) => { c.align = e.target.value; changed(); } }, ['left', 'center', 'right'].map((v) => h('option', { value: v, text: { left: '⇤', center: '↔', right: '⇥' }[v], selected: c.align === v ? true : null }))),
          h('select', { title: 'valign', onchange: (e) => { c.valign = e.target.value; changed(); } }, ['top', 'middle', 'bottom'].map((v) => h('option', { value: v, text: { top: '⤒', middle: '↕', bottom: '⤓' }[v], selected: c.valign === v ? true : null }))),
          h('input', { type: 'number', min: 1, title: 'colspan', placeholder: 'col', value: c.colspan || '', oninput: (e) => { const v = +e.target.value; if (v > 1) c.colspan = v; else delete c.colspan; changed(); } }),
          h('input', { type: 'number', min: 1, title: 'rowspan', placeholder: 'row', value: c.rowspan || '', oninput: (e) => { const v = +e.target.value; if (v > 1) c.rowspan = v; else delete c.rowspan; changed(); } })));
      tr.append(td);
    }
    tr.append(h('td', { style: 'border:0;min-width:0' }, h('button', { class: 'ghost small-btn', title: 'Удалить строку', text: '✕', onclick: () => { cells.splice(ri, 1); redraw(); changed(); } })));
    tbl.append(tr);
  });
  const del = h('tr', {}, Array.from({ length: cols }, (_, ci) => h('td', { style: 'border:0;min-width:0;text-align:center' }, h('button', { class: 'ghost small-btn', title: 'Удалить колонку', text: '✕', onclick: () => { cells.forEach((r) => r.splice(ci, 1)); redraw(); changed(); } }))));
  tbl.append(del);
  box.append(tbl, h('div', { class: 'row' },
    h('button', { class: 'ghost small-btn', text: '＋ строка', onclick: () => { cells.push(Array.from({ length: cols }, () => cell(''))); redraw(); changed(); } }),
    h('button', { class: 'ghost small-btn', text: '＋ колонка', onclick: () => { cells.forEach((r, i) => r.push(cell(i === 0 && r[0] && r[0].is_header ? 'Колонка' : '', i === 0 && r[0] && r[0].is_header))); redraw(); changed(); } }),
    h('button', { class: 'ghost small-btn', text: '＋ ячейка', title: 'Добавить ячейку в короткие строки', onclick: () => { cells.forEach((r) => { while (width(r) < cols) r.push(cell('')); }); redraw(); changed(); } })));
  return box;
}

// ---------- media ----------
export function kindOfType(t) { return constOf(t) || t.replace('InputMedia', '').replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase(); }

export function mediaPicker(obj, key, typeName) {
  const kind = kindOfType(typeName);
  if (typeof obj[key] !== 'object' || obj[key] === null) obj[key] = { type: kind, media: '' };
  const m = obj[key];
  m.type = kind;
  const box = h('div', { class: 'obj' });
  const input = h('input', { type: 'text', value: m.media || '', placeholder: 'URL, file_id или выберите загруженный файл', oninput: (e) => { m.media = e.target.value; changed(); } });
  const options = S.uploads.filter((u) => u.kind === kind || kind === 'document' || (kind === 'animation' && u.kind === 'video'));
  const sel = h('select', { onchange: (e) => { if (e.target.value) { m.media = e.target.value; input.value = m.media; changed(); } } },
    h('option', { value: '', text: options.length ? 'Загруженные…' : 'нет загруженных ' + kind }), options.map((u) => h('option', { value: u.ref, text: u.name, selected: m.media === u.ref ? true : null })));
  box.append(h('div', { class: 'media-pick' }, input, sel));
  box.append(objectFields(typeName, m, 9, { exclude: ['media'] }));
  return box;
}
