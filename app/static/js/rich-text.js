// RichText field: contenteditable with a toolbar, chips for entities, DOM <-> RichText.
import { closeDialog, editJson, openDialog } from './dialogs.js';
import { clone, h, toast } from './dom.js';
import { changed } from './editor.js';
import { objectFields } from './forms.js';
import { RT_LABELS, RT_WRAP } from './labels.js';
import { defaultObject, isNewType, rtTypes, T } from './spec.js';

// ---------- rich text field ----------
/** @type {{ ed: HTMLElement, sync: () => void } | null} */
let activeRte = null;
export const toolbar = h('div', { class: 'toolbar' });
document.body.append(toolbar);

export function richTextEditor(obj, key, opts) {
  opts = opts || {};
  const ed = h('div', { class: 'rte', contenteditable: 'true', 'data-ph': (opts.placeholder || '').replace(/^Optional\.\s*/, '').slice(0, 80) });
  ed.append(rtToDom(obj[key]));
  if (/\n$/.test(plainRt(obj[key]))) ed.append(h('br')); // the browser swallows one trailing <br>
  const sync = () => {
    const v = domToRt(ed);
    if (v === '' && !opts.keepEmpty && key === 'text' && obj.align !== undefined) delete obj[key]; // empty table cell = invisible
    else obj[key] = v;
    changed();
  };
  ed.addEventListener('input', sync);
  ed.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); document.execCommand('insertLineBreak'); }
    if ((e.ctrlKey || e.metaKey) && ['b', 'i', 'u'].includes(e.key.toLowerCase())) { e.preventDefault(); wrapSelection({ b: 'bold', i: 'italic', u: 'underline' }[e.key.toLowerCase()]); }
  });
  ed.addEventListener('paste', (e) => { e.preventDefault(); document.execCommand('insertText', false, e.clipboardData.getData('text/plain')); });
  ed.addEventListener('focus', () => { activeRte = { ed, sync }; showToolbar(ed); });
  ed.addEventListener('blur', () => setTimeout(() => { const a = document.activeElement; if (!(a && a.classList && a.classList.contains('rte')) && !toolbar.contains(a) && !toolbar.matches(':hover')) toolbar.classList.remove('on'); }, 150));
  ed.addEventListener('click', (e) => {
    const chip = e.target.closest('.rt-chip');
    if (chip) editEntity(JSON.parse(chip.dataset.rt), (v) => { chip.replaceWith(chipEl(v)); sync(); }, () => { chip.remove(); sync(); });
  });
  return h('div', { class: 'rte-wrap' }, ed);
}

export function showToolbar(ed) {
  toolbar.replaceChildren();
  const btn = (label, title, fn) => h('button', { type: 'button', text: label, title, onmousedown: (e) => { e.preventDefault(); fn(); } });
  toolbar.append(
    btn('Ж', 'Жирный (Ctrl+B)', () => wrapSelection('bold')), btn('К', 'Курсив (Ctrl+I)', () => wrapSelection('italic')),
    btn('Ч', 'Подчёркнутый (Ctrl+U)', () => wrapSelection('underline')), btn('З', 'Зачёркнутый', () => wrapSelection('strikethrough')),
    btn('▒', 'Спойлер', () => wrapSelection('spoiler')), btn('🖍', 'Маркер', () => wrapSelection('marked')),
    btn('</>', 'Код', () => wrapSelection('code')), btn('x₂', 'Нижний индекс', () => wrapSelection('subscript')), btn('x²', 'Верхний индекс', () => wrapSelection('superscript')),
    btn('🔗', 'Ссылка', () => { const url = prompt('URL ссылки', 'https://'); if (url) wrapSelection('url', url); }),
    btn('⌫', 'Убрать форматирование', () => clearFormatting()));
  const others = rtTypes().filter((t) => t.const && !RT_WRAP[t.const] && t.const !== 'url');
  const sel = h('select', { title: 'Вставить элемент из спецификации', onmousedown: (e) => e.stopPropagation(), onchange: (e) => {
    const name = e.target.value; e.target.value = '';
    if (!name) return;
    insertEntity(name);
  } }, h('option', { value: '', text: '＋ элемент…' }), others.map((t) => h('option', { value: t.name, title: t.desc, text: (RT_LABELS[t.const] || t.const) + (isNewType(t.name) ? ' · новое' : '') })));
  toolbar.append(sel);
  const r = ed.getBoundingClientRect();
  toolbar.classList.add('on');
  const top = r.top - toolbar.offsetHeight - 6;
  toolbar.style.top = (top < 4 ? r.bottom + 6 : top) + 'px';
  toolbar.style.left = Math.max(4, Math.min(r.left, innerWidth - toolbar.offsetWidth - 8)) + 'px';
}
addEventListener('scroll', () => { if (activeRte && toolbar.classList.contains('on')) showToolbar(activeRte.ed); }, true);

export function selectionIn(ed) {
  const s = getSelection();
  if (!s || !s.rangeCount) return null;
  const r = s.getRangeAt(0);
  return ed.contains(r.commonAncestorContainer) ? r : null;
}

export function wrapEl(kind, url) {
  if (kind === 'url') return h('a', { 'data-url': url, title: url });
  const spec = RT_WRAP[kind];
  const [tag, cls] = spec.split('.');
  return h(tag, { class: cls || null });
}

export function wrapSelection(kind, url) {
  if (!activeRte) return;
  const r = selectionIn(activeRte.ed);
  if (!r || r.collapsed) { toast('Сначала выделите текст'); return; }
  const w = wrapEl(kind, url);
  w.append(r.extractContents());
  r.insertNode(w);
  getSelection()?.removeAllRanges();
  activeRte.sync();
}

export function clearFormatting() {
  if (!activeRte) return;
  const r = selectionIn(activeRte.ed);
  if (!r || r.collapsed) {
    // no selection: unwrap everything
    const ed = activeRte.ed;
    const text = domToPlain(ed);
    ed.replaceChildren(document.createTextNode(text));
  } else {
    const text = domToPlain(r.cloneContents());
    r.deleteContents();
    r.insertNode(document.createTextNode(text));
  }
  activeRte.sync();
}

export function insertEntity(typeName) {
  if (!activeRte) return;
  const target = activeRte;
  const r = selectionIn(target.ed);
  const selected = r ? r.toString() : '';
  const v = defaultObject(typeName);
  if ('text' in v || T(typeName).fields.some((f) => f.name === 'text')) v.text = selected || v.text || (RT_LABELS[v.type] || v.type);
  if (v.type === 'mention' && selected) v.username = selected.replace(/^@/, '');
  if (v.type === 'hashtag' && selected) v.hashtag = selected.replace(/^#/, '');
  if (v.type === 'custom_emoji') v.alternative_text = selected || '⭐';
  if (v.type === 'mathematical_expression') v.expression = selected || 'E=mc^2';
  editEntity(v, (val) => {
    const chip = chipEl(val);
    if (r && target.ed.contains(r.startContainer)) { r.deleteContents(); r.insertNode(chip); } else target.ed.append(chip);
    target.sync();
  });
}

export function editEntity(value, onSave, onDelete) {
  const name = rtTypes().find((t) => t.const === value.type)?.name;
  if (!name) { editJson(value, onSave); return; }
  const v = clone(value);
  const actions = [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' })];
  if (onDelete) actions.unshift(h('button', { class: 'danger', type: 'button', text: 'Удалить', onclick: () => { closeDialog(); onDelete(); } }));
  actions.push(h('button', { type: 'button', text: 'Готово', onclick: () => { closeDialog(); onSave(v); } }));
  openDialog([h('h3', { text: RT_LABELS[value.type] || value.type }), h('p', { class: 'muted small', text: T(name).description }), objectFields(name, v, 5)], actions);
}

export function chipEl(v) {
  const label = v.text !== undefined ? plainRt(v.text) : v.alternative_text || v.expression || v.name || (v.button && plainRt(v.button.text)) || v.type;
  return h('span', { class: 'rt-chip', contenteditable: 'false', 'data-rt': JSON.stringify(v), 'data-kind': RT_LABELS[v.type] || v.type, text: label });
}

export function plainRt(rt) {
  if (rt === null || rt === undefined) return '';
  if (typeof rt === 'string') return rt;
  if (Array.isArray(rt)) return rt.map(plainRt).join('');
  return rt.text !== undefined ? plainRt(rt.text) : rt.alternative_text || rt.expression || '';
}

export function rtToDom(rt) {
  const frag = document.createDocumentFragment();
  if (rt === null || rt === undefined) return frag;
  if (typeof rt === 'string') { rt.split('\n').forEach((line, i) => { if (i) frag.append(h('br')); if (line) frag.append(document.createTextNode(line)); }); return frag; }
  if (Array.isArray(rt)) { rt.forEach((x) => frag.append(rtToDom(x))); return frag; }
  if (typeof rt === 'object') {
    if (RT_WRAP[rt.type] || rt.type === 'url') {
      const w = wrapEl(rt.type, rt.url);
      const { type, text, url, ...extra } = rt; // fields unknown to the editor survive editing
      if (Object.keys(extra).length) w.dataset.extra = JSON.stringify(extra);
      w.append(rtToDom(rt.text)); frag.append(w);
    } else frag.append(chipEl(rt));
  }
  return frag;
}

export function domToRt(root) {
  const pieces = [];
  const push = (p) => {
    if (p === '' || p === null || p === undefined) return;
    if (typeof p === 'string' && typeof pieces[pieces.length - 1] === 'string') pieces[pieces.length - 1] += p;
    else pieces.push(p);
  };
  root.childNodes.forEach((n, idx) => {
    if (n.nodeType === 3) { push(n.nodeValue.replace(/\u00a0/g, ' ')); return; }
    if (n.nodeType !== 1) return;
    const tag = n.tagName.toLowerCase();
    if (tag === 'br') { if (!(idx === root.childNodes.length - 1 && root.classList && root.classList.contains('rte'))) push('\n'); return; }
    if (n.classList.contains('rt-chip')) { try { push(JSON.parse(n.dataset.rt)); } catch { /* ignore */ } return; }
    const inner = domToRt(n);
    const kind = { b: 'bold', strong: 'bold', i: 'italic', em: 'italic', u: 'underline', s: 'strikethrough', strike: 'strikethrough', del: 'strikethrough', mark: 'marked', code: 'code', sub: 'subscript', sup: 'superscript' }[tag]
      || (tag === 'span' && n.classList.contains('rt-spoiler') ? 'spoiler' : null);
    let extra = {};
    try { extra = n.dataset.extra ? JSON.parse(n.dataset.extra) : {}; } catch { /* ignore */ }
    if (kind) { if (inner !== '') push({ type: kind, text: inner, ...extra }); return; }
    if (tag === 'a') { if (inner !== '') push({ type: 'url', text: inner, url: n.dataset.url || n.getAttribute('href') || '', ...extra }); return; }
    if (tag === 'div' || tag === 'p') { if (pieces.length) push('\n'); }
    if (Array.isArray(inner)) inner.forEach(push); else push(inner);
  });
  if (!pieces.length) return '';
  return pieces.length === 1 ? pieces[0] : pieces;
}

export function domToPlain(node) {
  let s = '';
  node.childNodes.forEach((n) => {
    if (n.nodeType === 3) s += n.nodeValue;
    else if (n.nodeType === 1) s += n.tagName === 'BR' ? '\n' : n.classList && n.classList.contains('rt-chip') ? n.textContent : domToPlain(n);
  });
  return s;
}
