/* Rich Studio preview renderer.
 * Draws a post (rich_message as html / markdown / blocks) the way Telegram
 * lays it out, as closely as a browser can. Unknown blocks, entities and
 * tg-* tags are still shown (dashed outline) so features newer than this
 * renderer never disappear from the preview.
 * Usage: RichRender.render(post, {mediaBase: ''}) -> HTMLElement
 */
(function (global) {
  'use strict';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** @returns {HTMLElement} */
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return e;
  }

  // ---------- media ----------
  function mediaSrc(media, ctx) {
    if (!media) return null;
    const m = typeof media === 'string' ? media : media.media;
    if (typeof m !== 'string') return null;
    // one file in the media folder: «upload:..» was /media/.., the page itself
    if (m.startsWith('upload:')) return /^\.{0,2}$|[/\\]/.test(m.slice(7)) ? null : (ctx.mediaBase || '') + '/media/' + encodeURIComponent(m.slice(7));
    if (/^https?:\/\//i.test(m) && fileUrl(m)) return m;
    return null; // file_id or attach:// cannot be previewed
  }

  function mediaEl(kind, media, ctx, extra) {
    const src = mediaSrc(media, ctx);
    const label = typeof media === 'object' && media ? media.media : media;
    const spoiler = media && media.has_spoiler;
    let node;
    if (!src) {
      node = el('div', { class: 'tg-media-ph' }, el('span', { class: 'tg-media-kind', text: kindLabel(kind) }), el('small', { text: label || 'нет файла' }));
    } else if (kind === 'photo' || (kind === 'animation' && /\.gif(?:[?#]|$)/i.test(src))) {
      // a GIF plays only in <img>; Telegram shows an animation playing by itself in a loop, without controls
      node = el('img', { src, alt: '', loading: 'lazy' });
    } else if (kind === 'video' || kind === 'animation') {
      node = el('video', { src, controls: kind === 'video', autoplay: kind === 'animation', loop: kind === 'animation', playsinline: true });
      // autoplay policy looks at the property; the muted attribute only sets the default after parsing
      node.defaultMuted = node.muted = true;
    } else if (kind === 'audio' || kind === 'voice_note' || kind === 'voice') {
      node = el('audio', { src, controls: true });
    } else {
      node = el('a', { class: 'tg-doc', href: src, target: '_blank', rel: 'noopener' }, el('span', { class: 'tg-doc-ico', text: '📄' }), (() => { const last = src.split('/').pop() || ''; try { return decodeURIComponent(last); } catch (e) { return last; } })());
    }
    // tg-gif: the «GIF» label over a playing animation (the placeholder names the kind by itself)
    const wrap = el('div', { class: 'tg-media tg-media-' + kind + (src && kind === 'animation' ? ' tg-gif' : '') + (spoiler ? ' tg-media-spoiler' : '') }, node);
    if (spoiler) wrap.addEventListener('click', () => wrap.classList.remove('tg-media-spoiler'));
    if (extra) wrap.append(extra);
    return wrap;
  }

  function kindLabel(k) {
    return ({ photo: 'Фото', video: 'Видео', animation: 'GIF', audio: 'Аудио', voice_note: 'Голосовое', voice: 'Голосовое', document: 'Файл' })[k] || k;
  }

  // ---------- math ----------
  function math(expr, display) {
    const span = el(display ? 'div' : 'span', { class: display ? 'tg-math-block' : 'tg-math' });
    if (global.katex) {
      try { global.katex.render(expr, span, { displayMode: !!display, throwOnError: false }); return span; } catch (e) { /* fall through */ }
    }
    span.textContent = expr;
    return span;
  }

  // ---------- date/time ----------
  function formatTime(unix, fmt) {
    const d = new Date(unix * 1000);
    if (isNaN(d.getTime())) return '';
    fmt = fmt || '';
    /** @type {Intl.DateTimeFormatOptions} */
    const opts = {};
    if (/w/.test(fmt)) opts.weekday = 'short';
    if (/D/.test(fmt)) { opts.day = 'numeric'; opts.month = 'long'; opts.year = 'numeric'; } else if (/d/.test(fmt)) { opts.day = '2-digit'; opts.month = '2-digit'; opts.year = 'numeric'; }
    if (/T/.test(fmt)) { opts.hour = '2-digit'; opts.minute = '2-digit'; opts.second = '2-digit'; } else if (/t/.test(fmt)) { opts.hour = '2-digit'; opts.minute = '2-digit'; }
    if (/r/.test(fmt)) {
      const diff = Math.round((d.getTime() - Date.now()) / 1000);
      const rtf = new Intl.RelativeTimeFormat('ru', { numeric: 'auto' });
      const a = Math.abs(diff);
      if (a < 60) return rtf.format(diff, 'second');
      if (a < 3600) return rtf.format(Math.round(diff / 60), 'minute');
      if (a < 86400) return rtf.format(Math.round(diff / 3600), 'hour');
      return rtf.format(Math.round(diff / 86400), 'day');
    }
    if (!Object.keys(opts).length) { opts.dateStyle = 'medium'; opts.timeStyle = 'short'; }
    return d.toLocaleString('ru-RU', opts);
  }

  // ---------- buttons ----------
  // a class name made of a string of the post: one word or nothing, else «x tg-cell-empty» puts any class of tg.css on
  // the element (one that hides it, too)
  const word = (v) => (typeof v === 'string' && /^[a-z_]+$/.test(v) ? v : '');

  function buttonEl(b, ctx) {
    if (!b || typeof b !== 'object') return null;
    const action = ['url', 'callback_data', 'web_app', 'login_url', 'switch_inline_query', 'switch_inline_query_current_chat', 'switch_inline_query_chosen_chat', 'copy_text', 'disabled', 'pay', 'callback_game'].find((k) => b[k] !== undefined);
    const icon = { url: '↗', copy_text: '⧉', switch_inline_query: '↪', switch_inline_query_chosen_chat: '↪', login_url: '🔑', web_app: '▣' }[action] || '';
    const title = action === 'url' ? b.url : action === 'copy_text' ? 'Копирует: ' + (b.copy_text && b.copy_text.text) : action === 'callback_data' ? 'callback: ' + b.callback_data : action || 'без действия';
    const btn = el('span', { class: 'tg-btn' + (word(b.style) ? ' tg-btn-' + b.style : '') + (action === 'disabled' ? ' tg-btn-disabled' : ''), title },
      renderRT(b.text, ctx), icon ? el('span', { class: 'tg-btn-ico', text: icon }) : null);
    // the scheme as the check reads it (safeUrl gives the address back as written, a space in front included)
    const go = action === 'url' && typeof b.url === 'string' && safeUrl(b.url) ? asRead(b.url) : '';
    if (/^https?:\/\//i.test(go)) btn.addEventListener('click', () => window.open(go, '_blank', 'noopener'));
    return btn;
  }

  // ---------- RichText ----------
  const WRAP = { bold: 'b', italic: 'i', underline: 'u', strikethrough: 's', subscript: 'sub', superscript: 'sup', marked: 'mark', code: 'code' };

  function renderRT(rt, ctx) {
    const frag = document.createDocumentFragment();
    if (rt === null || rt === undefined) return frag;
    if (typeof rt === 'string') {
      rt.split('\n').forEach((line, i) => { if (i) frag.append(el('br')); frag.append(document.createTextNode(line)); });
      return frag;
    }
    if (Array.isArray(rt)) { rt.forEach((x) => frag.append(renderRT(x, ctx))); return frag; }
    if (typeof rt !== 'object') return frag;
    const t = rt.type;
    const inner = () => renderRT(rt.text, ctx);
    if (WRAP[t]) frag.append(el(WRAP[t], {}, inner()));
    else if (t === 'spoiler') frag.append(spoilerEl(inner()));
    else if (t === 'url') frag.append(linkDropped(rt.url) ? inner() : el('a', { href: safeUrl(tgAddress(rt.url)), target: '_blank', rel: 'noopener', title: rt.url }, inner()));
    else if (t === 'text_mention') frag.append(el('a', { class: 'tg-link', title: 'id ' + (rt.user && rt.user.id) }, inner()));
    else if (t === 'email_address') frag.append(el('a', { href: 'mailto:' + rt.email_address }, inner()));
    else if (t === 'phone_number') frag.append(el('a', { href: 'tel:' + rt.phone_number }, inner()));
    else if (['mention', 'hashtag', 'cashtag', 'bot_command', 'bank_card_number'].includes(t)) frag.append(el('span', { class: 'tg-link' }, inner()));
    else if (t === 'ton_address') frag.append(tonEl(inner(), rt.address || '')); // Bot API 10.4
    else if (t === 'date_time') frag.append(el('span', { class: 'tg-time', title: formatTime(rt.unix_time, 'wDT') + ' (у читателя: его часовой пояс)' }, rt.text ? inner() : formatTime(rt.unix_time, rt.date_time_format)));
    else if (t === 'custom_emoji') frag.append(el('span', { class: 'tg-emoji', title: 'custom emoji ' + rt.custom_emoji_id }, rt.alternative_text || '⭐'));
    else if (t === 'mathematical_expression') frag.append(math(rt.expression || '', false));
    else if (t === 'anchor') frag.append(el('a', { name: rt.name, id: 'anchor-' + rt.name, class: 'tg-anchor' }));
    else if (t === 'anchor_link') frag.append(el('a', { href: '#anchor-' + (rt.anchor_name || ''), class: 'tg-link' }, inner()));
    else if (t === 'reference') frag.append(el('span', { class: 'tg-ref', id: 'ref-' + rt.name }, inner()));
    else if (t === 'reference_link') frag.append(el('a', { href: '#ref-' + rt.reference_name, class: 'tg-reflink' }, inner()));
    else if (t === 'button') frag.append(buttonEl(rt.button, ctx) || '');
    else frag.append(unknown('текст: ' + t, rt.text !== undefined ? inner() : JSON.stringify(rt)));
    return frag;
  }

  // a TON address reads as a link; the address itself only in the title
  const tonEl = (content, address) => el('span', { class: 'tg-link tg-ton', title: 'Адрес TON: ' + address }, content);

  function spoilerEl(content) {
    const s = el('span', { class: 'tg-spoiler' }, content);
    s.addEventListener('click', () => s.classList.toggle('tg-spoiler-open'));
    return s;
  }

  function unknown(label, content) {
    return el('span', { class: 'tg-unknown', title: 'Этого элемента нет в рендере предпросмотра, но Telegram его получит: ' + label }, content);
  }

  // ---------- blocks ----------
  function caption(cap, ctx) {
    if (!cap) return null;
    if (typeof cap === 'object' && !Array.isArray(cap) && 'text' in cap && !cap.type) {
      return el('figcaption', {}, renderRT(cap.text, ctx), cap.credit ? el('cite', {}, renderRT(cap.credit, ctx)) : null);
    }
    return el('figcaption', {}, renderRT(cap, ctx));
  }

  function renderBlocks(blocks, ctx) {
    const frag = document.createDocumentFragment();
    (blocks || []).forEach((b) => frag.append(renderBlock(b, ctx)));
    return frag;
  }

  function renderBlock(b, ctx) {
    if (!b || typeof b !== 'object') return document.createTextNode('');
    const rt = (v) => renderRT(v, ctx);
    switch (b.type) {
      case 'paragraph': return el('p', {}, rt(b.text));
      case 'heading': return el('h' + Math.min(6, Math.max(1, b.size || 1)), {}, rt(b.text));
      case 'pre': return codeBox(el('pre', { 'data-lang': b.language || null }, el('code', {}, rt(b.text))), b.language);
      case 'footer': return el('footer', {}, rt(b.text));
      case 'divider': return el('hr');
      case 'mathematical_expression': return math(b.expression || '', true);
      case 'anchor': return el('a', { name: b.name, id: 'anchor-' + b.name, class: 'tg-anchor' });
      case 'thinking': return el('div', { class: 'tg-thinking' }, rt(b.text || 'Думаю…'));
      case 'list': {
        const items = b.items || [];
        const ordered = items.some((it) => it.value !== undefined || it.type);
        const list = el(ordered ? 'ol' : 'ul', {});
        items.forEach((it) => {
          const li = el('li', { value: it.value, type: it.type });
          if (it.has_checkbox) { li.classList.add('tg-task'); li.append(el('input', { type: 'checkbox', checked: !!it.is_checked, disabled: true })); }
          li.append(renderBlocks(it.blocks, ctx));
          list.append(li);
        });
        return list;
      }
      case 'blockquote': return el('blockquote', {}, renderBlocks(b.blocks, ctx), b.credit ? el('cite', {}, rt(b.credit)) : null);
      case 'expandable_blockquote': return expandable(el('div', {}, rt(b.text)), b.credit ? el('cite', {}, rt(b.credit)) : null);
      case 'pullquote': return el('aside', { class: 'tg-pullquote' }, el('div', {}, rt(b.text)), b.credit ? el('cite', {}, rt(b.credit)) : null);
      case 'collage': return el('figure', { class: 'tg-collage' }, el('div', { class: 'tg-collage-grid' }, renderBlocks(b.blocks, ctx)), caption(b.caption, ctx));
      case 'slideshow': return slideshow(renderBlocks(b.blocks, ctx), caption(b.caption, ctx));
      case 'table': return table(b, ctx);
      case 'details': {
        const d = el('details', { open: !!b.is_open }, el('summary', {}, rt(b.summary)), el('div', { class: 'tg-details-body' }, renderBlocks(b.blocks, ctx)));
        return d;
      }
      case 'map': return el('figure', { class: 'tg-map-fig' }, mapEl(b.location && b.location.latitude, b.location && b.location.longitude, b.zoom, b.width, b.height), caption(b.caption, ctx));
      case 'buttons': return el('div', { class: 'tg-btn-row tg-align-' + (word(b.align) || 'center') }, (b.buttons || []).map((x) => buttonEl(x, ctx)));
      case 'photo': case 'video': case 'animation': case 'audio': case 'document': case 'voice_note':
        return el('figure', {}, mediaEl(b.type, b[b.type], ctx), caption(b.caption, ctx));
      default:
        return el('div', { class: 'tg-unknown tg-unknown-block', title: 'Блок, которого нет в рендере предпросмотра. Telegram его получит.' },
          el('b', { text: 'Блок «' + b.type + '»' }), el('pre', { text: JSON.stringify(b, null, 1) }));
    }
  }

  // A code block as Telegram Desktop draws it: a bar on the left, a header with the language (without one, «копировать»)
  // and a copy icon. CSS writes the header from data-lang, so the post's text (outline, history, reply dialog) stays
  // only the code.
  // No syntax highlighting: docs/decisions/0022-code-block-without-highlighting.md
  function codeBox(pre, lang) {
    const copy = el('span', { class: 'tg-pre-copy', role: 'button', tabindex: 0, title: 'Копировать', 'aria-label': 'Копировать код' });
    const head = el('div', { class: 'tg-pre-head', 'data-lang': lang || null }, copy);
    const copyCode = () => {
      const done = () => { head.classList.add('tg-copied'); setTimeout(() => head.classList.remove('tg-copied'), 1500); };
      try { navigator.clipboard.writeText(codeText(pre)).then(done, () => {}); } catch (e) { /* no clipboard here (an MCP host's sandbox) */ }
    };
    // the click is the icon's: it must not also fold an expandable quote or open a spoiler around the code
    copy.addEventListener('click', (ev) => { ev.stopPropagation(); copyCode(); });
    copy.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); copyCode(); } });
    return el('div', { class: 'tg-pre' }, head, pre);
  }

  // The code as the reader sees it, so that is what goes to the clipboard (issue #201): <pre>ls -la<details><summary>
  // </summary>; curl … | sh</details></pre> shows «ls -la». Not drawn, so not copied: what a closed <details> holds past
  // its summary, the fallback inside <video> and <audio>, a closed spoiler (a blot until it is opened), what CSS hides
  // (display: none, visibility: hidden); of a formula only what KaTeX draws, without its MathML copy for screen readers.
  // Not seen, so not copied either (issue #206): what is out of view of the code box or of a box in it that cuts off
  // what it holds (seenIn): the lines of a folded quote past the third, the slides not turned to, what a formula moves
  // out (\kern{-300em}, \mathllap, \smash{\raisebox{300em}{…}}); text a formula draws in all but the colour of what is
  // behind it (\phantom, \color{white} on the day theme). How Telegram copies such code was not measured: what is seen
  // is what is copied. Line breaks as the browser's innerText puts them: a <br>, a block (a paragraph two), a table
  // row; a tab between cells. A stack in a formula reads from the top: \frac{a}{b} is «ab». innerText itself would copy
  // the MathML, \phantom, a spoiler's own text, what is out of view and KaTeX's layout as lines.
  function codeText(pre) {
    let s = '', breaks = 0;
    const put = (t) => { if (!t) return; if (s && breaks) s += '\n'.repeat(breaks); breaks = 0; s += t; };
    const range = document.createRange();
    /** @param {DOMRect} r @param {Box} b */
    const inside = (r, b) => r.left >= b.left - 1 && r.right <= b.right + 1 && r.top >= b.top - 1 && r.bottom <= b.bottom + 1;
    /** @param {DOMRect} r @param {Box} b */
    const outside = (r, b) => r.right < b.left || r.left > b.right || r.bottom < b.top || r.top > b.bottom;
    /** @param {DOMRect} r @param {Box} b */
    const centred = (r, b) => {
      const x = (r.left + r.right) / 2, y = (r.top + r.bottom) / 2;
      return x >= b.left && x <= b.right && y >= b.top && y <= b.bottom;
    };
    // what of a text is in view: all of it or none at once, else by halves down to a character, which is in view by its
    // middle; a text the browser lays out nowhere is not drawn. Each look costs as much as the whole text: a code box
    // that cuts a few lines (a folded quote) takes tens of looks, past `looks` what is left undecided is not copied, so
    // thousands of lines across the edge of a slide do not hang the page (issue #198)
    let looks = 2000;
    /** @param {Text} t @param {Box} box */
    const seenText = (t, box) => {
      const part = (i, j) => {
        if (j - i < t.data.length && --looks < 0) return '';
        range.setStart(t, i);
        range.setEnd(t, j);
        const rects = [...range.getClientRects()];
        if (!rects.length) return '';
        if (rects.every((r) => inside(r, box))) return t.data.slice(i, j);
        if (j - i === 1) return rects.some((r) => centred(r, box)) ? t.data.slice(i, j) : '';
        if (rects.every((r) => outside(r, box))) return '';
        let m = (i + j) >> 1;
        if (/[\udc00-\udfff]/.test(t.data[m]) && m + 1 < j) m++; // not between the halves of one character
        return part(i, m) + part(m, j);
      };
      return part(0, t.data.length);
    };
    /** @param {Node} n @param {boolean} math @param {Box} box what of the page is seen there @param {number[]} behind the background there */
    const walk = (n, math, box, behind) => {
      if (n.nodeType === 3) {
        const css = getComputedStyle(/** @type {Element} */ (n.parentNode));
        // in a formula the post sets the colours (\color, \colorbox)
        if (/^(hidden|collapse)$/.test(css.visibility) || (math && faint(css.color, behind))) return;
        const t = seenText(/** @type {Text} */ (n), box);
        // KaTeX puts zero-width spaces between the rows of a superscript or a fraction
        put(math ? t.replace(/\u200b/g, '') : t);
        return;
      }
      const e = /** @type {HTMLElement} */ (n);
      if (n.nodeType !== 1 || e.localName === 'video' || e.localName === 'audio' || e.classList.contains('katex-mathml')) return;
      if (e.classList.contains('tg-spoiler') && !e.classList.contains('tg-spoiler-open')) return;
      // the <br>'s own rects: a range round it is found by its place among its parent's children, from the first one
      // each time (6000 lines of code in blocks are 6000 <br>)
      if (e.localName === 'br') {
        if ([...e.getClientRects()].some((r) => centred(r, box))) put('\n');
        return;
      }
      const css = getComputedStyle(e);
      if (css.display === 'none') return;
      // KaTeX lays a formula out with blocks and tables of its own: it reads as one line of what it draws
      const lines = math ? 0 : e.localName === 'p' ? 2 : css.display && !/^(inline|contents|table-cell|ruby)/.test(css.display) ? 1 : 0;
      breaks = Math.max(breaks, lines);
      const inner = math || e.classList.contains('katex-html');
      // the background from the code box down, element by element: looked up from each formula, it went through all the
      // tags above it every time
      let under = layer(css.backgroundColor, behind);
      // \colorbox and \fcolorbox are a layer of their own in the stack that holds them, under what they hold
      const colorbox = math && e.classList.contains('vlist') && e.querySelector(':scope > * > .colorbox, :scope > * > .fcolorbox');
      if (colorbox) under = layer(getComputedStyle(colorbox).backgroundColor, under);
      /** @type {Node[]} */
      let kids = [...e.childNodes];
      if (e.localName === 'details' && !(/** @type {HTMLDetailsElement} */ (e)).open) kids = kids.filter((c) => c.nodeName === 'SUMMARY').slice(0, 1);
      // KaTeX writes a stack (a fraction, limits, a superscript over a subscript) from the bottom up
      else if (math && e.classList.contains('vlist')) {
        const top = new Map(kids.map((c) => [c, c.nodeType === 1 ? /** @type {Element} */ (c).getBoundingClientRect().top : 0]));
        kids.sort((a, b) => (top.get(a) || 0) - (top.get(b) || 0));
      }
      const seen = seenIn(box, e, css);
      kids.forEach((c) => walk(c, inner, seen, under));
      breaks = Math.max(breaks, lines);
      if (!math && css.display === 'table-cell' && e.nextElementSibling) put('\t');
    };
    // what of the code is seen: the boxes of the post around it cut it too (code in a folded quote), then the code box
    // itself
    const around = [];
    for (let a = pre.parentElement; a && !a.classList.contains('tg-body'); a = a.parentElement) around.unshift(a);
    const box = [...around, pre].reduce((b, a) => seenIn(b, a, getComputedStyle(a)), ALL);
    const behind = backdrop(pre);
    pre.childNodes.forEach((c) => walk(c, false, box, behind));
    return s;
  }

  /** @typedef {{ left: number, right: number, top: number, bottom: number }} Box */
  const ALL = { left: -Infinity, right: Infinity, top: -Infinity, bottom: Infinity };
  const NONE = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
  // What of the page is seen inside an element, from what is seen around it (box). A box that cuts off what it holds
  // shows it in its padding box only; one the reader scrolls shows besides all it scrolls to there. Never what is before
  // the start of its scrolling: left of a box written left to right, right of one written right to left, above any. A
  // slideshow turns its slides: the one in view is seen, not all it scrolls to
  /** @param {Box} box @param {HTMLElement} e @param {CSSStyleDeclaration} css @returns {Box} */
  function seenIn(box, e, css) {
    if (css.overflowX === 'visible' && css.overflowY === 'visible') return box;
    const r = e.getBoundingClientRect();
    const x = r.left + e.clientLeft, y = r.top + e.clientTop;
    const b = { ...box };
    if (css.overflowX !== 'visible') { b.left = Math.max(b.left, x); b.right = Math.min(b.right, x + e.clientWidth); }
    if (css.overflowY !== 'visible') { b.top = Math.max(b.top, y); b.bottom = Math.min(b.bottom, y + e.clientHeight); }
    if (b.left > b.right || b.top > b.bottom) return NONE; // nothing of it is seen: nothing to scroll into view either
    const scrolls = (v) => /^(auto|scroll)$/.test(v) && !e.classList.contains('tg-slides');
    if (scrolls(css.overflowX)) {
      const more = e.scrollWidth - e.clientWidth;
      if (css.direction === 'rtl') { b.left -= more + e.scrollLeft; b.right -= e.scrollLeft; } else { b.left -= e.scrollLeft; b.right += more - e.scrollLeft; }
    }
    if (scrolls(css.overflowY)) { b.top -= e.scrollTop; b.bottom += e.scrollHeight - e.clientHeight - e.scrollTop; }
    return b;
  }

  // A colour as CSS gives it, [r, g, b, alpha]: a colour of KaTeX is a name or #hex, so rgb() or rgba()
  /** @param {string} c @returns {number[] | null} */
  const rgba = (c) => {
    const m = /^rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)$/.exec(c);
    return m ? [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]] : null;
  };
  /** a colour laid over an opaque one @param {string | number[] | null} top @param {number[]} under */
  const layer = (top, under) => {
    const c = typeof top === 'string' ? rgba(top) : top;
    return c && c[3] ? [0, 1, 2].map((i) => c[i] * c[3] + under[i] * (1 - c[3])).concat(1) : under;
  };
  /** the background behind an element: its own and its parents' over the white of the page @param {Element | null} e */
  function backdrop(e) {
    const layers = [];
    for (; e; e = e.parentElement) {
      const c = rgba(getComputedStyle(e).backgroundColor);
      if (c && c[3]) layers.push(c);
      if (c && c[3] === 1) break;
    }
    return layers.reduceRight((under, c) => layer(c, under), [255, 255, 255, 1]);
  }
  /** @param {number[]} c */
  const luminance = (c) => [0.2126, 0.7152, 0.0722].reduce((sum, k, i) => {
    const v = c[i] / 255;
    return sum + k * (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  }, 0);
  // text in all but the colour behind it: a contrast under 1.5:1 (WCAG's ratio; white on the code of the day theme is
  // 1.2, gray 3.4, transparent 1)
  /** @param {string} color @param {number[]} behind */
  function faint(color, behind) {
    const [a, b] = [luminance(layer(color, behind)), luminance(behind)].sort((p, q) => q - p);
    return (a + 0.05) / (b + 0.05) < 1.5;
  }

  function expandable(content, cite) {
    const q = el('blockquote', { class: 'tg-expandable tg-collapsed' }, content, cite);
    q.addEventListener('click', () => q.classList.toggle('tg-collapsed'));
    return q;
  }

  // Slides turn by touch (native scroll), by a click on a dot, by ← → on the focused slides and by
  // dragging with the mouse. Moves are physical (scroll deltas), so RTL posts turn the right way too.
  function slideshow(contentFrag, cap) {
    const track = el('div', { class: 'tg-slides' }, contentFrag);
    const count = track.children.length;
    // nothing to turn in a single slide: no focus stop, no hint about arrows
    if (count > 1) Object.entries({ tabindex: 0, role: 'group', 'aria-label': 'Слайдшоу: листается стрелками ← →, мышью и точками' }).forEach(([k, v]) => track.setAttribute(k, String(v)));
    const dots = el('div', { class: 'tg-dots', 'aria-hidden': 'true' });
    for (let i = 0; i < count; i++) dots.append(el('span', { class: i ? '' : 'on' }));
    const by = (left) => track.scrollBy({ left, behavior: 'smooth' });
    track.addEventListener('scroll', () => {
      const i = Math.round(Math.abs(track.scrollLeft) / Math.max(1, track.clientWidth));
      [...dots.children].forEach((d, j) => d.classList.toggle('on', j === i));
    });
    dots.addEventListener('click', (ev) => {
      const s = track.children[[...dots.children].indexOf(/** @type {Element} */ (ev.target))];
      if (s) by(s.getBoundingClientRect().left - track.getBoundingClientRect().left);
      track.focus({ preventScroll: true });
    });
    track.addEventListener('keydown', (ev) => {
      const step = ev.key === 'ArrowLeft' ? -1 : ev.key === 'ArrowRight' ? 1 : 0;
      if (!step || ev.target !== track) return; // arrows inside a slide (video controls) stay theirs
      ev.preventDefault();
      track.scrollBy({ left: step * track.clientWidth }); // at once: a smooth turn would eat a quick second press
    });
    // While dragging snapping is off; the click that ends a drag is swallowed, so it does not open
    // a spoiler or start a video under the pointer
    /** @type {{ x: number, left: number, dx: number, moved: boolean } | null} */
    let drag = null;
    let swallow = false;
    track.addEventListener('dragstart', (ev) => ev.preventDefault());
    track.addEventListener('pointerdown', (ev) => {
      swallow = false;
      if (ev.pointerType === 'mouse' && ev.button === 0 && count > 1) drag = { x: ev.clientX, left: track.scrollLeft, dx: 0, moved: false };
    });
    track.addEventListener('pointermove', (ev) => {
      if (!drag) return;
      if (!(ev.buttons & 1)) { end(); return; } // the button went up where we did not see it
      drag.dx = ev.clientX - drag.x;
      if (!drag.moved) {
        if (Math.abs(drag.dx) < 5) return;
        drag.moved = true;
        track.classList.add('tg-dragging');
        track.setPointerCapture(ev.pointerId);
      }
      track.scrollLeft = drag.left - drag.dx;
    });
    const end = () => {
      const d = drag;
      drag = null;
      if (!d || !d.moved) return;
      swallow = true;
      setTimeout(() => { swallow = false; });
      // to the nearest slide, or to the next one when the drag went a fifth of a slide, as a swipe does
      const w = track.clientWidth, from = track.scrollLeft;
      let to = Math.round(from / w) * w;
      if (to === Math.round(d.left / w) * w && Math.abs(d.dx) > w / 5) to -= Math.sign(d.dx) * w;
      // snapping comes back once the slide is in place: turned on midway it jumps to the nearest slide
      const t0 = performance.now();
      const step = (now) => {
        if (drag && drag.moved) return; // a new drag took over
        const k = Math.min(1, (now - t0) / 200);
        track.scrollLeft = from + (to - from) * (1 - (1 - k) ** 3);
        if (k < 1) requestAnimationFrame(step); else track.classList.remove('tg-dragging');
      };
      requestAnimationFrame(step);
    };
    track.addEventListener('pointerup', end);
    track.addEventListener('pointercancel', end);
    track.addEventListener('click', (ev) => { if (swallow) { swallow = false; ev.preventDefault(); ev.stopPropagation(); } }, true);
    return el('figure', { class: 'tg-slideshow' }, track, count > 1 ? dots : null, cap);
  }

  function table(b, ctx) {
    const t = el('table', { class: ['tg-table', b.is_bordered && 'bordered', b.is_striped && 'striped', b.is_compact && 'compact'].filter(Boolean).join(' ') });
    if (b.caption) t.append(el('caption', {}, renderRT(b.caption, ctx)));
    const body = el('tbody');
    (b.cells || []).forEach((row) => {
      const tr = el('tr');
      (row || []).forEach((c) => {
        c = c || {};
        const cell = el(c.is_header ? 'th' : 'td', { colspan: c.colspan > 1 ? c.colspan : null, rowspan: c.rowspan > 1 ? c.rowspan : null, class: c.text === undefined ? 'tg-cell-empty' : null }, renderRT(c.text, ctx));
        // through CSSOM, a value of one property: written into the style attribute, «left;position:fixed;…» was CSS
        // of its own and covered the studio page over the shadow root (issue #201). A value CSS does not take is the
        // default, as the check warns about it
        cell.style.textAlign = c.align || 'left';
        cell.style.textAlign ||= 'left';
        cell.style.verticalAlign = c.valign || 'top';
        cell.style.verticalAlign ||= 'top';
        tr.append(cell);
      });
      body.append(tr);
    });
    t.append(body);
    return el('div', { class: 'tg-table-wrap' }, t);
  }

  function mapEl(lat, lon, zoom, w, h) {
    lat = parseFloat(lat); lon = parseFloat(lon); zoom = Math.max(0, Math.min(18, parseInt(zoom ?? 15, 10) || 15));
    const ratio = w && h ? h / w : 0.6;
    const box = el('div', { class: 'tg-map', style: `aspect-ratio:${1 / Math.min(20, Math.max(0.05, ratio))}` });
    if (isFinite(lat) && isFinite(lon)) {
      const n = 2 ** zoom;
      const xt = (lon + 180) / 360 * n;
      const yt = (1 - Math.log(Math.tan(lat * Math.PI / 180) + 1 / Math.cos(lat * Math.PI / 180)) / Math.PI) / 2 * n;
      const tx = Math.floor(xt), ty = Math.floor(yt);
      // 3x3 tiles centred on the point
      const grid = el('div', { class: 'tg-map-tiles', style: `left:calc(50% - ${(xt - tx + 1) * 256}px);top:calc(50% - ${(yt - ty + 1) * 256}px)` });
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const tile = el('img', { src: `https://tile.openstreetmap.org/${zoom}/${(tx + dx + n) % n}/${ty + dy}.png`, alt: '', loading: 'lazy', referrerpolicy: 'no-referrer' });
        tile.addEventListener('error', () => { tile.style.visibility = 'hidden'; }); // offline: keep the grid background
        grid.append(tile);
      }
      box.append(grid, el('div', { class: 'tg-map-pin', text: '📍' }), el('a', { class: 'tg-map-attr', href: `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=${zoom}/${lat}/${lon}`, target: '_blank', rel: 'noopener', text: '© OpenStreetMap' }));
    } else box.append(el('div', { class: 'tg-media-ph', text: 'Карта: нет координат' }));
    return box;
  }

  // ---------- HTML / Markdown ----------
  // Allowlist sanitizer for user-supplied HTML/Markdown. Anything not listed is unwrapped
  // (children kept as text) or dropped entirely; tg-* tags are kept so new Telegram tags still show.
  const ALLOWED_TAGS = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del',
    'sub', 'sup', 'mark', 'code', 'pre', 'a', 'br', 'hr', 'footer', 'blockquote', 'cite', 'aside', 'ul', 'ol', 'li', 'input',
    'details', 'summary', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'colgroup', 'col', 'figure',
    'figcaption', 'img', 'video', 'audio', 'source', 'span', 'div', 'section', 'article', 'header', 'small', 'q', 'abbr']);
  const DROP_TAGS = new Set(['script', 'style', 'iframe', 'frame', 'frameset', 'object', 'embed', 'link', 'meta', 'base',
    'form', 'textarea', 'select', 'option', 'button', 'svg', 'math', 'template', 'noscript', 'canvas', 'applet', 'portal',
    'dialog', 'slot', 'title', 'head']);
  const ALLOWED_ATTRS = new Set(['href', 'src', 'name', 'title', 'alt', 'colspan', 'rowspan', 'align', 'valign', 'start',
    'type', 'value', 'checked', 'open', 'bordered', 'striped', 'compact', 'expandable', 'tg-spoiler', 'lang',
    'dir', 'width', 'height', 'poster']);
  const URL_ATTRS = new Set(['href', 'src', 'url', 'poster']);
  const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'tg:', 'mailto:', 'tel:']);

  // The address is checked as the browser reads it: it takes tabs and line breaks out anywhere (URL standard) and in an
  // http(s) address reads «\» as «/». The check also trims every space, so nothing in front hides a scheme or a «//».
  // What goes into href is the address as written: Telegram keeps U+FEFF in it, at the end too (measured, issue #174),
  // and off the ends the browser itself takes only C0 controls and spaces. A #name is no address: the preview finds
  // its reference or anchor itself (transformHtml), by the name trimmed, as before #192.
  const asRead = (v) => v.replace(/[\t\n\r]/g, '').trim();
  function safeUrl(v) {
    if (typeof v !== 'string') return null;
    const s = asRead(v);
    if (!s) return null;
    if (s.startsWith('#')) return s;
    // «/\evil.example» and «\/evil.example» are «//evil.example», another site (issue #192)
    if (/^[/\\]{2}/.test(s)) return null;
    if (s.startsWith('/')) return v; // same-origin (uploaded media)
    try {
      const u = new URL(s);
      return SAFE_PROTOCOLS.has(u.protocol) ? v : null;
    } catch (e) { return null; }
  }

  // A src or poster the preview loads by itself, with the cookies of the page it is drawn on: on the studio that is a
  // request on the person's behalf (<img src="/api/check?chat_id=@x">, issue #201). So of the studio's own addresses
  // only its files go, /media/ (app/api/media.py), as the browser resolves the path: «/media/../api/x» and
  // «/media\..\api\x» are /api/x. Another site's address goes as before.
  function fileUrl(v) {
    const s = safeUrl(v);
    if (s === null) return null;
    const here = /^https?:$/.test(location.protocol) ? location.href : 'https://studio.invalid/';
    let u;
    try { u = new URL(s, here); } catch (e) { return null; }
    if (!/^https?:$/.test(u.protocol)) return s; // tg://, mailto: and tel: load nothing
    return u.origin !== new URL(here).origin || u.pathname.startsWith('/media/') ? s : null;
  }

  function sanitize(root) {
    // walk a static snapshot, deepest first is not needed: removed subtrees are skipped via isConnected
    [...root.querySelectorAll('*')].forEach((n) => {
      if (!root.contains(n)) return;
      const tag = n.localName.toLowerCase();
      if (DROP_TAGS.has(tag) || n.namespaceURI !== 'http://www.w3.org/1999/xhtml') { n.remove(); return; }
      const isTg = tag.startsWith('tg-');
      if (!isTg && !ALLOWED_TAGS.has(tag)) { n.replaceWith(...n.childNodes); return; }
      [...n.attributes].forEach((a) => {
        const name = a.name.toLowerCase();
        // a name is an anchor, <a name>; on <img> it is a property of document: <img name="documentElement"> replaced
        // document.documentElement on /render and in the MCP app (issue #201)
        // the style of <tg-button> is its colour (primary, danger…), a word that names a class (word), never CSS
        const keep = (name !== 'style' || (tag === 'tg-button' && word(a.value) !== '')) && !name.startsWith('on') && !name.includes(':') &&
          (isTg ? true : (ALLOWED_ATTRS.has(name) && (name !== 'name' || tag === 'a')) || (name === 'class' && tag === 'code' && /^language-[\w+#-]+$/.test(a.value)));
        if (!keep) { n.removeAttribute(a.name); return; }
        if (URL_ATTRS.has(name) && !(name === 'src' && /^tg:/.test(a.value)) && (name === 'src' || name === 'poster' ? fileUrl(a.value) : safeUrl(a.value)) === null) n.removeAttribute(a.name);
      });
      if (tag === 'input' && (n.getAttribute('type') || '').toLowerCase() !== 'checkbox') n.remove();
    });
  }

  // what kind the file is, its entry in media says, not the link: the docs give only tg://photo, video, audio and
  // document, so a GIF (InputMediaAnimation) comes as tg://video and a voice note (InputMediaVoiceNote) as tg://audio
  const MEDIA_KINDS = new Set(['photo', 'video', 'animation', 'audio', 'document', 'voice_note']);
  function mediaFromSrc(src, ctx) {
    const m = /^tg:\/\/(photo|video|audio|document|animation|voice(?:_note)?)\?id=([\w\-.]+)/.exec(src || '');
    if (!m) return null;
    const item = (ctx.mediaList || []).find((x) => x && x.id === m[2]);
    const media = item ? item.media : null;
    const type = media && typeof media === 'object' ? media.type : null;
    return { kind: MEDIA_KINDS.has(type) ? type : m[1] === 'voice' ? 'voice_note' : m[1], media, id: m[2] };
  }

  function transformHtml(root, ctx) {
    sanitize(root);
    const walk = (node, inPre) => {
      const tag = node === root ? '' : node.tagName.toLowerCase();
      // a formula is its text, taken before anything in it is drawn, and a tag in it is not drawn by itself: drawn
      // first, a formula in a formula gave the outer one the text of the inner one as KaTeX drew it, three times longer
      // on every level (12 levels: 52 s, issue #198)
      if (tag === 'tg-math' || tag === 'tg-math-block') { node.replaceWith(math(node.textContent, tag === 'tg-math-block')); return; }
      [...node.childNodes].forEach((c) => { if (c.nodeType === 1) walk(c, inPre || tag === 'pre'); });
      if (node === root) return;
      /** @type {HTMLElement | null} */
      let rep = null;
      const attr = (n) => node.getAttribute(n);
      const kids = () => [...node.childNodes];
      if (tag === 'tg-spoiler') rep = spoilerEl(kids());
      else if (tag === 'tg-emoji') rep = el('span', { class: 'tg-emoji', title: 'custom emoji ' + attr('emoji-id') }, kids());
      else if (tag === 'tg-time') {
        // Telegram drops a <tg-time> without text, so the reader sees nothing there
        const u = parseInt(attr('unix'), 10);
        if (!node.textContent.trim()) { node.remove(); return; }
        rep = el('span', { class: 'tg-time', title: formatTime(u, 'wDT') + ' (у читателя: его часовой пояс)' }, kids());
      }
      else if (tag === 'tg-collage' || tag === 'tg-slideshow') {
        // the caption goes under the media, wherever it was written: it is neither a cell nor a slide
        const caps = kids().filter((k) => k.nodeType === 1 && k.tagName.toLowerCase() === 'figcaption');
        const items = kids().filter((k) => !caps.includes(k));
        rep = tag === 'tg-slideshow' ? slideshow(items, caps) : el('figure', { class: 'tg-collage' }, el('div', { class: 'tg-collage-grid' }, items), caps);
      }
      else if (tag === 'tg-map') rep = mapEl(attr('lat') ?? attr('latitude'), attr('long') ?? attr('lon') ?? attr('longitude'), attr('zoom'), attr('width'), attr('height'));
      else if (tag === 'tg-thinking') rep = el('div', { class: 'tg-thinking' }, kids());
      else if (tag === 'tg-reference') rep = el('span', { class: 'tg-ref', id: 'ref-' + attr('name') }, kids());
      else if (tag === 'tg-button-row') rep = el('div', { class: 'tg-btn-row tg-align-' + (word(attr('align')) || 'center') }, kids().filter((k) => k.nodeType !== 3));
      else if (tag === 'tg-button') {
        const b = { text: node.textContent, style: attr('style') || undefined };
        const type = attr('type') || (attr('url') ? 'url' : 'callback_data');
        if (type === 'url') b.url = safeUrl(attr('url')) || ''; else if (type === 'copy_text') b.copy_text = { text: attr('text') }; else if (type === 'disabled') b.disabled = {}; else b[type] = attr('data') || attr(type) || '';
        rep = buttonEl(b, ctx);
      } else if (tag === 'tg-document' || ((tag === 'img' || tag === 'video' || tag === 'audio') && /^tg:/.test(attr('src') || ''))) {
        /** @type {{ kind: string, media: any, id?: string }} */
        const info = mediaFromSrc(attr('src'), ctx) || { kind: tag === 'tg-document' ? 'document' : tag === 'img' ? 'photo' : tag, media: null };
        const kind = tag === 'tg-document' ? 'document' : info.kind;
        rep = mediaEl(kind, info.media || (info.id ? 'нет в media: ' + info.id : null), ctx);
        if (node.hasAttribute('tg-spoiler')) rep.classList.add('tg-media-spoiler');
      } else if (tag === 'blockquote' && node.hasAttribute('expandable')) {
        rep = expandable(el('div', {}, kids()), null);
      } else if (tag === 'aside') { node.classList.add('tg-pullquote'); }
      else if (tag === 'table') {
        ['bordered', 'striped', 'compact'].forEach((k) => node.hasAttribute(k) && node.classList.add(k));
        node.classList.add('tg-table');
        // Telegram's defaults: headers centred, other cells left, everything vertically centred
        node.querySelectorAll('th, td').forEach((c) => {
          c.style.textAlign = c.getAttribute('align') || (c.tagName === 'TH' ? 'center' : 'left');
          c.style.verticalAlign = c.getAttribute('valign') || 'middle';
        });
        const wrap = el('div', { class: 'tg-table-wrap' });
        node.replaceWith(wrap); wrap.append(node); return;
      } else if (tag === 'li' && node.firstElementChild && node.firstElementChild.matches('input[type=checkbox]')) {
        node.classList.add('tg-task'); node.firstElementChild.disabled = true;
      } else if (tag === 'a' && attr('name') && !attr('href')) {
        rep = el('a', { name: attr('name'), id: 'anchor-' + attr('name'), class: 'tg-anchor' });
      } else if (tag === 'a' && (linkDropped(attr('href')) || attr('href')?.includes('\u2028'))) {
        // Telegram drops a link whose address has a ", a space… in it (LINK_DROPS) and keeps only its text, and so it
        // does over a U+2028 here: tgEntities took the typed ones out of the address (hrefTyped), this one is an entity
        node.replaceWith(...kids()); return;
      } else if (tag === 'a' && attr('href')) {
        // a U+2028 typed as it is went out before the browser read the address, so the sanitizer saw the address as
        // it is: «/U+2028/x» typed is «//x», a link to another site
        const href = safeUrl(attr('href'));
        if (href === null) node.removeAttribute('href');
        else {
          node.setAttribute('href', href);
          // #name links are resolved after the walk, when all anchors and references are known
          if (!href.startsWith('#')) { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener'); }
        }
      } else if (tag === 'pre') {
        // a <pre> in a code block is part of its code, not a box of its own: each moved all that was under it into its
        // box, and 6000 of them took a second (issue #198)
        if (inPre) return;
        const lang = node.querySelector('code[class*="language-"]');
        const m = lang && /language-([\w+#-]+)/.exec(lang.className);
        if (m) node.dataset.lang = m[1];
        const parent = node.parentNode, next = node.nextSibling;
        parent.insertBefore(codeBox(node, m && m[1]), next); // codeBox takes the <pre> out to put it under its header
        return;
      } else if (tag.startsWith('tg-')) {
        rep = unknown('тег <' + tag + '>', kids());
      }
      if (rep) node.replaceWith(rep);
    };
    walk(root, false);
    // <a href="#x"> is a footnote link when a <tg-reference name="x"> exists, an anchor link when
    // an <a name="x"> exists, and an ordinary link otherwise
    const refs = new Set([...root.querySelectorAll('.tg-ref')].map((n) => n.id.slice(4)));
    const anchors = new Set([...root.querySelectorAll('.tg-anchor')].map((n) => n.getAttribute('name')));
    root.querySelectorAll('a[href^="#"]').forEach((a) => {
      const raw = (a.getAttribute('href') || '').slice(1);
      let name;
      try { name = decodeURIComponent(raw); } catch (e) { name = raw; } // a broken %-escape stays as typed
      if (refs.has(name)) { a.setAttribute('href', '#ref-' + name); a.classList.add('tg-reflink'); }
      else if (anchors.has(name) || !name) { a.setAttribute('href', '#anchor-' + name); a.classList.add('tg-link'); } // bare # is the top of the post
    });
    wrapLoose(root);
    root.querySelectorAll('blockquote').forEach(wrapLoose);
  }

  // Text and inline tags outside any block become a paragraph, as Telegram makes them
  const INLINE_TAGS = new Set(['A', 'B', 'STRONG', 'I', 'EM', 'U', 'INS', 'S', 'STRIKE', 'DEL', 'SUB', 'SUP', 'MARK', 'CODE', 'SPAN', 'BR', 'SMALL', 'Q', 'ABBR', 'CITE', 'INPUT']);
  function wrapLoose(root) {
    /** @type {Node[]} */
    let run = [];
    const flush = (before) => {
      if (run.some((n) => n.nodeType === 1 || (n.textContent || '').trim())) {
        const p = el('p');
        root.insertBefore(p, before);
        run.forEach((n) => p.append(n));
        // the line breaks and spaces at the edges of a loose run are not part of the text
        while (p.firstChild && p.firstChild.nodeType === 3 && !(p.firstChild.textContent || '').trim()) p.firstChild.remove();
      }
      run = [];
    };
    [...root.childNodes].forEach((n) => {
      const e = /** @type {Element} */ (n);
      // a quote's <cite> is its credit line, not part of the text
      const inline = n.nodeType === 3 || (n.nodeType === 1 && INLINE_TAGS.has(e.tagName) && !e.classList.contains('tg-anchor') && !(e.tagName === 'CITE' && root.tagName === 'BLOCKQUOTE'));
      if (inline) run.push(n); else flush(n);
    });
    flush(null);
  }

  // The named entities Telegram decodes, in html and markdown alike (all 2125 names of HTML5 measured, PR #173; the
  // same table is TG_ENTITIES in app/spec/validator.py), and numeric ones with a small x. Every other «&…» (&laquo;,
  // &NBSP;, &nbsp without «;», &#X22;) stays as typed: the browser gets it as text.
  // Numeric ones as Telegram makes them, not as a browser does (measured on Bot API 10.4, 2026-11-16, issue #174;
  // tests/telegram_parse/numeric_entities.json): digits without a limit; 0…31 a space, but &#10; in <pre> a line break;
  // 128…159 the same code point (a browser reads them by windows-1252: &#128; «€», &#150; «–»); a surrogate or a
  // number past U+10FFFF erases the whole text it stands in, up to the nearest tag (a heading or a quote left without
  // text, and Telegram refuses the post). In a markdown code fence they stay as typed, as markdown-it leaves them.
  const TG_ENTITIES = { amp: '&', apos: "'", copy: '©', gt: '>', hellip: '…', ldquo: '“', lsquo: '‘', lt: '<', mdash: '—',
    nbsp: '\u00a0', ndash: '–', quot: '"', rdquo: '”', reg: '®', rsquo: '’', trade: '™' };
  const ENTITY_SRC = `&(?:(${Object.keys(TG_ENTITIES).join('|')})|#([0-9]+)|#x([0-9a-fA-F]+));`;
  const ENTITY_AT = new RegExp(ENTITY_SRC, 'y');
  const ENTITY_OR_AMP = new RegExp(ENTITY_SRC + '|&', 'g');
  // past U+10FFFF every number is the same, so a long one is not read; zeros in front do not count
  const codeOf = (dec, hex) => {
    const digits = (dec ?? hex).replace(/^0+(?=.)/, '');
    return digits.length > 8 ? 0x110000 : parseInt(digits, dec !== undefined ? 10 : 16);
  };
  /** What Telegram makes of the numeric entity n; null: it erases the text the entity stands in */
  function tgChar(n, pre) {
    if (n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) return null;
    if (n < 32) return n === 10 && pre ? '\n' : ' ';
    return String.fromCodePoint(n);
  }
  const vanishes = (s) => [...s.matchAll(ENTITY_OR_AMP)].some((m) => (m[2] ?? m[3]) !== undefined && tgChar(codeOf(m[2], m[3]), false) === null);
  // A tag, to tell the text of html from its tags (the same source as HTML_TAG_RE in app/spec/validator.py, which says
  // why it is linear): a piece of text between two tags is what Telegram erases for an entity it makes no character of
  const TAG = /<\/?[A-Za-z](?=([^\s/<>]*))\1(?:[^<>"']|"[^"<]*"|'[^'<]*')*>/g;
  // An attribute of a tag, its name and its value as written (the same source as ATTR_RE in app/spec/validator.py):
  // the spaces around it are the ASCII ones of html, so a U+2028 is part of a value without quotes, as for the browser
  const ATTR_RE = /(?<=[\t\n\f\r "'/])([^\t\n\f\r "'=/>]+)(?:[\t\n\f\r ]*=[\t\n\f\r ]*("[^"]*"|'[^']*'|[^\t\n\f\r >]*))?/g;
  // <a href> as Telegram reads it: a U+2028 typed as it is it takes out of the address before it decodes the entities,
  // so one that an entity makes (&#x2028;, &#8232;) stays there, and over it Telegram drops the link (measured, issue
  // #195; the same rule is tg_href in app/spec/validator.py). In <tg-button url> that was not measured.
  function hrefTyped(tag) {
    if (!tag.includes('\u2028') || !/^<a[\t\n\f\r /]/i.test(tag)) return tag;
    for (const m of tag.matchAll(ATTR_RE)) {
      if (m[1].toLowerCase() !== 'href') continue;
      const value = m[2] || '', end = (m.index || 0) + m[0].length;
      return tag.slice(0, end - value.length) + value.replace(/\u2028/g, '') + tag.slice(end);
    }
    return tag;
  }
  // html as the browser must get it to read the entities as Telegram does: a numeric one is the character Telegram
  // makes (an entity again where it would be markup), in a tag too, but one that erases text stays as typed there (not
  // measured in attributes); 128…159 go as the characters themselves, as entities the browser would read them by
  // windows-1252; every other «&» is text
  function tgEntities(src) {
    let out = '', at = 0, pre = 0;
    const put = (s, inTag) => s.replace(ENTITY_OR_AMP, (m, name, dec, hex) => {
      if (name) return m;
      if (dec === undefined && hex === undefined) return '&amp;';
      const n = codeOf(dec, hex);
      const c = tgChar(n, !inTag && pre > 0);
      if (c === null) return '&amp;' + m.slice(1);
      if (n >= 128 && n < 160) return c;
      if (c === ' ') return inTag ? '&#32;' : ' ';
      return c === '\n' ? '&#10;' : `&#x${n.toString(16)};`;
    });
    const text = (s) => (vanishes(s) ? '' : put(s, false));
    for (const m of src.matchAll(TAG)) {
      out += text(src.slice(at, m.index)) + put(hrefTyped(m[0]), true);
      const p = /^<(\/?)pre[\s/>]/i.exec(m[0]);
      if (p) pre = Math.max(0, pre + (p[1] ? -1 : 1));
      at = (m.index || 0) + m[0].length;
    }
    return out + text(src.slice(at));
  }

  // A link address as Telegram takes it, its entities decoded (measured on Bot API 10.4, 2026-11-16, issue #174; the
  // same set is LINK_DROPS in app/spec/validator.py): over a " < > space or control character, U+0085, U+00A0, U+2003,
  // U+3000 or U+200B in it Telegram drops the link and keeps its text, in html and in blocks alike; U+2028 typed as it
  // is it takes out of the address (tgAddress, in html hrefTyped), and over one an entity makes in html it drops the
  // link (issue #195). A button looks the same with any address: Telegram refuses the post over a bad one, and the
  // check says so. js/html-export.js writes these characters %-encoded (percentAddress).
  // eslint-disable-next-line no-control-regex -- the control characters are the point
  const LINK_DROPS = /["<>\u0000-\u0020\u0085\u00a0\u2003\u3000\u200b]/;
  const linkDropped = (url) => typeof url === 'string' && LINK_DROPS.test(url);
  const tgAddress = (url) => (typeof url === 'string' ? url.replace(/\u2028/g, '') : url);
  /** @param {string} url the address that goes through: what makes Telegram drop a link %-encoded, U+2028 out */
  const percentAddress = (url) => tgAddress(url).replace(new RegExp(LINK_DROPS.source, 'g'), encodeURIComponent);

  // ---------- TON addresses Telegram finds in the text ----------
  // A user-friendly TON address: 48 characters of base64 and base64url, a mix of both too, for 36 bytes, that is flags,
  // workchain, a 32-byte hash and the CRC16-XMODEM of those 34 bytes, big-endian. Telegram finds one in the text of a
  // post by itself, in html, markdown and the strings of blocks alike, with a right checksum, flags 0x11, 0x51, 0x91 or
  // 0xd1 (EQ…, UQ…, kQ…, 0Q…) and any workchain (measured for #172 and #195; captured html-detected-card-ton and
  // html-ton-checksum). js/html-export.js checks ton_address texts with the same function.
  const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  const TON_FORM = /^[\w+/-]{48}$/;
  const TON_FLAGS = new Set([0x11, 0x51, 0x91, 0xd1]); // bounceable or not, on the main net or the test one
  function isTonAddress(s) {
    if (typeof s !== 'string' || !TON_FORM.test(s)) return false;
    const six = (c) => (c === '-' || c === '+' ? 62 : c === '_' || c === '/' ? 63 : B64.indexOf(c));
    const bytes = [];
    for (let i = 0; i < 48; i += 4) {
      const n = (six(s[i]) << 18) | (six(s[i + 1]) << 12) | (six(s[i + 2]) << 6) | six(s[i + 3]);
      bytes.push(n >> 16, (n >> 8) & 255, n & 255);
    }
    // the workchain (bytes[1]) is any: 1, 2, 0x7f, −128 and −1 (Ef8…) were found alike
    if (!TON_FLAGS.has(bytes[0])) return false;
    let crc = 0;
    for (const b of bytes.slice(0, 34)) {
      crc ^= b << 8;
      for (let k = 0; k < 8; k++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
    return crc === bytes[34] * 256 + bytes[35];
  }

  // Unless skip_entity_detection is on. Not inside code, links, formulas and other elements that already are something
  // else. A card number with spaces Telegram does not find, and the preview finds no card numbers at all.
  const TON_IN_TEXT = /(?<![\w+/-])[\w+/-]{48}(?![\w+/-])/g;
  const NO_DETECTION = 'a, code, pre, .tg-link, .tg-math, .tg-math-block, .tg-time, .tg-emoji, .tg-btn, .tg-media, .tg-unknown-block';
  function findTon(root) {
    // Telegram reads neighbouring strings of RichText as one text: ['EQAA…', '…M9c'] is one address
    root.normalize();
    /** @type {Text[]} */
    const texts = [];
    // what is under such an element is skipped whole: closest() from every piece of text went up through all the tags
    // above it (issue #198)
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 3 ? NodeFilter.FILTER_ACCEPT : /** @type {Element} */ (n).matches(NO_DETECTION) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP),
    });
    for (let n = walker.nextNode(); n; n = walker.nextNode()) texts.push(/** @type {Text} */ (n));
    texts.forEach((n) => {
      const s = n.data;
      /** @type {(string | Node)[]} */
      const parts = [];
      let at = 0;
      for (const m of s.matchAll(TON_IN_TEXT)) {
        if (!isTonAddress(m[0])) continue;
        parts.push(s.slice(at, m.index), tonEl(m[0], m[0]));
        at = (m.index || 0) + m[0].length;
      }
      if (at) n.replaceWith(...[...parts, s.slice(at)].filter((p) => p !== ''));
    });
  }

  function renderHtml(src, ctx) {
    // <template> content is inert: no scripts run and no images load until sanitized nodes are attached
    const tpl = document.createElement('template');
    tpl.innerHTML = '<div>' + src + '</div>';
    const root = /** @type {HTMLElement} */ (tpl.content.firstChild);
    transformHtml(root, ctx);
    const frag = document.createDocumentFragment();
    while (root.firstChild) frag.append(root.firstChild);
    return frag;
  }

  // Markdown the way Telegram parses it (checked against the real Bot API in tests/render_parity.mjs):
  // GFM plus ||spoiler||, ==marker==, $inline$ and $$block$$ formulas, [^footnotes]; tables are always
  // bordered and striped. Everything is turned into the same HTML the HTML mode understands.
  /** @type {any} */
  /** @type {any} */
  let md = null;

  // inline `<mark>text</mark>`-style pair: ||spoiler|| or ==marker==
  function pairRule(name, ch, open, close) {
    return (state, silent) => {
      const s = state.src, start = state.pos;
      if (s.charCodeAt(start) !== ch || s.charCodeAt(start + 1) !== ch) return false;
      const end = s.indexOf(String.fromCharCode(ch, ch), start + 2);
      if (end < 0 || end === start + 2 || end + 2 > state.posMax) return false;
      if (!silent) {
        const max = state.posMax;
        state.push('html_inline', '', 0).content = open;
        state.pos = start + 2; state.posMax = end;
        state.md.inline.tokenize(state);
        state.posMax = max;
        state.push('html_inline', '', 0).content = close;
      }
      state.pos = end + 2;
      return true;
    };
  }

  // $formula$ as in Pandoc: no space after the opening $, none before the closing one, no digit right after it
  function inlineMath(state, silent) {
    const s = state.src, start = state.pos;
    if (s[start] !== '$') return false;
    // $$x$$ inside a line is an inline formula too; on its own line it is a block (blockMath)
    if (s[start + 1] === '$') {
      const close = s.indexOf('$$', start + 2);
      if (close < 0 || close === start + 2 || close + 2 > state.posMax) return false;
      if (!silent) state.push('html_inline', '', 0).content = '<tg-math>' + esc(s.slice(start + 2, close)) + '</tg-math>';
      state.pos = close + 2;
      return true;
    }
    if (/\s/.test(s[start + 1] || ' ')) return false;
    // whether a $ closes depends on it alone, not on the opening one: when none closes an earlier $ up to the end, none
    // closes this one either. Without it every $ looked to the end again: 34 KB of «$1» took 2.7 s (issue #198)
    const open = state.tgMathUnclosedInline;
    if (open && open[0] <= start && state.posMax <= open[1]) return false;
    let end = start + 1;
    for (;;) {
      end = s.indexOf('$', end + 1);
      if (end < 0 || end >= state.posMax) { state.tgMathUnclosedInline = [start, state.posMax]; return false; }
      if (!/\s/.test(s[end - 1]) && !/\d/.test(s[end + 1] || '')) break;
    }
    if (!silent) state.push('html_inline', '', 0).content = '<tg-math>' + esc(s.slice(start + 1, end)) + '</tg-math>';
    state.pos = end + 1;
    return true;
  }

  // $$formula$$ on its own line, or $$ … $$ over several lines
  function blockMath(state, startLine, endLine, silent) {
    const line = (n) => state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]);
    const first = line(startLine);
    if (!first.startsWith('$$')) return false;
    let body = first.slice(2), last = startLine;
    if (body.trimEnd().endsWith('$$') && body.trim().length > 2) body = body.trimEnd().slice(0, -2);
    else {
      // no line up to endLine closes an earlier $$, so none closes this one either: without it every $$ line looked
      // to the end again (issue #192)
      const open = state.tgMathUnclosed;
      if (open && open[0] < startLine && endLine <= open[1]) return false;
      const parts = [body];
      for (last = startLine + 1; last < endLine; last++) {
        const l = line(last);
        if (l.trimEnd().endsWith('$$')) { parts.push(l.trimEnd().slice(0, -2)); break; }
        parts.push(l);
      }
      if (last >= endLine) { state.tgMathUnclosed = [startLine, endLine]; return false; }
      body = parts.join('\n');
    }
    if (silent) return true;
    state.line = last + 1;
    const t = state.push('html_block', '', 0);
    t.content = '<tg-math-block>' + esc(body.trim()) + '</tg-math-block>\n';
    t.map = [startLine, state.line];
    return true;
  }

  // [^label]: text definitions are collected and printed as a footer, [^label] becomes a numbered link
  function footnoteDef(state, startLine, endLine, silent) {
    const s = state.src.slice(state.bMarks[startLine] + state.tShift[startLine], state.eMarks[startLine]);
    const m = /^\[\^([^[\]\s]+)\]:\s*(.*)$/.exec(s);
    if (!m) return false;
    if (silent) return true;
    const parts = [m[2]];
    let n = startLine + 1;
    for (; n < endLine && state.sCount[n] >= state.blkIndent + 4; n++) parts.push(state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]));
    const env = state.env;
    // no prototype: labels such as [^__proto__] or [^toString] are plain names
    (env.footnotes = env.footnotes || Object.create(null))[m[1]] = parts.join(' ');
    state.line = n;
    return true;
  }
  // a label has no «[» (as a link label), so a run of «[^» is looked through once, not from each one (issue #192)
  function footnoteRef(state, silent) {
    const m = /^\[\^([^[\]\s]+)\]/.exec(state.src.slice(state.pos, state.posMax));
    const notes = state.env.footnotes;
    if (!m || !notes || !Object.hasOwn(notes, m[1])) return false;
    if (!silent) {
      const order = state.env.footnoteOrder = state.env.footnoteOrder || [];
      if (!order.includes(m[1])) order.push(m[1]);
      const i = order.indexOf(m[1]) + 1;
      state.push('html_inline', '', 0).content = `<sup><a name="fnref-${i}-1"></a><a href="#fn-${i}">${i}</a></sup>`;
    }
    state.pos += m[0].length;
    return true;
  }
  function footnoteFooter(state) {
    const order = state.env.footnoteOrder;
    if (!order || !order.length) return;
    const t = new state.Token('html_block', '', 0);
    t.content = order.map((k, i) => `<footer>${i + 1}. <tg-reference name="fn-${i + 1}">${state.md.renderInline(state.env.footnotes[k], {})}</tg-reference> <a href="#fnref-${i + 1}-1">↩</a></footer>`).join('');
    state.tokens.push(t);
  }

  // markdown-it's text rule with | added to the stop characters, so ||spoiler|| gets a chance to match
  const STOP = new Set([0x0A, 0x21, 0x23, 0x24, 0x25, 0x26, 0x2A, 0x2B, 0x2D, 0x3A, 0x3C, 0x3D, 0x40, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x7B, 0x7C, 0x7D, 0x7E]);
  function text(state, silent) {
    let pos = state.pos;
    while (pos < state.posMax && !STOP.has(state.src.charCodeAt(pos))) pos++;
    if (pos === state.pos) return false;
    if (!silent) state.pending += state.src.slice(state.pos, pos);
    state.pos = pos;
    return true;
  }

  // markdown-it knows every entity of HTML5; Telegram's markdown only the ones its HTML does, numbers the same way
  function entity(state, silent) {
    if (state.src.charCodeAt(state.pos) !== 0x26) return false;
    ENTITY_AT.lastIndex = state.pos;
    const m = ENTITY_AT.exec(state.src);
    if (!m) return false;
    if (!silent) {
      const c = m[1] ? TG_ENTITIES[m[1]] : tgChar(codeOf(m[2], m[3]), false);
      // one that erases its text goes on into the HTML as typed, and reading the HTML erases the text around it
      if (c === null) state.push('html_inline', '', 0).content = m[0];
      else state.pending += c;
    }
    state.pos += m[0].length;
    return true;
  }

  function markdownParser() {
    const m = /** @type {any} */ (global.markdownit)({ html: true, linkify: true, breaks: false });
    m.inline.ruler.at('text', text);
    m.inline.ruler.at('entity', entity);
    // in a link address Telegram decodes no entity at all ([x](…?q=&quot;x&quot;) keeps «&quot;», measured):
    // markdown-it's own reading decodes them, so the address is taken as written, with only its \-escapes undone
    const destination = m.helpers.parseLinkDestination;
    m.helpers.parseLinkDestination = (str, start, max) => {
      const res = destination(str, start, max);
      if (res.ok) {
        const raw = str.slice(start, res.pos);
        res.str = (raw.startsWith('<') && raw.endsWith('>') ? raw.slice(1, -1) : raw).replace(/\\([!-/:-@[-`{-~])/g, '$1');
      }
      return res;
    };
    m.inline.ruler.before('emphasis', 'tg_spoiler', pairRule('tg_spoiler', 0x7c, '<tg-spoiler>', '</tg-spoiler>'));
    m.inline.ruler.before('emphasis', 'tg_mark', pairRule('tg_mark', 0x3d, '<mark>', '</mark>'));
    m.inline.ruler.after('escape', 'tg_math', inlineMath);
    m.inline.ruler.before('link', 'tg_footnote_ref', footnoteRef);
    m.block.ruler.before('fence', 'tg_math_block', blockMath, { alt: ['paragraph'] });
    m.block.ruler.before('reference', 'tg_footnote_def', footnoteDef);
    m.core.ruler.after('inline', 'tg_footnote_footer', footnoteFooter);
    m.renderer.rules.table_open = () => '<table bordered striped>\n';
    // column alignment arrives as style="text-align:…", which the sanitizer drops; Telegram reads it
    const cell = (tokens, idx, opts, env, self) => {
      const style = tokens[idx].attrGet('style');
      const a = style && /text-align:(\w+)/.exec(style);
      if (a) { tokens[idx].attrs = tokens[idx].attrs.filter((x) => x[0] !== 'style'); tokens[idx].attrSet('align', a[1]); }
      return self.renderToken(tokens, idx, opts);
    };
    m.renderer.rules.th_open = cell;
    m.renderer.rules.td_open = cell;
    return m;
  }

  // Telegram drops cells with nothing between the pipes: "| 1 || 3 |" is two cells, not three,
  // and ||spoiler|| inside a table row falls apart the same way
  const DELIM_ROW = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
  function tableRows(src) {
    const lines = src.split('\n');
    /** @type {string | null} */
    let fence = null;
    for (let i = 0; i < lines.length; i++) {
      const f = /^\s*(`{3,}|~{3,})/.exec(lines[i]);
      if (f) { if (!fence) fence = f[1][0]; else if (f[1][0] === fence) fence = null; continue; }
      if (fence || !/^\s*\|/.test(lines[i]) || !DELIM_ROW.test(lines[i + 1] || '')) continue;
      let j = i;
      for (; j < lines.length && /^\s*\|/.test(lines[j]); j++) if (j !== i + 1) lines[j] = lines[j].replace(/(?<!\\)\|(?:\|)+/g, '|');
      i = j - 1; // the rows of this table are done: from each of them it was read again to its end (issue #192)
    }
    return lines.join('\n');
  }

  function renderMarkdown(src, ctx) {
    if (!md && global.markdownit) md = markdownParser();
    let html;
    if (md) {
      html = md.render(tableRows(src), {});
      html = html.replace(/<li>\s*\[([ xX])\]\s*/g, (m, c) => `<li><input type="checkbox"${c.trim() ? ' checked' : ''}> `)
        .replace(/<li><p>\s*\[([ xX])\]\s*/g, (m, c) => `<li><p><input type="checkbox"${c.trim() ? ' checked' : ''}> `);
    } else html = '<pre>' + esc(src) + '</pre>';
    return renderHtml(tgEntities(html), ctx); // raw HTML in markdown: the same entities as in the html mode
  }

  // ---------- message ----------
  function render(post, opts) {
    opts = opts || {};
    const rm = (post && post.rich_message) || {};
    const ctx = { mediaBase: opts.mediaBase || '', mediaList: rm.media || [] };
    const body = el('div', { class: 'tg-body', dir: rm.is_rtl ? 'rtl' : null });
    try {
      if (rm.blocks) body.append(renderBlocks(rm.blocks, ctx));
      else if (rm.html) body.append(renderHtml(tgEntities(rm.html), ctx));
      else if (rm.markdown) body.append(renderMarkdown(rm.markdown, ctx));
      else body.append(el('p', { class: 'tg-empty', text: 'Пустой пост' }));
      // blocks Telegram answered with went through its detection already: an address it left as text is text, so
      // whoever draws them (the parse check, test:parity) passes skip_entity_detection
      if (!rm.skip_entity_detection) findTon(body);
    } catch (e) {
      body.append(el('pre', { class: 'tg-error', text: 'Ошибка рендера: ' + e.message }));
    }
    const bubble = el('div', { class: 'tg-bubble' }, body,
      el('div', { class: 'tg-meta' }, opts.views ? el('span', { text: '👁 ' + opts.views }) : null, el('span', { text: new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) })));
    const kb = post && post.reply_markup && post.reply_markup.inline_keyboard;
    const wrap = el('div', { class: 'tg-msg' }, opts.channelTitle ? el('div', { class: 'tg-channel', text: opts.channelTitle }) : null, bubble);
    if (Array.isArray(kb) && kb.length) {
      wrap.append(el('div', { class: 'tg-kb' }, kb.map((row) => el('div', { class: 'tg-kb-row' }, (row || []).map((b) => buttonEl(b, ctx))))));
    }
    return wrap;
  }

  // ---------- outline ----------
  // A rendered post reduced to what the reader sees: block and inline structure plus text. Two ways of
  // drawing the same post (its HTML and the blocks Telegram made of it) give the same outline; the
  // editor and tests/render_parity.mjs use it to tell where the preview and Telegram differ.
  function outline(post) {
    const root = render(post, {}).querySelector('.tg-body');
    if (!root) return '';
    const INLINE = { B: 'b', STRONG: 'b', I: 'i', EM: 'i', U: 'u', INS: 'u', S: 's', DEL: 's', STRIKE: 's', MARK: 'mark', SUB: 'sub', SUP: 'sup', CODE: 'code', CITE: 'cite', CAPTION: 'caption', SUMMARY: 'summary', FOOTER: 'footer', HR: 'hr', BR: 'br' };
    const BLOCK = { P: 'p', H1: 'h1', H2: 'h2', H3: 'h3', H4: 'h4', H5: 'h5', H6: 'h6', UL: 'ul', OL: 'ol', TR: 'tr' };
    const out = [];
    // the place of each item in its list, counted once for the list: indexOf for every item was quadratic (issue #198)
    /** @type {Map<Element, number>} */
    const place = new Map();
    const text = (s) => { s = s.replace(/\s+/g, ' '); if (s) out.push(s); };
    const walk = (n) => {
      if (n.nodeType === 3) { text(n.textContent); return; }
      if (n.nodeType !== 1) return;
      const c = n.classList;
      let tag;
      if (c.contains('katex') || c.contains('tg-math') || c.contains('tg-math-block')) {
        const tex = n.querySelector('annotation');
        out.push(`<${c.contains('tg-math-block') ? 'math-block' : 'math'}:${(tex ? tex.textContent : n.textContent).trim()}>`);
        return;
      }
      if (c.contains('tg-spoiler')) tag = 'spoiler';
      else if (c.contains('tg-expandable')) tag = 'expandable';
      else if (c.contains('tg-pullquote')) tag = 'pullquote';
      else if (c.contains('tg-time')) tag = 'time';
      else if (c.contains('tg-ref')) tag = 'ref';
      else if (c.contains('tg-ton')) tag = 'ton'; // a ton_address, or an address Telegram finds in the text
      else if (c.contains('tg-anchor')) { out.push('<anchor>'); return; }
      else if (n.tagName === 'A') {
        const href = n.getAttribute('href') || '';
        tag = c.contains('tg-reflink') || href.startsWith('#ref-') ? 'reflink' : href.startsWith('#anchor-') ? 'anchorlink' : 'a';
      } else if (n.tagName === 'TABLE') tag = 'table' + ['bordered', 'striped', 'compact'].filter((k) => c.contains(k)).map((k) => '.' + k).join('');
      else if (n.tagName === 'TH' || n.tagName === 'TD') {
        const align = n.style.textAlign || n.getAttribute('align') || 'left';
        const span = n.getAttribute('colspan') ? ' colspan=' + n.getAttribute('colspan') : '';
        tag = `${n.tagName.toLowerCase()} ${align}${span}`;
      } else if (n.tagName === 'BLOCKQUOTE') tag = 'blockquote';
      else if (n.tagName === 'DETAILS') tag = 'details' + (n.open ? ' open' : '');
      else if (n.tagName === 'PRE') tag = 'pre' + (n.dataset.lang ? ':' + n.dataset.lang : ''); // the language label the reader sees
      else if (n.tagName === 'LI') {
        const box = n.querySelector(':scope > input[type=checkbox]');
        const list = n.parentElement;
        if (list.tagName === 'OL' && !place.has(n)) [...list.children].forEach((c, i) => place.set(c, i));
        const num = list.tagName === 'OL' ? ' ' + (n.getAttribute('value') || (parseInt(list.getAttribute('start') || '1', 10) + (place.get(n) ?? 0))) : '';
        tag = 'li' + (box ? (box.checked ? ' [x]' : ' [ ]') : '') + num;
      } else if (n.tagName === 'INPUT') return;
      else if (n.tagName === 'P' && n.parentElement.tagName === 'LI') tag = null; // same look with or without the paragraph
      else if (n.tagName === 'CODE' && n.parentElement.tagName === 'PRE') tag = null;
      else tag = INLINE[n.tagName] || BLOCK[n.tagName] || null;
      if (tag) out.push(`<${tag}>`);
      n.childNodes.forEach(walk);
      if (tag && !['hr', 'br'].includes(tag)) out.push(`</${tag.split(/[ .:]/)[0]}>`);
    };
    root.childNodes.forEach(walk);
    // inline wrappers that hold only spaces and runs of spaces mean nothing to the reader
    return out.join('').replace(/ +/g, ' ').replace(/<br> /g, '<br>').replace(/ <\/pre>/g, '</pre>').replace(/ ?(<math-block:[^>]*>|<hr>|<anchor>) ?/g, '$1').trim().replace(/ ?(<\/?(?:p|h\d|li[^>]*|ul|ol|blockquote|tr|t[hd][^>]*|table[^>]*|footer|summary|details[^>]*|pre[^>]*|caption|cite|pullquote|expandable)>) ?/g, '$1');
  }

  global.RichRender = { render, renderRT, renderBlocks, formatTime, mediaSrc, safeUrl, outline, isTonAddress, percentAddress };
})(window);
