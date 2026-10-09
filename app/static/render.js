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
    if (m.startsWith('upload:')) return (ctx.mediaBase || '') + '/media/' + encodeURIComponent(m.slice(7));
    if (/^https?:\/\//i.test(m) && safeUrl(m)) return m;
    return null; // file_id or attach:// cannot be previewed
  }

  function mediaEl(kind, media, ctx, extra) {
    const src = mediaSrc(media, ctx);
    const label = typeof media === 'object' && media ? media.media : media;
    const spoiler = media && media.has_spoiler;
    let node;
    if (!src) {
      node = el('div', { class: 'tg-media-ph' }, el('span', { class: 'tg-media-kind', text: kindLabel(kind) }), el('small', { text: label || 'нет файла' }));
    } else if (kind === 'photo') {
      node = el('img', { src, alt: '', loading: 'lazy' });
    } else if (kind === 'video' || kind === 'animation') {
      node = el('video', { src, controls: kind === 'video', autoplay: kind === 'animation', loop: kind === 'animation', muted: true, playsinline: true });
    } else if (kind === 'audio' || kind === 'voice_note' || kind === 'voice') {
      node = el('audio', { src, controls: true });
    } else {
      node = el('a', { class: 'tg-doc', href: src, target: '_blank', rel: 'noopener' }, el('span', { class: 'tg-doc-ico', text: '📄' }), (() => { const last = src.split('/').pop() || ''; try { return decodeURIComponent(last); } catch (e) { return last; } })());
    }
    const wrap = el('div', { class: 'tg-media tg-media-' + kind + (spoiler ? ' tg-media-spoiler' : '') }, node);
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
  function buttonEl(b, ctx) {
    if (!b || typeof b !== 'object') return null;
    const action = ['url', 'callback_data', 'web_app', 'login_url', 'switch_inline_query', 'switch_inline_query_current_chat', 'switch_inline_query_chosen_chat', 'copy_text', 'disabled', 'pay', 'callback_game'].find((k) => b[k] !== undefined);
    const icon = { url: '↗', copy_text: '⧉', switch_inline_query: '↪', switch_inline_query_chosen_chat: '↪', login_url: '🔑', web_app: '▣' }[action] || '';
    const title = action === 'url' ? b.url : action === 'copy_text' ? 'Копирует: ' + (b.copy_text && b.copy_text.text) : action === 'callback_data' ? 'callback: ' + b.callback_data : action || 'без действия';
    const btn = el('span', { class: 'tg-btn' + (b.style ? ' tg-btn-' + b.style : '') + (action === 'disabled' ? ' tg-btn-disabled' : ''), title },
      renderRT(b.text, ctx), icon ? el('span', { class: 'tg-btn-ico', text: icon }) : null);
    if (action === 'url' && b.url && /^https?:\/\//i.test(b.url) && safeUrl(b.url)) btn.addEventListener('click', () => window.open(b.url, '_blank', 'noopener'));
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
    else if (t === 'url') frag.append(el('a', { href: safeUrl(rt.url), target: '_blank', rel: 'noopener', title: rt.url }, inner()));
    else if (t === 'text_mention') frag.append(el('a', { class: 'tg-link', title: 'id ' + (rt.user && rt.user.id) }, inner()));
    else if (t === 'email_address') frag.append(el('a', { href: 'mailto:' + rt.email_address }, inner()));
    else if (t === 'phone_number') frag.append(el('a', { href: 'tel:' + rt.phone_number }, inner()));
    else if (['mention', 'hashtag', 'cashtag', 'bot_command', 'bank_card_number'].includes(t)) frag.append(el('span', { class: 'tg-link' }, inner()));
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
      case 'pre': return el('pre', { 'data-lang': b.language || null }, el('code', {}, rt(b.text)));
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
      case 'buttons': return el('div', { class: 'tg-btn-row tg-align-' + (b.align || 'center') }, (b.buttons || []).map((x) => buttonEl(x, ctx)));
      case 'photo': case 'video': case 'animation': case 'audio': case 'document': case 'voice_note':
        return el('figure', {}, mediaEl(b.type, b[b.type], ctx), caption(b.caption, ctx));
      default:
        return el('div', { class: 'tg-unknown tg-unknown-block', title: 'Блок, которого нет в рендере предпросмотра. Telegram его получит.' },
          el('b', { text: 'Блок «' + b.type + '»' }), el('pre', { text: JSON.stringify(b, null, 1) }));
    }
  }

  function expandable(content, cite) {
    const q = el('blockquote', { class: 'tg-expandable tg-collapsed' }, content, cite);
    q.addEventListener('click', () => q.classList.toggle('tg-collapsed'));
    return q;
  }

  function slideshow(contentFrag, cap) {
    const track = el('div', { class: 'tg-slides' }, contentFrag);
    const count = track.children.length;
    const dots = el('div', { class: 'tg-dots' });
    for (let i = 0; i < count; i++) dots.append(el('span', { class: i ? '' : 'on' }));
    track.addEventListener('scroll', () => {
      const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
      [...dots.children].forEach((d, j) => d.className = j === i ? 'on' : '');
    });
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
        const cell = el(c.is_header ? 'th' : 'td', { colspan: c.colspan > 1 ? c.colspan : null, rowspan: c.rowspan > 1 ? c.rowspan : null, style: `text-align:${c.align || 'left'};vertical-align:${c.valign || 'top'}`, class: c.text === undefined ? 'tg-cell-empty' : null }, renderRT(c.text, ctx));
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

  function safeUrl(v) {
    if (typeof v !== 'string') return null;
    const s = v.trim();
    if (!s) return null;
    if (s.startsWith('#')) return s;
    if (s.startsWith('/') && !s.startsWith('//')) return s; // same-origin (uploaded media)
    try {
      const u = new URL(s);
      return SAFE_PROTOCOLS.has(u.protocol) ? s : null;
    } catch (e) { return null; }
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
        const keep = name !== 'style' && !name.startsWith('on') && !name.includes(':') &&
          (isTg ? true : ALLOWED_ATTRS.has(name) || (name === 'class' && tag === 'code' && /^language-[\w+#-]+$/.test(a.value)));
        if (!keep) { n.removeAttribute(a.name); return; }
        if (URL_ATTRS.has(name) && !(name === 'src' && /^tg:/.test(a.value)) && safeUrl(a.value) === null) n.removeAttribute(a.name);
      });
      if (tag === 'input' && (n.getAttribute('type') || '').toLowerCase() !== 'checkbox') n.remove();
    });
  }

  function mediaFromSrc(src, ctx) {
    const m = /^tg:\/\/(photo|video|audio|document|animation|voice(?:_note)?)\?id=([\w\-.]+)/.exec(src || '');
    if (!m) return null;
    const item = (ctx.mediaList || []).find((x) => x && x.id === m[2]);
    return { kind: m[1] === 'voice' ? 'voice_note' : m[1], media: item ? item.media : null, id: m[2] };
  }

  function transformHtml(root, ctx) {
    sanitize(root);
    const walk = (node) => {
      [...node.childNodes].forEach((c) => { if (c.nodeType === 1) walk(c); });
      if (node === root || node.nodeType !== 1) return;
      const tag = node.tagName.toLowerCase();
      /** @type {HTMLElement | null} */
      let rep = null;
      const attr = (n) => node.getAttribute(n);
      const kids = () => [...node.childNodes];
      if (tag === 'tg-spoiler') rep = spoilerEl(kids());
      else if (tag === 'tg-emoji') rep = el('span', { class: 'tg-emoji', title: 'custom emoji ' + attr('emoji-id') }, kids());
      else if (tag === 'tg-math') rep = math(node.textContent, false);
      else if (tag === 'tg-math-block') rep = math(node.textContent, true);
      else if (tag === 'tg-time') {
        // Telegram drops a <tg-time> without text, so the reader sees nothing there
        const u = parseInt(attr('unix'), 10);
        if (!node.textContent.trim()) { node.remove(); return; }
        rep = el('span', { class: 'tg-time', title: formatTime(u, 'wDT') + ' (у читателя: его часовой пояс)' }, kids());
      }
      else if (tag === 'tg-collage') {
        const caps = kids().filter((k) => k.nodeType === 1 && k.tagName.toLowerCase() === 'figcaption');
        rep = el('figure', { class: 'tg-collage' }, el('div', { class: 'tg-collage-grid' }, kids().filter((k) => !caps.includes(k))), caps);
      }
      else if (tag === 'tg-slideshow') rep = slideshow(kids(), null);
      else if (tag === 'tg-map') rep = mapEl(attr('lat') ?? attr('latitude'), attr('long') ?? attr('lon') ?? attr('longitude'), attr('zoom'), attr('width'), attr('height'));
      else if (tag === 'tg-thinking') rep = el('div', { class: 'tg-thinking' }, kids());
      else if (tag === 'tg-reference') rep = el('span', { class: 'tg-ref', id: 'ref-' + attr('name') }, kids());
      else if (tag === 'tg-button-row') rep = el('div', { class: 'tg-btn-row tg-align-' + (attr('align') || 'center') }, kids().filter((k) => k.nodeType !== 3));
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
      } else if (tag === 'a' && attr('href')) {
        // #name links are resolved after the walk, when all anchors and references are known
        if (!attr('href').startsWith('#')) { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener'); }
      } else if (tag === 'pre') {
        const lang = node.querySelector('code[class*="language-"]');
        const m = lang && /language-([\w+#-]+)/.exec(lang.className);
        if (m) node.dataset.lang = m[1];
      } else if (tag.startsWith('tg-')) {
        rep = unknown('тег <' + tag + '>', kids());
      }
      if (rep) node.replaceWith(rep);
    };
    walk(root);
    // <a href="#x"> is a footnote link when a <tg-reference name="x"> exists, an anchor link when
    // an <a name="x"> exists, and an ordinary link otherwise
    const refs = new Set([...root.querySelectorAll('.tg-ref')].map((n) => n.id.slice(4)));
    const anchors = new Set([...root.querySelectorAll('.tg-anchor')].map((n) => n.getAttribute('name')));
    root.querySelectorAll('a[href^="#"]').forEach((a) => {
      const name = decodeURIComponent((a.getAttribute('href') || '').slice(1));
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
    let end = start + 1;
    for (;;) {
      end = s.indexOf('$', end + 1);
      if (end < 0 || end >= state.posMax) return false;
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
      const parts = [body];
      for (last = startLine + 1; last < endLine; last++) {
        const l = line(last);
        if (l.trimEnd().endsWith('$$')) { parts.push(l.trimEnd().slice(0, -2)); break; }
        parts.push(l);
      }
      if (last >= endLine) return false;
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
    const m = /^\[\^([^\]\s]+)\]:\s*(.*)$/.exec(s);
    if (!m) return false;
    if (silent) return true;
    const parts = [m[2]];
    let n = startLine + 1;
    for (; n < endLine && state.sCount[n] >= state.blkIndent + 4; n++) parts.push(state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]));
    const env = state.env;
    (env.footnotes = env.footnotes || {})[m[1]] = parts.join(' ');
    state.line = n;
    return true;
  }
  function footnoteRef(state, silent) {
    const m = /^\[\^([^\]\s]+)\]/.exec(state.src.slice(state.pos, state.posMax));
    const notes = state.env.footnotes;
    if (!m || !notes || !(m[1] in notes)) return false;
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

  function markdownParser() {
    const m = /** @type {any} */ (global.markdownit)({ html: true, linkify: true, breaks: false });
    m.inline.ruler.at('text', text);
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
      for (let j = i; j < lines.length && /^\s*\|/.test(lines[j]); j++) if (j !== i + 1) lines[j] = lines[j].replace(/(?<!\\)\|(?:\|)+/g, '|');
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
    return renderHtml(html, ctx);
  }

  // ---------- message ----------
  function render(post, opts) {
    opts = opts || {};
    const rm = (post && post.rich_message) || {};
    const ctx = { mediaBase: opts.mediaBase || '', mediaList: rm.media || [] };
    const body = el('div', { class: 'tg-body', dir: rm.is_rtl ? 'rtl' : null });
    try {
      if (rm.blocks) body.append(renderBlocks(rm.blocks, ctx));
      else if (rm.html) body.append(renderHtml(rm.html, ctx));
      else if (rm.markdown) body.append(renderMarkdown(rm.markdown, ctx));
      else body.append(el('p', { class: 'tg-empty', text: 'Пустой пост' }));
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
        const num = list.tagName === 'OL' ? ' ' + (n.getAttribute('value') || (parseInt(list.getAttribute('start') || '1', 10) + [...list.children].indexOf(n))) : '';
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

  global.RichRender = { render, renderRT, renderBlocks, formatTime, mediaSrc, safeUrl, outline };
})(window);
