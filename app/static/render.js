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
    'type', 'value', 'checked', 'open', 'bordered', 'striped', 'compact', 'collapsed', 'expandable', 'tg-spoiler', 'lang',
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
          (isTg ? true : ALLOWED_ATTRS.has(name) || (name === 'class' && a.value === 'tg-spoiler'));
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
      if (tag === 'tg-spoiler' || (tag === 'span' && node.classList.contains('tg-spoiler'))) rep = spoilerEl(kids());
      else if (tag === 'tg-emoji') rep = el('span', { class: 'tg-emoji', title: 'custom emoji ' + attr('emoji-id') }, kids());
      else if (tag === 'tg-math') rep = math(node.textContent, false);
      else if (tag === 'tg-math-block') rep = math(node.textContent, true);
      else if (tag === 'tg-time') { const u = parseInt(attr('unix'), 10); rep = el('span', { class: 'tg-time', title: formatTime(u, 'wDT') }, node.textContent.trim() ? kids() : formatTime(u, attr('format'))); }
      else if (tag === 'tg-collage') {
        const caps = kids().filter((k) => k.nodeType === 1 && k.tagName.toLowerCase() === 'figcaption');
        rep = el('figure', { class: 'tg-collage' }, el('div', { class: 'tg-collage-grid' }, kids().filter((k) => !caps.includes(k))), caps);
      }
      else if (tag === 'tg-slideshow') rep = slideshow(kids(), null);
      else if (tag === 'tg-map') rep = mapEl(attr('lat') ?? attr('latitude'), attr('long') ?? attr('lon') ?? attr('longitude'), attr('zoom'), attr('width'), attr('height'));
      else if (tag === 'tg-thinking') rep = el('div', { class: 'tg-thinking' }, kids());
      else if (tag === 'tg-reference') rep = el('span', { class: 'tg-ref', id: 'ref-' + attr('name') }, kids());
      else if (tag === 'tg-button-row') rep = el('div', { class: 'tg-btn-row tg-align-' + (attr('align') || 'center') }, kids());
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
      } else if (tag === 'blockquote' && (node.hasAttribute('collapsed') || node.hasAttribute('expandable'))) {
        rep = expandable(el('div', {}, kids()), null);
      } else if (tag === 'aside') { node.classList.add('tg-pullquote'); }
      else if (tag === 'table') {
        ['bordered', 'striped', 'compact'].forEach((k) => node.hasAttribute(k) && node.classList.add(k));
        node.classList.add('tg-table');
        const wrap = el('div', { class: 'tg-table-wrap' });
        node.replaceWith(wrap); wrap.append(node); return;
      } else if (tag === 'li' && node.firstElementChild && node.firstElementChild.matches('input[type=checkbox]')) {
        node.classList.add('tg-task'); node.firstElementChild.disabled = true;
      } else if (tag === 'a' && attr('href')) {
        const h = attr('href');
        if (h.startsWith('#')) node.setAttribute('href', '#ref-' + h.slice(1));
        else { node.setAttribute('target', '_blank'); node.setAttribute('rel', 'noopener'); }
      } else if (tag.startsWith('tg-')) {
        rep = unknown('тег <' + tag + '>', kids());
      }
      if (rep) node.replaceWith(rep);
    };
    walk(root);
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

  /** @type {any} */
  let md = null;
  function renderMarkdown(src, ctx) {
    if (!md && global.markdownit) md = global.markdownit({ html: true, linkify: true, breaks: false });
    let html;
    if (md) {
      html = md.render(src);
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

  global.RichRender = { render, renderRT, renderBlocks, formatTime, mediaSrc, safeUrl };
})(window);
