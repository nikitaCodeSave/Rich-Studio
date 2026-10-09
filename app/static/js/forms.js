// Forms generated from the spec: block list, object fields, tables, media fields.
import { api } from './api.js';
import { closeDialog, editJson, openDialog } from './dialogs.js';
import { $, $$, clone, h, toast } from './dom.js';
import { changed } from './editor.js';
import { BLOCK_GROUPS, BLOCK_LABELS, FIELD_HINTS, FIELD_LABELS, IGNORED_MEDIA_FIELDS, plural } from './labels.js';
import { clipboardTable } from './paste.js';
import { richTextEditor } from './rich-text.js';
import { blockTypeName, blockTypes, cell, constField, constOf, defaultFor, defaultObject, isNewType, newBlock, T } from './spec.js';
import { S } from './state.js';

/** block object -> its card, so a validation message can scroll to the block it is about */
export const cardOf = new WeakMap();

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
  const moveTo = (j) => { if (j === i) return; arr.splice(j, 0, ...arr.splice(i, 1)); redraw(); changed(); };
  const head = h('div', { class: 'block-head' },
    h('span', { class: 'label', text: label, title: name ? T(name).description : 'Тип не найден в спецификации', onclick: () => card.classList.toggle('collapsed') }),
    h('span', { class: 'type', text: b.type }),
    h('span', { class: 'tools' },
      h('button', { title: 'Выше', text: '↑', onclick: () => move(-1) }),
      h('button', { title: 'Ниже', text: '↓', onclick: () => move(1) }),
      arr.length > 2 ? h('button', { title: 'В начало', text: '⤒', onclick: () => moveTo(0) }) : null,
      arr.length > 2 ? h('button', { title: 'В конец', text: '⤓', onclick: () => moveTo(arr.length - 1) }) : null,
      h('button', { title: 'Вставить блок ниже', text: '＋', onclick: () => pickBlock((c) => { arr.splice(i + 1, 0, newBlock(c)); redraw(); changed(); }) }),
      h('button', { title: 'Дублировать', text: '⧉', onclick: () => { arr.splice(i + 1, 0, clone(b)); redraw(); changed(); } }),
      h('button', { title: 'Редактировать JSON', text: '{}', onclick: () => editJson(b, (v) => { arr[i] = v; redraw(); changed(); }) }),
      h('button', { title: 'Удалить', text: '✕', onclick: () => { arr.splice(i, 1); redraw(); changed(); } })));
  const body = h('div', { class: 'block-body' });
  if (name) body.append(objectFields(name, b, depth));
  else body.append(h('p', { class: 'muted small', text: 'Тип «' + b.type + '» не найден в спецификации ' + S.spec.version + '. Блок уйдёт в Telegram как есть; правьте через {}.' }));
  card.append(head, body);
  cardOf.set(b, card);
  // a picture pasted into a text field becomes a block right after this one
  const insertFiles = async (files) => {
    const added = await uploadBlocks(files);
    if (added.length) { arr.splice(i + 1, 0, ...added); redraw(); changed(); }
  };
  card.addEventListener('rs-paste-files', (e) => { e.stopPropagation(); insertFiles(/** @type {CustomEvent} */ (e).detail); });
  // files dragged from the desktop onto a block land right after it
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  card.addEventListener('dragover', (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.stopPropagation(); card.classList.add('drop'); });
  card.addEventListener('dragleave', (e) => { if (!card.contains(/** @type {Node} */ (e.relatedTarget))) card.classList.remove('drop'); });
  card.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault(); e.stopPropagation(); card.classList.remove('drop');
    insertFiles([...e.dataTransfer.files]);
  });
  return card;
}

export function pickBlock(cb) {
  const all = blockTypes();
  const groupOf = (c) => Object.keys(BLOCK_GROUPS).find((g) => BLOCK_GROUPS[g].includes(c)) || 'Другое';
  const tile = (b) => h('button', {
    class: isNewType(b.name) ? 'new' : '', title: b.desc, 'data-q': ((BLOCK_LABELS[b.const] || '') + ' ' + b.const).toLowerCase(),
    onclick: () => { closeDialog(); cb(b.const); },
  }, h('b', { text: BLOCK_LABELS[b.const] || b.const }), h('small', { text: b.const + (isNewType(b.name) ? ' · новое' : '') }));
  const groups = [...Object.keys(BLOCK_GROUPS), 'Другое'].map((g) => {
    const items = all.filter((b) => groupOf(b.const) === g);
    return items.length ? h('section', { class: 'palette-group' }, h('h4', { text: g }), h('div', { class: 'palette' }, items.map(tile))) : null;
  });
  const box = h('div', { class: 'palette-groups' }, groups);
  const search = h('input', { type: 'search', placeholder: 'Найти блок', 'aria-label': 'Найти блок', oninput: (e) => {
    const q = e.target.value.trim().toLowerCase();
    $$('button', box).forEach((b) => { b.hidden = !!q && !b.dataset.q.includes(q); });
    $$('.palette-group', box).forEach((g) => { g.hidden = !$$('button', g).some((b) => !b.hidden); });
  }, onkeydown: (e) => { if (e.key === 'Enter') /** @type {HTMLElement | undefined} */ ($$('button', box).find((b) => !b.hidden))?.click(); } });
  openDialog([h('h3', { text: 'Добавить блок' }), search, box, h('p', { class: 'muted small', text: 'Блоки из спецификации ' + S.spec.version + '. Наведите, чтобы увидеть описание.' })], [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' })]);
  search.focus();
}

/** Upload pasted or dropped files; each becomes a media block (photo, video…) pointing at upload:<name>. */
export async function uploadBlocks(files) {
  const blocks = [];
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f, f.name && f.name !== 'image.png' ? f.name : 'paste-' + Date.now() + '.' + (f.type.split('/')[1] || 'bin'));
    try {
      const u = await api('POST', '/api/media', fd);
      const b = newBlock(u.kind);
      if (b && b[u.kind]) { b[u.kind].media = u.ref; blocks.push(b); } else toast('Файл ' + u.name + ' загружен, но такого блока в спецификации нет', 'err');
    } catch (e) { toast(e.message, 'err'); }
  }
  if (blocks.length) { toast('Вставлено: ' + plural(blocks.length, 'файл', 'файла', 'файлов'), 'ok'); document.dispatchEvent(new CustomEvent('rs-uploads-changed')); }
  return blocks;
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
  return h('div', { class: 'field' + (wide ? ' wide' : ''), 'data-field': f.name }, label, fieldEditor(f, obj, f.name, depth, redraw), rm);
}

// editor for obj[key] according to spec field types
export function fieldEditor(f, obj, key, depth, redraw) {
  const types = f.types;
  const t = types[0];
  const set = (v) => { obj[key] = v; changed(); };
  if (types.length === 1) {
    if (t === 'RichText') return richTextEditor(obj, key, { placeholder: FIELD_HINTS[f.name] || '' });
    if (t === 'String' && f.enum && f.enum.length > 1) {
      return h('select', { onchange: (e) => set(e.target.value) }, f.enum.map((v) => h('option', { value: v, text: v, selected: obj[key] === v ? true : null })));
    }
    if (t === 'String') {
      const long = ['expression', 'text'].includes(key);
      const inp = h(long ? 'textarea' : 'input', { type: 'text', value: obj[key] ?? '', placeholder: FIELD_HINTS[f.name] || '', oninput: (e) => set(e.target.value) });
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
    const it = itemType === 'InputRichBlockListItem' ? { blocks: [{ type: 'paragraph', text: '' }] } : itemType === 'RichMessageButton' ? { text: 'Кнопка', url: '' } : defaultObject(itemType);
    arr.push(it); redraw(); changed();
  } }));
  return box;
}

// ---------- table ----------
const ALIGN = { left: 'По левому краю', center: 'По центру', right: 'По правому краю' };
const VALIGN = { top: 'Сверху', middle: 'По середине', bottom: 'Снизу' };

// cells are plain text fields; what a cell is (header, alignment, merge) is set in the bar under the table for the cell in focus
export function tableEditor(cells, focus) {
  const box = h('div', { class: 'table-ed' });
  const redraw = (at) => { const next = tableEditor(cells, at); box.replaceWith(next); };
  const width = (r) => (r || []).reduce((n, c) => n + Math.max(1, (c && c.colspan) || 1), 0);
  const cols = () => Math.max(1, ...cells.map(width));
  const bar = h('div', { class: 'cell-bar', role: 'toolbar', 'aria-label': 'Ячейка' });
  const tbl = h('table');
  /** @type {HTMLElement | null} */
  let toFocus = null;
  const showBar = (ri, ci, td) => {
    const c = cells[ri][ci];
    $$('td.sel', tbl).forEach((x) => x.classList.remove('sel'));
    td.classList.add('sel');
    const edit = (fn) => { fn(); changed(); };
    const num = (key, title) => h('label', { class: 'small', title }, title + ' ', h('input', { type: 'number', min: 1, value: c[key] || 1, 'aria-label': title, oninput: (e) => edit(() => { const v = +e.target.value; if (v > 1) c[key] = v; else delete c[key]; td.setAttribute(key, v > 1 ? String(v) : '1'); }) }));
    bar.replaceChildren(
      h('span', { class: 'muted small', text: `Ячейка ${ri + 1}:${ci + 1}` }),
      h('button', { type: 'button', class: 'ghost small-btn' + (c.is_header ? ' on' : ''), 'aria-pressed': String(!!c.is_header), text: 'Заголовок', onclick: (e) => edit(() => {
        if (c.is_header) delete c.is_header; else c.is_header = true;
        td.classList.toggle('hdr', !!c.is_header); e.target.classList.toggle('on', !!c.is_header); e.target.setAttribute('aria-pressed', String(!!c.is_header));
      }) }),
      h('select', { 'aria-label': 'Выравнивание', onchange: (e) => edit(() => { c.align = e.target.value; td.style.textAlign = c.align; }) }, Object.entries(ALIGN).map(([v, t]) => h('option', { value: v, text: t, selected: (c.align || 'left') === v ? true : null }))),
      h('select', { 'aria-label': 'По вертикали', onchange: (e) => edit(() => { c.valign = e.target.value; }) }, Object.entries(VALIGN).map(([v, t]) => h('option', { value: v, text: t, selected: (c.valign || 'top') === v ? true : null }))),
      num('colspan', 'Ширина'), num('rowspan', 'Высота'),
      h('span', { class: 'grow' }),
      h('button', { type: 'button', class: 'ghost small-btn', title: 'Строка ниже', text: '＋ строка', onclick: () => { cells.splice(ri + 1, 0, Array.from({ length: cols() }, () => cell(''))); redraw([ri + 1, ci]); changed(); } }),
      h('button', { type: 'button', class: 'ghost small-btn', title: 'Колонка справа', text: '＋ колонка', onclick: () => { cells.forEach((r) => r.splice(Math.min(ci + 1, r.length), 0, cell(''))); redraw([ri, ci + 1]); changed(); } }),
      h('button', { type: 'button', class: 'ghost small-btn', title: 'Удалить строку', text: '✕ строка', onclick: () => { cells.splice(ri, 1); redraw(cells.length ? [Math.min(ri, cells.length - 1), 0] : null); changed(); } }),
      h('button', { type: 'button', class: 'ghost small-btn', title: 'Удалить колонку', text: '✕ колонка', onclick: () => { cells.forEach((r) => r.splice(ci, 1)); redraw([ri, Math.max(0, ci - 1)]); changed(); } }));
    bar.hidden = false;
  };
  // rows from a spreadsheet land from this cell to the right and down, growing the table
  const pasteTable = (ri, ci) => (e) => {
    const rows = clipboardTable(e.clipboardData.getData('text/plain'), e.clipboardData.getData('text/html'));
    if (!rows) return false;
    rows.forEach((vals, dr) => {
      while (cells.length <= ri + dr) cells.push(Array.from({ length: cols() }, () => cell('')));
      const row = cells[ri + dr];
      vals.forEach((v, dc) => {
        while (row.length <= ci + dc) row.push(cell(''));
        const c = row[ci + dc] && typeof row[ci + dc] === 'object' ? row[ci + dc] : (row[ci + dc] = cell(''));
        if (v.trim()) c.text = v.trim(); else delete c.text;
      });
    });
    const w = cols();
    cells.forEach((r) => { while (width(r) < w) r.push(cell('')); });
    redraw([ri, ci]); changed();
    toast(`Вставлено ${plural(rows.length, 'строка', 'строки', 'строк')} × ${Math.max(...rows.map((r) => r.length))}`, 'ok');
    return true;
  };
  cells.forEach((row, ri) => {
    const tr = h('tr');
    for (let ci = 0; ci < row.length; ci++) {
      if (!row[ci] || typeof row[ci] !== 'object') row[ci] = cell('');
      const c = row[ci];
      const td = h('td', { class: c.is_header ? 'hdr' : '', colspan: c.colspan > 1 ? c.colspan : null, rowspan: c.rowspan > 1 ? c.rowspan : null, style: c.align && c.align !== 'left' ? 'text-align:' + c.align : null },
        richTextEditor(c, 'text', { placeholder: ri === 0 && ci === 0 && cells.length === 1 ? 'можно вставить из таблицы' : '', compact: true, onPaste: pasteTable(ri, ci) }));
      td.addEventListener('focusin', () => showBar(ri, ci, td));
      if (focus && focus[0] === ri && focus[1] === ci) toFocus = td;
      tr.append(td);
    }
    tbl.append(tr);
  });
  bar.hidden = true;
  box.append(h('div', { class: 'table-scroll' }, tbl), bar, h('div', { class: 'row' },
    h('button', { class: 'ghost small-btn', text: '＋ строка', onclick: () => { cells.push(Array.from({ length: cols() }, () => cell(''))); redraw([cells.length - 1, 0]); changed(); } }),
    h('button', { class: 'ghost small-btn', text: '＋ колонка', onclick: () => { cells.forEach((r, i) => r.push(cell(i === 0 && r[0] && r[0].is_header ? 'Колонка' : '', i === 0 && r[0] && r[0].is_header))); redraw([0, cells[0] ? cells[0].length - 1 : 0]); changed(); } }),
    cells.some((r) => width(r) < cols()) ? h('button', { class: 'ghost small-btn', text: 'Дополнить строки', title: 'Добавить пустые ячейки в короткие строки', onclick: () => { const w = cols(); cells.forEach((r) => { while (width(r) < w) r.push(cell('')); }); redraw(); changed(); } }) : null,
    h('span', { class: 'muted small', text: 'Нажмите на ячейку, чтобы сделать её заголовком, выровнять или объединить. Таблицу из Google Sheets или Excel можно вставить в любую ячейку.' })));
  const cellToFocus = /** @type {HTMLElement | null} */ (toFocus);
  if (cellToFocus) setTimeout(() => /** @type {HTMLElement | null} */ (cellToFocus.querySelector('.rte'))?.focus(), 0);
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
