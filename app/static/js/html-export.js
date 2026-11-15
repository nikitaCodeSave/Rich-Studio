// «В HTML»: the visual post as Telegram's rich HTML, put into the HTML tab only on request (docs/decisions/0018).
// What every block and text element becomes is written by hand, like the preview in render.js, after
// core.telegram.org/bots/api#rich-html-style. BLOCK_FIELDS and INLINE_FIELDS are the table: spec/coverage.py
// (tests/test_spec_coverage.py) lists the spec's types missing there. Nothing is lost quietly: a type or a field not
// in the table, or a value HTML cannot say, refuses the whole export with the list of what and where, and the HTML
// tab stays as it was. The result is read back the way the preview reads HTML and must outline the same as the blocks.
import { openDialog, closeDialog } from './dialogs.js';
import { clone, h, toast } from './dom.js';
import { BLOCK_LABELS, RT_LABELS } from './labels.js';
import { S } from './state.js';

// block type → the fields it carries into HTML (besides type)
export const BLOCK_FIELDS = {
  paragraph: ['text'],
  heading: ['text', 'size'],
  pre: ['text', 'language'],
  footer: ['text'],
  divider: [],
  mathematical_expression: ['expression'],
  anchor: ['name'],
  list: ['items'],
  blockquote: ['blocks', 'credit'],
  expandable_blockquote: ['text', 'credit'],
  pullquote: ['text', 'credit'],
  collage: ['blocks', 'caption'],
  slideshow: ['blocks', 'caption'],
  table: ['cells', 'is_bordered', 'is_striped', 'is_compact', 'caption'],
  details: ['summary', 'blocks', 'is_open'],
  map: ['location', 'zoom', 'caption'],
  buttons: ['buttons', 'align'],
  photo: ['photo', 'caption'],
  video: ['video', 'caption'],
  animation: ['animation', 'caption'],
  audio: ['audio', 'caption'],
  document: ['document', 'caption'],
  voice_note: ['voice_note', 'caption'],
  thinking: ['text'],
};

// text element type → its fields that go into HTML (besides type)
export const INLINE_FIELDS = {
  bold: ['text'],
  italic: ['text'],
  underline: ['text'],
  strikethrough: ['text'],
  spoiler: ['text'],
  subscript: ['text'],
  superscript: ['text'],
  marked: ['text'],
  code: ['text'],
  url: ['text', 'url'],
  text_mention: ['text', 'user'],
  email_address: ['text', 'email_address'],
  phone_number: ['text', 'phone_number'],
  mention: ['text', 'username'],
  hashtag: ['text', 'hashtag'],
  cashtag: ['text', 'cashtag'],
  bot_command: ['text', 'bot_command'],
  bank_card_number: ['text', 'bank_card_number'],
  ton_address: ['text', 'address'],
  date_time: ['text', 'unix_time', 'date_time_format'],
  custom_emoji: ['custom_emoji_id', 'alternative_text'],
  mathematical_expression: ['expression'],
  anchor: ['name'],
  anchor_link: ['text', 'anchor_name'],
  reference: ['text', 'name'],
  reference_link: ['text', 'reference_name'],
  button: ['button'],
};

// the objects inside blocks
const PARTS = {
  item: ['blocks', 'has_checkbox', 'is_checked', 'value', 'type'],
  cell: ['text', 'is_header', 'colspan', 'rowspan', 'align', 'valign'],
  caption: ['text', 'credit'],
  location: ['latitude', 'longitude'],
};
// RichMessageButton: exactly one action, written as <tg-button type="…"> with these attributes
const ACTIONS = ['url', 'callback_data', 'web_app', 'login_url', 'switch_inline_query', 'switch_inline_query_current_chat', 'switch_inline_query_chosen_chat', 'copy_text', 'disabled'];
const ACTION_FIELDS = {
  web_app: ['url'],
  login_url: ['url', 'forward_text', 'request_write_access'],
  switch_inline_query_chosen_chat: ['query', 'allow_user_chats', 'allow_bot_chats', 'allow_group_chats', 'allow_channel_chats'],
  copy_text: ['text'],
  disabled: [],
};
// in the table without fields (null): HTML has no way to say them, so a post with them is refused with the reason
// written here; there are none now
/** @type {Record<string, string>} */
export const NO_HTML = {};
// found in the text by Telegram itself: in HTML they exist only as this text. A TON address has no tag, but Telegram
// finds one in HTML text (Bot API 10.4, tests/telegram_parse/captured.json: html-detected-card-ton)
const DETECTED = { mention: ['@', 'username'], hashtag: ['#', 'hashtag'], cashtag: ['$', 'cashtag'], bot_command: ['/', 'bot_command'], bank_card_number: ['', 'bank_card_number'], ton_address: ['', 'address'] };
// <a href> Telegram reads as an e-mail, a phone, a mention or a link inside the post, not as a plain link
/** @type {[RegExp, string][]} */
const SPECIAL_HREF = [[/^#/, 'ссылку на якорь или сноску'], [/^mailto:/i, 'e-mail'], [/^tel:/i, 'телефон'], [/^tg:\/\/user\?/i, 'упоминание']];
// block → [tag, tg:// link]: the spec gives the tag (InputRichBlockAnimation «<video>», VoiceNote «<audio>»), the docs
// only tg://photo, video, document and audio links; what kind the file is, its entry in media says
const MEDIA_TAGS = { photo: ['img', 'photo'], video: ['video', 'video'], animation: ['video', 'video'], audio: ['audio', 'audio'], voice_note: ['audio', 'audio'], document: ['tg-document', 'document'] };
const DEFAULT_ALIGN = { th: 'center', td: 'left' }; // Telegram's own, as tests/telegram_parse/captured.json shows
const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
// a type is looked up only among the table's own keys: «constructor» or «__proto__» are not types
const own = (table, key) => (typeof key === 'string' && Object.hasOwn(table, key) ? table[key] : undefined);
const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
// a " in an address, written &quot; in HTML, makes Telegram drop a link and refuse a button (BUTTON_URL_INVALID);
// %22 is the same address and goes through (tests/telegram_parse/captured.json: html-href-quot, html-href-pct22)
const noQuote = (url) => (typeof url === 'string' ? url.replace(/"/g, '%22') : url);
const plain = (v) => typeof v === 'string' ? v : Array.isArray(v) ? v.map(plain).join('') : isObj(v) ? plain(v.text ?? v.alternative_text ?? '') : '';

/**
 * The blocks as rich HTML. Works on what it is given without changing it.
 * @param {any[]} blocks
 * @param {{ media?: any[], skipEntities?: boolean, layout?: boolean }} [opts] media: rich_message.media already there
 *   (its ids stay taken, an entry with the same file is used again); skipEntities: rich_message.skip_entity_detection
 *   is on; layout: false gives the HTML without line breaks and indents between blocks, the one that is checked
 * @returns {{ ok: true, html: string, media: any[], warnings: string[], notes: string[] } | { ok: false, problems: string[], diff?: { blocks: string, html: string } }}
 *   warnings: where spaces in a row, tabs or an indent after a line break are, which HTML squeezes into one space;
 *   notes: what goes into HTML as the same text but will not be the same element (a card number with spaces)
 */
export function blocksToHtml(blocks, opts = {}) {
  // line breaks and indents between blocks are for the person reading the source; the check reads the HTML without
  // them, because the outline counts them as spaces where neither Telegram nor the preview shows anything
  const tight = write(blocks, opts, false);
  if (tight.problems.length) return { ok: false, problems: tight.problems };
  const warnings = [...tight.squeezed];
  if (tight.html) {
    // Telegram finds TON addresses in the text of blocks and HTML alike, and with detection off in neither
    const detection = opts.skipEntities ? { skip_entity_detection: true } : {};
    const theirs = lookOf({ blocks, ...detection });
    const mine = lookOf({ html: tight.html, media: tight.media, ...detection });
    if (mine !== theirs) return { ok: false, problems: ['В предпросмотре HTML выглядит иначе, чем визуальный пост'], diff: firstDifference(theirs, mine) };
  }
  const out = opts.layout === false ? tight : write(blocks, opts, true);
  return { ok: true, html: out.html, media: out.media, warnings, notes: tight.notes };
}

/** The post as the preview reads it (RichRender.outline): structure and text, not the values of attributes (links,
 * rowspan, map points, tg-time, emoji ids), which tests/html_export.mjs checks by themselves. In code a line break
 * is a line break: from blocks the preview draws it as <br>, from HTML source as the text's own newline, which the
 * outline turns into a space. The kind of a tg:// file the preview takes from its entry in media, as Telegram does. */
export function lookOf(richMessage) {
  return RichRender.outline({ rich_message: richMessage })
    .replace(/<pre[^>]*>.*?<\/pre>/gs, (p) => p.replace(/<br>/g, ' ').replace(/ +/g, ' ').replace(/(<pre[^>]*>) | (<\/pre>)/g, '$1$2'));
}

function firstDifference(a, b) {
  let i = 0;
  while (i < a.length && a[i] === b[i]) i++;
  const from = Math.max(0, i - 30);
  const cut = (s) => (from ? '…' : '') + s.slice(from, i + 50) + (s.length > i + 50 ? '…' : '');
  return { blocks: cut(a), html: cut(b) };
}

function write(blocks, opts, pretty) {
  /** @type {string[]} */
  const problems = [];
  /** @type {Set<string>} */
  const squeezed = new Set(); // Telegram reads HTML whitespace as HTML does (captured example-post-extras)
  /** @type {string[]} */
  const notes = [];
  const media = [...(opts.media || [])];
  const taken = new Set(media.map((m) => m && m.id));
  const used = new Set();
  const say = (where, what) => problems.push(`${where}: ${what}`);
  const nl = (ind) => (pretty ? '\n' + ind : '');
  const attrs = (list) => list.filter(([, v]) => v !== undefined && v !== false).map(([k, v]) => (v === true ? ' ' + k : ` ${k}="${esc(v).replace(/"/g, '&quot;')}"`)).join('');
  const known = (obj, fields, where) => {
    for (const k of Object.keys(obj)) if (!fields.includes(k) && obj[k] !== undefined) say(where, `поле ${k} не переносится в HTML`);
  };

  // ---------- text ----------
  const rt = (v, where, pre = false) => {
    if (v === undefined || v === null) return '';
    if (typeof v === 'string') {
      if (pre) return esc(v);
      if (/\t| {2}|\n /.test(v)) squeezed.add(where);
      return esc(v).replace(/\n/g, '<br/>');
    }
    if (Array.isArray(v)) return v.map((x) => rt(x, where, pre)).join('');
    if (!isObj(v)) { say(where, 'текст не по спецификации: ' + JSON.stringify(v)); return ''; }
    const t = v.type;
    if (typeof t !== 'string') { say(where, 'элемент текста без type: ' + JSON.stringify(v)); return ''; }
    const here = `${where} › «${own(RT_LABELS, t) || t}»`;
    const fields = own(INLINE_FIELDS, t);
    if (!fields) { say(here, own(NO_HTML, t) || `элемент текста «${t}» не переносится в HTML`); return ''; }
    known(v, ['type', ...fields], here);
    const inner = () => rt(v.text, here, pre);
    const tag = (name, list = []) => `<${name}${attrs(list)}>${inner()}</${name}>`;
    switch (t) {
      case 'bold': return tag('b');
      case 'italic': return tag('i');
      case 'underline': return tag('u');
      case 'strikethrough': return tag('s');
      case 'spoiler': return tag('tg-spoiler');
      case 'subscript': return tag('sub');
      case 'superscript': return tag('sup');
      case 'marked': return tag('mark');
      case 'code': return tag('code');
      case 'url': {
        const special = SPECIAL_HREF.find(([re]) => re.test(String(v.url || '')));
        if (special) say(here, `адрес «${v.url}» Telegram прочитает в HTML как ${special[1]}, а не как ссылку`);
        return tag('a', [['href', noQuote(v.url)]]);
      }
      case 'text_mention': {
        const id = isObj(v.user) ? v.user.id : undefined;
        if (!Number.isInteger(id)) say(here, 'нет числового user.id');
        return tag('a', [['href', 'tg://user?id=' + id]]);
      }
      case 'email_address': return tag('a', [['href', 'mailto:' + v.email_address]]);
      case 'phone_number': return tag('a', [['href', 'tel:' + v.phone_number]]);
      case 'mention': case 'hashtag': case 'cashtag': case 'bot_command': case 'bank_card_number': case 'ton_address': {
        const [prefix, key] = DETECTED[t];
        const shown = plain(v.text);
        const same = t === 'bank_card_number' ? shown.replace(/\D/g, '') === String(v[key] ?? '').replace(/\D/g, '') : shown === prefix + v[key];
        if (opts.skipEntities) say(here, 'при «Не распознавать ссылки, @, #…» (skip_entity_detection) Telegram не найдёт его в HTML');
        else if (!same) say(here, `текст «${shown}» не совпадает с ${prefix}${v[key]}: в HTML Telegram находит такие элементы только по тексту`);
        // the TON addresses Telegram finds in text are the preview's: user-friendly EQ…/UQ… of 48 characters with a right
        // checksum (RichRender.isTonAddress); a raw «0:83df…» or a mistyped address stays text
        else if (t === 'ton_address' && !RichRender.isTonAddress(shown)) say(here, `адрес «${shown}» Telegram в тексте не найдёт: он находит только адреса EQ… и UQ… из 48 символов с верной контрольной суммой`);
        // the text stays as the author wrote it; written without the spaces it would read otherwise than the blocks
        else if (t === 'bank_card_number' && /\D/.test(shown)) notes.push(`${here}: «${shown}» в HTML останется текстом, номером карты Telegram делает только цифры без пробелов`);
        return inner();
      }
      case 'date_time':
        if (!plain(v.text).trim()) say(here, 'дата без текста: пустой <tg-time> Telegram выбросит');
        return tag('tg-time', [['unix', v.unix_time], ['format', v.date_time_format]]);
      case 'custom_emoji': return `<tg-emoji${attrs([['emoji-id', v.custom_emoji_id]])}>${esc(v.alternative_text ?? '')}</tg-emoji>`;
      case 'mathematical_expression': return `<tg-math>${esc(v.expression ?? '')}</tg-math>`;
      case 'anchor': return `<a${attrs([['name', v.name ?? '']])}></a>`;
      case 'anchor_link': return tag('a', [['href', '#' + (v.anchor_name ?? '')]]);
      case 'reference': return tag('tg-reference', [['name', v.name ?? '']]);
      case 'reference_link': return tag('a', [['href', '#' + (v.reference_name ?? '')]]);
      case 'button': return button(v.button, here);
      default: say(here, `для «${t}» нет записи в HTML`); return '';
    }
  };

  const button = (b, where) => {
    if (!isObj(b)) { say(where, 'кнопка не по спецификации'); return ''; }
    known(b, ['text', 'style', ...ACTIONS], where);
    const actions = ACTIONS.filter((k) => b[k] !== undefined);
    if (actions.length !== 1) { say(where, actions.length ? 'у кнопки несколько действий: ' + actions.join(', ') : 'у кнопки нет действия'); return ''; }
    const k = actions[0];
    const v = b[k];
    if (ACTION_FIELDS[k]) {
      if (!isObj(v)) { say(where, `${k} не по спецификации`); return ''; }
      known(v, ACTION_FIELDS[k], where);
    }
    const list = [['type', k], ['style', b.style]];
    if (k === 'url') list.push(['url', noQuote(v)]);
    else if (k === 'callback_data') list.push(['data', v]);
    else if (k === 'web_app') list.push(['url', noQuote(v.url)]);
    else if (k === 'login_url') list.push(['url', noQuote(v.url)], ['forward-text', v.forward_text], ['request-write-access', !!v.request_write_access]);
    else if (k === 'switch_inline_query' || k === 'switch_inline_query_current_chat') list.push(['query', v]);
    else if (k === 'switch_inline_query_chosen_chat') list.push(['query', v.query], ...['user', 'bot', 'group', 'channel'].map((c) => [`allow-${c}-chats`, !!v[`allow_${c}_chats`]]));
    else if (k === 'copy_text') list.push(['text', v.text]);
    return `<tg-button${attrs(list)}>${rt(b.text, where)}</tg-button>`;
  };

  // ---------- blocks ----------
  const caption = (cap, where) => {
    if (cap === undefined) return '';
    if (!isObj(cap) || 'type' in cap) { say(where + ' › подпись', 'подпись не по спецификации: нужен объект {text, credit}'); return ''; }
    known(cap, PARTS.caption, where + ' › подпись');
    const credit = cap.credit !== undefined ? `<cite>${rt(cap.credit, where + ' › автор')}</cite>` : '';
    return `<figcaption>${rt(cap.text, where + ' › подпись')}${credit}</figcaption>`;
  };
  const figure = (inner, cap, where) => (cap === undefined ? inner : `<figure>${inner}${caption(cap, where)}</figure>`);
  const credit = (b, where) => (b.credit !== undefined ? `<cite>${rt(b.credit, where + ' › автор')}</cite>` : '');
  const children = (list, where, ind) => (Array.isArray(list) ? list : []).map((x, i) => nl(ind) + block(x, `${where} › блок ${i + 1}`, ind)).join('');

  const mediaId = (kind, m) => {
    const same = media.find((e) => isObj(e) && e.id && !used.has(e.id) && JSON.stringify(e.media) === JSON.stringify(m));
    if (same) { used.add(same.id); return same.id; }
    const base = kind === 'voice_note' ? 'voice' : kind;
    let i = 1;
    while (taken.has(base + i)) i++;
    taken.add(base + i); used.add(base + i);
    media.push({ id: base + i, media: clone(m) });
    return base + i;
  };

  const list = (b, where, ind) => {
    const items = Array.isArray(b.items) ? b.items : [];
    const ordered = items.some((it) => isObj(it) && (it.value !== undefined || it.type !== undefined));
    const values = items.map((it) => (isObj(it) ? it.value : undefined));
    const types = items.map((it) => (isObj(it) ? it.type : undefined));
    const counted = values.every((v, i) => Number.isInteger(v) && v === values[0] + i);
    const oneType = types.every((t) => t !== undefined && t === types[0]);
    const lis = items.map((it, i) => {
      const at = `${where} › пункт ${i + 1}`;
      if (!isObj(it)) { say(at, 'пункт не по спецификации'); return ''; }
      known(it, PARTS.item, at);
      const itemAttrs = [['value', counted ? undefined : it.value], ['type', oneType ? undefined : it.type]];
      const box = it.has_checkbox ? `<input type="checkbox"${it.is_checked ? ' checked' : ''}>` : '';
      const inner = Array.isArray(it.blocks) ? it.blocks : [];
      // the first paragraph is the item's own text, as Telegram reads <li>text</li>
      const first = isObj(inner[0]) && inner[0].type === 'paragraph' ? inner[0] : null;
      let head = '';
      if (first) { known(first, ['type', ...BLOCK_FIELDS.paragraph], `${at} › блок 1 («${BLOCK_LABELS.paragraph}»)`); head = rt(first.text, `${at} › блок 1 («${BLOCK_LABELS.paragraph}»)`); }
      const rest = inner.map((x, j) => (first && j === 0 ? '' : nl(ind + '    ') + block(x, `${at} › блок ${j + 1}`, ind + '    '))).join('');
      return nl(ind + '  ') + `<li${attrs(itemAttrs)}>${box}${head}${rest}${rest ? nl(ind + '  ') : ''}</li>`;
    }).join('');
    const tag = ordered ? 'ol' : 'ul';
    const head = ordered ? [['start', counted && values[0] !== 1 ? values[0] : undefined], ['type', oneType && types[0] !== '1' ? types[0] : undefined]] : [];
    return `<${tag}${attrs(head)}>${lis}${nl(ind)}</${tag}>`;
  };

  const table = (b, where, ind) => {
    const rows = (Array.isArray(b.cells) ? b.cells : []).map((row, r) => {
      const cells = (Array.isArray(row) ? row : []).map((c, i) => {
        const at = `${where} › ячейка ${r + 1}:${i + 1}`;
        if (!isObj(c)) { say(at, 'ячейка не по спецификации'); return ''; }
        known(c, PARTS.cell, at);
        const tag = c.is_header ? 'th' : 'td';
        const list = [['colspan', c.colspan > 1 ? c.colspan : undefined], ['rowspan', c.rowspan > 1 ? c.rowspan : undefined],
          ['align', c.align !== DEFAULT_ALIGN[tag] ? c.align : undefined], ['valign', c.valign !== 'middle' ? c.valign : undefined]];
        return `<${tag}${attrs(list)}>${rt(c.text, at)}</${tag}>`;
      }).join('');
      return nl(ind + '  ') + `<tr>${cells}</tr>`;
    }).join('');
    const cap = b.caption !== undefined ? nl(ind + '  ') + `<caption>${rt(b.caption, where + ' › подпись')}</caption>` : '';
    return `<table${attrs([['bordered', !!b.is_bordered], ['striped', !!b.is_striped], ['compact', !!b.is_compact]])}>${cap}${rows}${nl(ind)}</table>`;
  };

  const block = (b, where, ind) => {
    if (!isObj(b)) { say(where, 'блок не по спецификации'); return ''; }
    const t = b.type;
    const here = `${where} («${own(BLOCK_LABELS, t) || t}»)`;
    const fields = own(BLOCK_FIELDS, t);
    if (!fields) { say(here, `блок «${t}» не переносится в HTML`); return ''; }
    known(b, ['type', ...fields], here);
    const text = (v) => rt(v, here);
    const inner = ind + '  ';
    switch (t) {
      case 'paragraph': return `<p>${text(b.text)}</p>`;
      case 'heading': {
        if (!(Number.isInteger(b.size) && b.size >= 1 && b.size <= 6)) { say(here, `размер ${b.size}: в HTML есть только h1–h6`); return ''; }
        return `<h${b.size}>${text(b.text)}</h${b.size}>`;
      }
      case 'pre': {
        if (b.language === undefined) return `<pre>${rt(b.text, here, true)}</pre>`;
        if (!/^[\w+#-]+$/.test(String(b.language))) { say(here, `язык «${b.language}» не записать в class="language-…"`); return ''; }
        return `<pre><code class="language-${b.language}">${rt(b.text, here, true)}</code></pre>`;
      }
      case 'footer': return `<footer>${text(b.text)}</footer>`;
      case 'divider': return '<hr/>';
      case 'mathematical_expression': return `<tg-math-block>${esc(b.expression ?? '')}</tg-math-block>`;
      case 'anchor': return `<a${attrs([['name', b.name ?? '']])}></a>`;
      case 'thinking': return `<tg-thinking>${text(b.text)}</tg-thinking>`;
      case 'list': return list(b, here, ind);
      case 'blockquote': return `<blockquote>${children(b.blocks, here, inner)}${b.credit !== undefined ? nl(inner) + credit(b, here) : ''}${nl(ind)}</blockquote>`;
      case 'expandable_blockquote': return `<blockquote expandable>${text(b.text)}${credit(b, here)}</blockquote>`;
      case 'pullquote': return `<aside>${text(b.text)}${credit(b, here)}</aside>`;
      // no line breaks inside: a collage or a slideshow takes whatever is in it as its items
      case 'collage': case 'slideshow':
        return `<tg-${t}>${(Array.isArray(b.blocks) ? b.blocks : []).map((x, i) => block(x, `${here} › блок ${i + 1}`, '')).join('')}${caption(b.caption, here)}</tg-${t}>`;
      case 'table': return table(b, here, ind);
      case 'details': return `<details${b.is_open ? ' open' : ''}>${nl(inner)}<summary>${rt(b.summary, here + ' › заголовок')}</summary>${children(b.blocks, here, inner)}${nl(ind)}</details>`;
      case 'map': {
        const loc = b.location;
        if (!isObj(loc)) { say(here, 'нет точки (location)'); return ''; }
        known(loc, PARTS.location, here + ' › точка');
        return figure(`<tg-map${attrs([['lat', loc.latitude], ['long', loc.longitude], ['zoom', b.zoom]])}></tg-map>`, b.caption, here);
      }
      case 'buttons': {
        const btns = (Array.isArray(b.buttons) ? b.buttons : []).map((x, i) => nl(inner) + button(x, `${here} › кнопка ${i + 1}`)).join('');
        return `<tg-button-row${attrs([['align', b.align]])}>${btns}${nl(ind)}</tg-button-row>`;
      }
      case 'photo': case 'video': case 'animation': case 'audio': case 'document': case 'voice_note': {
        const m = b[t];
        if (!isObj(m) || typeof m.media !== 'string') { say(here, 'нет файла'); return ''; }
        const [tag, link] = MEDIA_TAGS[t];
        const src = attrs([['src', `tg://${link}?id=${mediaId(t, m)}`], ['tg-spoiler', !!m.has_spoiler]]);
        return figure(tag === 'img' ? `<img${src}/>` : `<${tag}${src}></${tag}>`, b.caption, here);
      }
      default: say(here, `для «${t}» нет записи в HTML`); return '';
    }
  };

  const html = (Array.isArray(blocks) ? blocks : []).map((b, i) => block(b, `блок ${i + 1}`, '')).join(pretty ? '\n' : '');
  return { html, media, problems, squeezed, notes };
}

// ---------- the button ----------
/** «В HTML» in the visual mode: the HTML tab gets the post, after a question if it holds text of its own; on a
 * refusal it stays as it was. `apply` shows the HTML tab once S.buf.html and S.media are set. */
export function exportToHtml(apply) {
  if (!S.buf.blocks.length) { toast('В визуальном посте пока нет блоков', 'err'); return; }
  const r = blocksToHtml(clone(S.buf.blocks), { media: clone(S.media), skipEntities: !!S.flags.skip_entity_detection });
  if (!r.ok) { refused(r); return; }
  const put = () => {
    const added = r.media.length - S.media.length;
    S.buf.html = r.html;
    S.media = r.media;
    apply();
    toast('Пост переведён в HTML' + (added ? `, медиа добавлены в список (${added})` : '') + '. Блоки остались во вкладке «Визуально».', 'ok');
    for (const note of r.notes) toast(note);
    // not a refusal: the text stays, only its extra spaces go
    if (r.warnings.length) toast('В HTML несколько пробелов подряд, табуляция и отступ после переноса строки станут одним пробелом: ' + r.warnings.slice(0, 3).join('; ') + (r.warnings.length > 3 ? ` и ещё ${r.warnings.length - 3}` : '') + '.');
  };
  if (!S.buf.html.trim() || S.buf.html === r.html) { put(); return; }
  openDialog([
    h('h3', { text: 'Заменить текст во вкладке HTML?' }),
    h('p', { text: 'Во вкладке HTML уже есть свой текст. Он заменится HTML визуального поста; вернуть прежний можно отменой (↶).' }),
  ], [h('button', { class: 'ghost', value: 'cancel', text: 'Отмена' }), h('button', { type: 'button', class: 'primary', text: 'Заменить', onclick: () => { closeDialog(); put(); } })]);
}

function refused(r) {
  const shown = r.problems.slice(0, 20);
  openDialog([
    h('h3', { text: 'Пост не переведён в HTML' }),
    h('p', { text: 'Вкладка HTML не изменена: в HTML пропала бы часть поста.' }),
    h('ul', { class: 'export-problems' }, shown.map((p) => h('li', { text: p }))),
    r.problems.length > shown.length ? h('p', { class: 'muted small', text: `и ещё ${r.problems.length - shown.length}` }) : null,
    r.diff ? h('div', { class: 'export-diff' }, h('span', { class: 'muted', text: 'Визуально:' }), h('code', { text: r.diff.blocks }), h('span', { class: 'muted', text: 'HTML:' }), h('code', { text: r.diff.html })) : null,
  ]);
}
