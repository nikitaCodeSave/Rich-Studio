// HTML and Markdown modes: snippet bars and textarea helpers.
import { $, h } from './dom.js';
import { changed } from './editor.js';
import { addMediaEntry } from './media.js';
import { S } from './state.js';

// ---------- code modes ----------
export function insertAtCursor(ta, snip) {
  const [a, b] = [ta.selectionStart, ta.selectionEnd];
  const sel = ta.value.slice(a, b);
  const text = snip.replace('$SEL$', sel);
  const caret = snip.includes('$SEL$') && !sel ? a + snip.indexOf('$SEL$') : a + text.length;
  ta.setRangeText(text, a, b, 'end');
  ta.focus();
  ta.setSelectionRange(caret, caret);
  ta.dispatchEvent(new Event('input'));
}

export function snippetBar(kind) {
  const bar = $('#snip-' + kind);
  bar.replaceChildren();
  (window.SNIPPETS[kind] || []).forEach((g) => {
    bar.append(h('div', { class: 'snip-group' }, h('span', { text: g.group }), g.items.map((it) => h('button', {
      type: 'button', class: it.verified === false ? 'unverified' : '', text: it.label,
      title: (it.title || it.label) + (it.verified === false ? '\n⚠ синтаксис из сторонних источников, проверьте на тестовом канале' : ''),
      onclick: () => {
        let snip = it.snip;
        if (it.media) snip = snip.replace('$ID$', addMediaEntry(it.media, ''));
        insertAtCursor($('#src-' + kind), snip);
      },
    }))));
  });
}

export function bindCode(kind) {
  const ta = $('#src-' + kind);
  ta.addEventListener('input', () => { S.buf[kind] = ta.value; changed(); });
  ta.addEventListener('keydown', (e) => { if (e.key === 'Tab') { e.preventDefault(); ta.setRangeText('  ', ta.selectionStart, ta.selectionEnd, 'end'); ta.dispatchEvent(new Event('input')); } });
}
