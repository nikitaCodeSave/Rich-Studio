// Lookups in the Bot API spec and default values for new blocks and fields.
import { S } from './state.js';

// ---------- spec helpers ----------
export const T = (name) => (S.spec && S.spec.types[name]) || null;
export const constOf = (name) => (T(name) || {}).const;
export function blockTypes() {
  const base = T('InputRichBlock');
  return ((base && base.subtypes) || []).filter((n) => T(n)).map((n) => ({ name: n, const: constOf(n), desc: T(n).description }));
}
export function blockTypeName(constName) {
  return blockTypes().find((b) => b.const === constName)?.name;
}
export function rtTypes() {
  const base = T('RichText');
  return ((base && base.subtypes) || []).filter((n) => T(n)).map((n) => ({ name: n, const: constOf(n), desc: T(n).description }));
}
export function constField(name) {
  const t = T(name);
  if (!t || !t.const) return null;
  const f = t.fields.find((f) => f.name === 'type' || f.name === 'source' || f.name === 'status');
  return f && f.name;
}
export const isNewType = (name) => (S.spec.changes || []).some((c) => (c.items || []).some((i) => i.name === name || (i.name || '').endsWith('→ ' + name)));

export function defaultFor(types, fieldName) {
  const t = types[0];
  if (t === 'RichText') return '';
  if (t === 'String') return { date_time_format: 'wDT' }[fieldName] ?? '';
  if (t === 'Integer') return { size: 2, zoom: 15, unix_time: Math.floor(Date.now() / 1000) + 86400 }[fieldName] ?? 0;
  if (t === 'Float') return 0;
  if (t === 'Boolean' || t === 'True') return false;
  if (t === 'Array of InputRichBlock') return [{ type: 'paragraph', text: '' }];
  if (t === 'Array of InputRichBlockListItem') return [{ blocks: [{ type: 'paragraph', text: '' }] }, { blocks: [{ type: 'paragraph', text: '' }] }];
  if (t === 'Array of Array of RichBlockTableCell') return [[cell('Колонка 1', true), cell('Колонка 2', true)], [cell(''), cell('')]];
  if (t === 'Array of RichMessageButton') return [{ text: 'Кнопка', url: '' }];
  if (t.startsWith('Array of')) return [];
  if (t === 'Location') return { latitude: 55.7522, longitude: 37.6156 };
  return defaultObject(t);
}
export function cell(text, header) { const c = { align: 'left', valign: 'top' }; if (text) c.text = text; if (header) c.is_header = true; return c; }
export function defaultObject(typeName) {
  const t = T(typeName);
  if (!t) return {};
  if (t.subtypes && t.subtypes.length) return defaultObject(t.subtypes[0]);
  const o = {};
  const cf = constField(typeName);
  if (cf) o[cf] = t.const;
  for (const f of t.fields) {
    if (!f.required || f.name === cf) continue;
    if (f.enum && f.types[0] === 'String') { o[f.name] = f.enum[0]; continue; }
    o[f.name] = defaultFor(f.types, f.name);
  }
  return o;
}
export function newBlock(constName) {
  const name = blockTypeName(constName);
  const b = defaultObject(name);
  if (constName === 'heading') b.size = 2;
  return b;
}
