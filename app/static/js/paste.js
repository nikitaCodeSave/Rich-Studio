// Clipboard HTML (Google Docs, Notion, a web page) -> RichText. Pure: no editor state, no DOM of the page.
// The HTML is parsed into an inert document (scripts never run, images never load) and only the formatting
// RichText has survives: bold, italic, underline, strike, code, sub/sup, marker, spoiler and links.

const SKIP = new Set(['script', 'style', 'head', 'title', 'meta', 'link', 'template', 'noscript', 'iframe', 'object', 'svg', 'button', 'select', 'textarea']);
const BLOCK = new Set(['p', 'div', 'section', 'article', 'header', 'footer', 'aside', 'main', 'nav', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'blockquote', 'pre', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'dl', 'dt', 'dd', 'figure', 'figcaption', 'hr', 'address']);
const TAG_MARK = { b: 'bold', strong: 'bold', i: 'italic', em: 'italic', cite: 'italic', u: 'underline', ins: 'underline', s: 'strikethrough',
  strike: 'strikethrough', del: 'strikethrough', code: 'code', kbd: 'code', samp: 'code', tt: 'code', sub: 'subscript', sup: 'superscript',
  mark: 'marked', 'tg-spoiler': 'spoiler' };
const SAFE_URL = /^(https?:|tg:|mailto:)/i;

/**
 * @param {string} html clipboard text/html
 * @returns {{ rt: any, images: number }} RichText ('' when nothing is left) and how many pictures were dropped
 */
export function htmlToRt(html) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  /** @type {{ text?: string, nl?: boolean, marks?: any[] }[]} */
  const runs = [];
  let images = 0;
  const walk = (/** @type {Node} */ n, /** @type {any[]} */ marks, /** @type {boolean} */ pre, /** @type {string} */ list) => {
    if (n.nodeType === 3) {
      const t = pre ? n.nodeValue || '' : (n.nodeValue || '').replace(/\s+/g, ' ');
      if (t) runs.push({ text: t, marks });
      return;
    }
    if (n.nodeType !== 1) return;
    const el = /** @type {Element} */ (n);
    const tag = el.tagName.toLowerCase();
    if (SKIP.has(tag)) return;
    if (tag === 'img') { images++; return; }
    if (tag === 'br') { runs.push({ nl: true }); return; }
    const block = BLOCK.has(tag);
    if (block) runs.push({ nl: true });
    const next = marks.slice();
    const add = (m) => { if (!next.some((x) => x.type === m.type)) next.push(m); };
    const style = (el.getAttribute('style') || '').toLowerCase();
    const weight = /font-weight\s*:\s*(\w+)/.exec(style)?.[1] || '';
    const heavy = weight === 'bold' || weight === 'bolder' || +weight >= 600;
    const light = weight === 'normal' || weight === 'lighter' || (+weight > 0 && +weight < 600);
    // Google Docs wraps the whole clipboard in <b style="font-weight:normal">
    if ((TAG_MARK[tag] === 'bold' && !light) || heavy) add({ type: 'bold' });
    if (TAG_MARK[tag] && TAG_MARK[tag] !== 'bold') add({ type: TAG_MARK[tag] });
    if (/font-style\s*:\s*italic/.test(style)) add({ type: 'italic' });
    if (/text-decoration[\w-]*\s*:[^;]*underline/.test(style) && tag !== 'a') add({ type: 'underline' });
    if (/text-decoration[\w-]*\s*:[^;]*line-through/.test(style)) add({ type: 'strikethrough' });
    if (/vertical-align\s*:\s*super/.test(style)) add({ type: 'superscript' });
    if (/vertical-align\s*:\s*sub/.test(style)) add({ type: 'subscript' });
    if (tag === 'span' && el.classList.contains('tg-spoiler')) add({ type: 'spoiler' });
    if (tag === 'a') { const href = (el.getAttribute('href') || '').trim(); if (SAFE_URL.test(href)) add({ type: 'url', url: href }); }
    if (/^h[1-6]$/.test(tag)) add({ type: 'bold' });
    if (tag === 'li') {
      const nth = list === 'ol' ? [...(el.parentElement?.children || [])].filter((x) => x.tagName === 'LI').indexOf(el) + 1 : 0;
      runs.push({ text: nth ? nth + '. ' : '• ', marks: [] });
    }
    const inPre = pre || tag === 'pre';
    const inList = tag === 'ul' || tag === 'ol' ? tag : list;
    el.childNodes.forEach((c) => walk(c, next, inPre, inList));
    if (tag === 'td' || tag === 'th') runs.push({ text: '\t', marks: [] });
    if (block) runs.push({ nl: true });
  };
  walk(doc.body, [], false, '');
  return { rt: build(tidy(runs), 0), images };
}

// block boundaries become one line break; spaces around breaks and repeated spaces go
function tidy(runs) {
  const out = [];
  let pendingNl = false;
  for (const r of runs) {
    if (r.nl) { pendingNl = out.length > 0; continue; }
    let text = r.text;
    if (pendingNl) { text = text.replace(/^[ \t]+/, ''); if (!text) continue; trimEnd(out); out.push({ text: '\n', marks: [] }); pendingNl = false; }
    const prev = out[out.length - 1];
    if (prev && /[ \n]$/.test(prev.text)) text = text.replace(/^ +/, '');
    if (!prev) text = text.replace(/^\s+/, '');
    if (text) out.push({ text, marks: r.marks });
  }
  trimEnd(out);
  return out;
}

function trimEnd(out) {
  while (out.length) {
    const last = out[out.length - 1];
    last.text = last.text.replace(/[ \t]+$/, '');
    if (last.text) return;
    out.pop();
  }
}

const sameMark = (a, b) => a && b && a.type === b.type && a.url === b.url;

// runs with the same outer mark share one entity, so <b>a<i>b</i></b> stays one bold piece
function build(runs, depth) {
  /** @type {any[]} */
  const pieces = [];
  for (let i = 0; i < runs.length;) {
    const m = runs[i].marks[depth];
    if (!m) {
      if (typeof pieces[pieces.length - 1] === 'string') pieces[pieces.length - 1] += runs[i].text;
      else pieces.push(runs[i].text);
      i++;
      continue;
    }
    let j = i;
    while (j < runs.length && sameMark(runs[j].marks[depth], m)) j++;
    const text = build(runs.slice(i, j), depth + 1);
    if (text !== '') pieces.push(m.url ? { type: 'url', text, url: m.url } : { type: m.type, text });
    i = j;
  }
  if (!pieces.length) return '';
  return pieces.length === 1 ? pieces[0] : pieces;
}

/** Spreadsheet clipboard as rows of cells, or null when it is not a table (one cell, or no tabs and no <table>). */
export function clipboardTable(text, html) {
  if (!text) return null;
  const rows = text.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n').map((r) => r.split('\t'));
  const isTable = rows.some((r) => r.length > 1) || (/<table[\s>]/i.test(html || '') && rows.length > 1);
  return isTable ? rows : null;
}
