// The source tabs (HTML, Markdown, JSON): CodeMirror 6 over each textarea (vendor/codemirror.js, docs/decisions/0017).
// The textarea stays the value: CodeMirror writes every change back to it with an input event, so code.js and
// editor.js read it and listen to it as before; text from elsewhere comes in through setSource(). CodeMirror loads
// on the first open of a source tab. Until then, when it cannot load, and with «Простой редактор» on, the textarea
// is the editor, and the draft saves as always.
import { htmlCatalog, htmlSource, jsonSource } from './completion.js';
import { h, store } from './dom.js';
import { S } from './state.js';

/** @typedef {typeof import('../vendor/codemirror.js')} CM */
/** @typedef {'html' | 'markdown' | 'json'} Lang */
/** @typedef {{ left: number, right: number, top: number, bottom: number }} Rect */
/** @typedef {{ ta: HTMLTextAreaElement, lang: Lang, box: HTMLElement, toggle: HTMLInputElement, note: HTMLElement, view: import('@codemirror/view').EditorView | null, cm: CM | null, starting: boolean }} Source */

const ENTRY = new URL('../vendor/codemirror.js', import.meta.url).href;
const LABELS = { html: 'Исходник HTML', markdown: 'Исходник Markdown', json: 'JSON поста' };
// the search panel and the completion list in Russian (CodeMirror's phrases)
const PHRASES = {
  Find: 'Найти', Replace: 'Заменить', next: 'далее', previous: 'назад', all: 'все', 'match case': 'с учётом регистра',
  'by word': 'слово целиком', regexp: 'рег. выражение', replace: 'заменить', 'replace all': 'заменить все', close: 'закрыть',
  'current match': 'текущее совпадение', 'replaced $ matches': 'заменено: $', 'replaced match on line $': 'заменено в строке $',
  'on line': 'в строке', 'Go to line': 'Перейти к строке', go: 'перейти', Completions: 'Подсказки',
};

/** @type {Map<HTMLTextAreaElement, Source>} */
const sources = new Map();
/** @type {Promise<CM> | null} */
let loading = null;
let failures = 0;
/** @type {import('./completion.js').HtmlCatalog | null} */
let catalog = null;
const plain = () => !!store.get('plainSource', false);

/** The vendored CodeMirror, loaded once. A failed load is not kept: the next call tries again, under another URL,
 * because the browser remembers a module that failed under its URL for the life of the page. */
export function loadCodeMirror() {
  loading ??= /** @type {Promise<CM>} */ (import(failures ? `${ENTRY}?retry=${failures}` : ENTRY)).catch((e) => {
    loading = null;
    failures++;
    throw e;
  });
  return loading;
}

/** A textarea of a source tab becomes CodeMirror when its tab is shown (showSource). */
export function setupSource(/** @type {HTMLTextAreaElement} */ ta, /** @type {Lang} */ lang) {
  const box = h('div', { class: 'cm-box', hidden: true });
  const toggle = /** @type {HTMLInputElement} */ (h('input', { type: 'checkbox', checked: plain(), onchange: (e) => setPlain(e.target.checked) }));
  const note = h('span', { class: 'muted small source-note' });
  ta.before(box);
  ta.after(h('div', { class: 'row source-tools' }, h('label', { class: 'row small', title: 'Обычное поле ввода без подсветки и подсказок: если на телефоне сбоит клавиатура' }, toggle, 'Простой редактор'), note));
  sources.set(ta, { ta, lang, box, toggle, note, view: null, cm: null, starting: false });
}

/** The tab of this source is shown: CodeMirror over its textarea, unless the plain editor is chosen. */
export function showSource(/** @type {string} */ lang) {
  for (const s of sources.values()) {
    if (s.lang !== lang) continue;
    if (s.view) s.view.requestMeasure();
    else if (!plain()) attach(s);
  }
}

/** Text from elsewhere (another draft, the post-wide undo, an applied JSON): the editor's own undo starts over. */
export function setSource(/** @type {HTMLTextAreaElement} */ ta, /** @type {string} */ text) {
  if (ta.value !== text) ta.value = text;
  const s = sources.get(ta);
  if (s?.view && s.cm && s.view.state.doc.toString() !== text) {
    s.view.setState(stateFor(s, s.cm, text, Math.min(s.view.state.selection.main.head, text.length)));
  }
}

/** A snippet at the cursor ($SEL$ takes the selection) when CodeMirror is the editor; false: use the textarea. */
export function insertSource(/** @type {HTMLTextAreaElement} */ ta, /** @type {string} */ snip) {
  const view = sources.get(ta)?.view;
  if (!view) return false;
  const { from, to } = view.state.selection.main;
  const selected = view.state.sliceDoc(from, to);
  const text = snip.replace('$SEL$', selected);
  const caret = snip.includes('$SEL$') && !selected ? from + snip.indexOf('$SEL$') : from + text.length;
  view.dispatch({ changes: { from, to, insert: text }, selection: { anchor: caret }, userEvent: 'input', scrollIntoView: true });
  view.focus();
  return true;
}

function setPlain(/** @type {boolean} */ on) {
  store.set('plainSource', on);
  for (const s of sources.values()) {
    s.toggle.checked = on;
    if (on) detach(s);
  }
  if (!on) showSource(S.mode);
}

async function attach(/** @type {Source} */ s) {
  if (s.starting) return;
  s.starting = true;
  try {
    const cm = await loadCodeMirror();
    if (plain() || s.view) return;
    const focused = document.activeElement === s.ta;
    s.cm = cm;
    s.view = new cm.view.EditorView({ parent: s.box, state: stateFor(s, cm, s.ta.value, s.ta.selectionStart) });
    s.ta.hidden = true;
    s.box.hidden = false;
    s.note.textContent = 'Ctrl+Пробел — подсказки, Ctrl+F — поиск и замена';
    if (focused) s.view.focus();
  } catch {
    // the textarea stays the editor and the draft saves as always; the next open of the tab tries again
    s.note.textContent = 'Подсветка не загрузилась, работает простое поле. Откройте вкладку ещё раз, чтобы попробовать снова.';
  } finally {
    s.starting = false;
  }
}

function detach(/** @type {Source} */ s) {
  s.view?.destroy();
  s.view = null;
  s.box.replaceChildren();
  s.box.hidden = true;
  s.ta.hidden = false;
  s.note.textContent = '';
}

/** @param {Source} s @param {CM} cm @param {string} doc @param {number} cursor */
function stateFor(s, cm, doc, cursor) {
  const { view: v, commands: c, language: l, autocomplete: a, search: f } = cm;
  const language = s.lang === 'json' ? cm.langJson.json()
    : s.lang === 'html' ? cm.langHtml.html()
      : cm.langMarkdown.markdown({ base: cm.langMarkdown.markdownLanguage, completeHTMLTags: false });
  // the studio's own suggestions instead of the languages' (lang-html would offer every HTML5 tag)
  const source = s.lang === 'json' ? jsonSource(l.syntaxTree, () => S.spec) : htmlSource(() => (catalog ??= htmlCatalog(/** @type {any} */ (window.SNIPPETS.html))));
  return cm.state.EditorState.create({
    doc,
    selection: { anchor: Math.max(0, Math.min(cursor, doc.length)) },
    extensions: [
      language, v.lineNumbers(), v.highlightActiveLineGutter(), v.highlightSpecialChars(), v.drawSelection(), v.dropCursor(),
      v.highlightActiveLine(), c.history(), l.indentOnInput(), l.bracketMatching(), l.indentUnit.of('  '), a.closeBrackets(),
      a.autocompletion({ override: [source], icons: false, positionInfo }), f.search({ top: true }), f.highlightSelectionMatches(),
      v.keymap.of([...a.closeBracketsKeymap, ...c.defaultKeymap, ...f.searchKeymap, ...c.historyKeymap, ...a.completionKeymap, c.indentWithTab]),
      v.EditorView.lineWrapping, cm.state.EditorState.phrases.of(PHRASES),
      v.EditorView.contentAttributes.of({ 'aria-label': LABELS[s.lang], spellcheck: 'false', autocorrect: 'off', autocapitalize: 'off', translate: 'no' }),
      s.ta.placeholder ? v.placeholder(s.ta.placeholder) : [],
      theme(cm),
      v.EditorView.updateListener.of((u) => {
        if (!u.docChanged) return;
        s.ta.value = u.state.doc.toString();
        s.ta.dispatchEvent(new Event('input'));
      }),
    ],
  });
}

/**
 * Where the description of the selected option goes: beside the list when there is room, else under it (or over it
 * near the bottom of the screen). CodeMirror's own narrow layout puts it over the next options, which a phone then
 * cannot tap. Coordinates are relative to the list.
 * @param {unknown} _view @param {Rect} list @param {Rect} option @param {Rect} info @param {Rect} space
 */
function positionInfo(_view, list, option, info, space) {
  const right = space.right - list.right;
  const left = list.left - space.left;
  if (Math.max(right, left) >= 220) {
    const side = right >= 220 ? 'right' : 'left';
    return { style: `top: ${option.top - list.top}px; max-width: ${Math.min(400, (side === 'right' ? right : left) - 6)}px`, class: 'cm-completionInfo-' + side };
  }
  const below = space.bottom - list.bottom >= info.bottom - info.top || space.bottom - list.bottom >= list.top - space.top;
  return { style: `${below ? 'top' : 'bottom'}: ${list.bottom - list.top}px; max-width: ${space.right - list.left - 6}px`, class: 'cm-completionInfo-under' };
}

// colours from the page's variables and color-scheme: the light and dark page need no second theme
function theme(/** @type {CM} */ cm) {
  const t = cm.lezerHighlight.tags;
  return [
    cm.view.EditorView.theme({
      '&': { color: 'var(--text)', backgroundColor: 'var(--panel)', border: '1px solid var(--border)', borderRadius: '6px', fontSize: '13px', maxHeight: '75vh' },
      '&.cm-focused': { outline: '2px solid color-mix(in srgb, var(--accent) 45%, transparent)', borderColor: 'var(--accent)' },
      '.cm-scroller': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', lineHeight: '1.5', overflow: 'auto' },
      '.cm-content, .cm-gutter': { minHeight: '380px' },
      '.cm-content': { caretColor: 'var(--text)' },
      '.cm-cursor, .cm-dropCursor': { borderLeftColor: 'var(--text)' },
      '.cm-gutters': { backgroundColor: 'var(--soft)', color: 'var(--muted)', border: 'none', borderRadius: '6px 0 0 6px' },
      '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'color-mix(in srgb, var(--soft) 60%, transparent)' },
      '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection': { backgroundColor: 'var(--chip)' },
      '.cm-tooltip, .cm-panels': { backgroundColor: 'var(--panel)', color: 'var(--text)', border: '1px solid var(--border)' },
      '.cm-tooltip-autocomplete > ul': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', maxWidth: 'calc(100vw - 16px)' },
      '.cm-tooltip-autocomplete > ul > li[aria-selected]': { backgroundColor: 'var(--accent)', color: 'var(--accent-text)' },
      '.cm-completionDetail': { color: 'var(--muted)', fontStyle: 'normal', marginLeft: '8px' },
      '.cm-tooltip-autocomplete > ul > li[aria-selected] .cm-completionDetail': { color: 'inherit', opacity: '.8' },
      // the description of an option: never wider than a phone's screen (positionInfo)
      '.cm-tooltip.cm-completionInfo': { boxSizing: 'border-box', whiteSpace: 'pre-line' },
      '.cm-tooltip.cm-completionInfo.cm-completionInfo-under': { left: '0' },
      '.cm-panel input, .cm-panel button, .cm-textfield, .cm-button': { color: 'var(--text)', backgroundColor: 'var(--panel)', backgroundImage: 'none', border: '1px solid var(--border)', borderRadius: '6px' },
      '.cm-panel.cm-search': { fontSize: '13px' },
    }),
    cm.language.syntaxHighlighting(cm.language.HighlightStyle.define([
      { tag: [t.tagName, t.heading], color: 'light-dark(#176b9d, #79c5ff)' },
      { tag: [t.attributeName, t.propertyName], color: 'light-dark(#8c4d14, #f2bd7d)' },
      { tag: [t.string, t.attributeValue, t.url], color: 'light-dark(#25713d, #8bd9a0)' },
      { tag: [t.number, t.bool, t.null, t.keyword], color: 'light-dark(#7c3aad, #d5a3ff)' },
      { tag: [t.comment, t.meta, t.processingInstruction, t.angleBracket], color: 'var(--muted)' },
      { tag: t.strong, fontWeight: 'bold' }, { tag: t.heading, fontWeight: 'bold' }, { tag: t.emphasis, fontStyle: 'italic' },
      { tag: t.strikethrough, textDecoration: 'line-through' }, { tag: t.link, textDecoration: 'underline' },
      { tag: t.monospace, color: 'light-dark(#7c3aad, #d5a3ff)' },
    ])),
  ];
}
