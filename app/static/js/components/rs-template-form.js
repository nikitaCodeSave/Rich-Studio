// <rs-template-form>: a post template inside a dialog.
// mode "edit": the template's title, its fields (label, type, required, default) and the strings of its post that
//   can become a field: a whole JSON string, and in HTML a text between tags or a link in href/src. «→ поле» puts
//   {{key}} in place of the string and makes it the field's default; ✕ on a field puts the default back.
//   A {{key}} typed into the post by hand shows up as a field too. `value`: {title, post, fields} for /api/templates.
// mode "fill": a form from the fields; `value`: {title, values} for /api/templates/{id}/draft.
// The strings are found by walking the post's JSON, not by a list of block types. `fixed` holds "name=value" pairs
// the spec fixes (a block's type, an enum value): they are never a field. Nor is what services/templates.py refuses: a
// string next to a parse_mode or entities (a media caption), and a button's callback_data with an answer in callbacks
// is listed with why instead of «→ поле». The page does the requests and shows their errors in `error`.
import { css, html, LitElement, nothing } from '../../vendor/lit-core.min.js';
import { clone } from '../dom.js';
import { FIELD_LABELS } from '../labels.js';

const VAR = /\{\{([A-Za-z_][A-Za-z0-9_]{0,31})\}\}/g;
const hasVar = (/** @type {string} */ s) => /\{\{[A-Za-z_][A-Za-z0-9_]{0,31}\}\}/.test(s);
const TYPES = { text: 'Строка', multiline: 'Несколько строк', url: 'Ссылка или файл' };
// a tag (quoted values may hold >) or a comment of an HTML source
const TAG = /<!--[\s\S]*?(?:-->|$)|<(?:"[^"]*"|'[^']*'|[^"'>])*>?/g;
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0' };

/** HTML text as the person reads it. @param {string} s */
function unescapeHtml(s) {
  return s.replace(/&(?:#(\d+)|#x([0-9a-f]+)|([a-z]+));/gi, (m, dec, hex, name) => {
    if (name) return ENTITIES[name.toLowerCase()] ?? m;
    const cp = dec ? parseInt(dec, 10) : parseInt(hex, 16);
    return cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : m;
  });
}
/** @param {string} s */
const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#x27;');

/** @typedef {(string | number)[]} JsonPath */
/** @typedef {{ path: JsonPath, start?: number, end?: number, value: string, where: string, url: boolean, why?: string }} Spot */

/** In HTML: the text between tags and the links in href/src. @param {string} src @param {JsonPath} path @param {Spot[]} out */
function htmlSpots(src, path, out) {
  let last = 0;
  const text = (a, b) => {
    const raw = src.slice(a, b);
    const body = raw.trim();
    const start = a + raw.length - raw.trimStart().length;
    if (body && !hasVar(body)) out.push({ path, start, end: start + body.length, value: unescapeHtml(body), where: 'Текст', url: false });
  };
  for (const m of src.matchAll(TAG)) {
    const at = m.index || 0;
    text(last, at);
    last = at + m[0].length;
    for (const a of m[0].matchAll(/\s(href|src)\s*=\s*(?:"([^"]*)"|'([^']*)')/gi)) {
      const v = a[2] ?? a[3];
      if (!v || hasVar(v)) continue;
      const start = at + (a.index || 0) + a[0].length - v.length - 1;
      out.push({ path, start, end: start + v.length, value: unescapeHtml(v), where: a[1].toLowerCase() === 'href' ? 'Ссылка' : 'Адрес файла', url: true });
    }
  }
  text(last, src.length);
}

// the post's answers to its buttons (services/callbacks.py): its keys are callback_data, not names of fields
const CALLBACKS = 'callbacks';

/** The JSON key a string stands under, as services/templates.py _key reads it: for a string in a list, the key of the
 * list; a string answer in callbacks stands under its button's callback_data, which names no field: "" then.
 * @param {JsonPath} path */
function keyOf(path) {
  for (let i = path.length - 1; i >= 0; i--) {
    const p = path[i];
    if (typeof p === 'string') return i === 1 && path[0] === CALLBACKS ? '' : p;
  }
  return '';
}

/** How Telegram reads the string, as _source: the html or markdown it stands under, any other string as it is.
 * @param {JsonPath} path */
function sourceOf(path) {
  const key = keyOf(path);
  return (key === 'html' || key === 'markdown') && path[path.length - 1] === key ? key : 'text';
}

/** Set as Python reads it: an empty string, list or object is not. @param {unknown} v */
const filled = (v) => (Array.isArray(v) ? v.length > 0 : v && typeof v === 'object' ? Object.keys(v).length > 0 : !!v);

/** A parse_mode or entities next to the string (a media caption, a poll question), as _formatted_by: Telegram would
 * format the value by them, so the service refuses a field there. @param {Json | null} parent */
function formattedBy(parent) {
  return Object.entries(parent || {}).some(([k, v]) => filled(v) && (k.endsWith('parse_mode') || k.endsWith('entities')));
}

// a button of the post (not of a rich reply: the studio does not answer those) whose callback_data has an answer in
// callbacks, as _check refuses it: a key of callbacks is never a field, so another value would lose the answer
const CALLBACK_DATA = 'callback_data';
const ANSWERED = 'связан с ответом на нажатие в callbacks';
const ANSWERED_WHY = 'Ответ на нажатие ищется по этому значению среди ключей callbacks, а ключ полем не меняется: после заполнения кнопка осталась бы без ответа';

/** Strings of the post that can become a field. @param {Json} post @param {Set<string>} fixed */
function spots(post, fixed) {
  /** @type {Spot[]} */
  const out = [];
  const answers = post[CALLBACKS];
  const answered = new Set(answers && typeof answers === 'object' && !Array.isArray(answers) ? Object.keys(answers) : []);
  eachString(post, (v, path, parent) => {
    const key = keyOf(path);
    const source = sourceOf(path);
    if (source === 'markdown' || formattedBy(parent)) return;
    if (source === 'html') { htmlSpots(v, path, out); return; }
    if (!v.trim() || hasVar(v) || fixed.has(key + '=' + v)) return;
    /** @type {Spot} */
    const spot = { path, value: v, where: FIELD_LABELS[key] || key || 'Ответ на кнопку', url: key === 'url' || /^(https?:|tg:|upload:)/.test(v) };
    if (key === CALLBACK_DATA && path[0] !== CALLBACKS && answered.has(v)) spot.why = ANSWERED;
    out.push(spot);
  });
  return out;
}

/** Every string of the value with its path and the object it belongs to (for a string in a list, the object holding
 * the list), as services/templates.py _strings walks it. Nothing is copied: it runs on every render.
 * @param {(s: string, path: JsonPath, parent: Json | null) => void} fn */
function eachString(v, fn, /** @type {JsonPath} */ path = [], /** @type {Json | null} */ parent = null) {
  if (typeof v === 'string') fn(v, path, parent);
  else if (Array.isArray(v)) v.forEach((x, i) => eachString(x, fn, [...path, i], parent));
  else if (v && typeof v === 'object') for (const [k, x] of Object.entries(v)) eachString(x, fn, [...path, k], v);
}

/** Every string of the value through fn(string, its path). @param {(s: string, path: JsonPath) => string} fn */
function mapStrings(v, fn, /** @type {JsonPath} */ path = []) {
  if (typeof v === 'string') return fn(v, path);
  if (Array.isArray(v)) return v.map((x, i) => mapStrings(x, fn, [...path, i]));
  if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, mapStrings(x, fn, [...path, k])]));
  return v;
}

/** The {{key}} names in the post, in order. @param {Json} post */
function usedKeys(post) {
  /** @type {string[]} */
  const keys = [];
  eachString(post, (s) => { for (const m of s.matchAll(VAR)) if (!keys.includes(m[1])) keys.push(m[1]); });
  return keys;
}

const short = (/** @type {string} */ s) => (s.length > 90 ? s.slice(0, 90) + '…' : s);

export class RsTemplateForm extends LitElement {
  static properties = {
    mode: { type: String },
    template: { attribute: false },
    fixed: { attribute: false },
    error: { type: String },
    name: { state: true },
    post: { state: true },
    fields: { state: true },
    values: { state: true },
  };

  static styles = css`
    :host { display: block; font-size: 14px; color: var(--text, #17212b); }
    * { box-sizing: border-box; }
    h4 { margin: 12px 0 6px; font-size: 13px; }
    label { display: flex; flex-direction: column; gap: 3px; margin: 0 0 8px; font-size: 13px; }
    label.small { margin: 0; font-size: 12px; color: var(--muted, #707579); }
    input, select, textarea { font: inherit; font-size: 14px; color: var(--text, #17212b); background: var(--panel, #fff); border: 1px solid var(--border, #dfe3e8);
      border-radius: 6px; padding: 5px 8px; min-width: 0; width: 100%; }
    textarea { min-height: 64px; resize: vertical; }
    input:focus, select:focus, textarea:focus { outline: 2px solid color-mix(in srgb, var(--accent, #3390ec) 45%, transparent); border-color: var(--accent, #3390ec); }
    button { font: inherit; font-size: 12.5px; padding: 3px 8px; border-radius: 8px; border: 1px solid var(--border, #dfe3e8);
      background: transparent; color: var(--accent, #3390ec); cursor: pointer; white-space: nowrap; }
    button:hover { border-color: var(--accent, #3390ec); }
    button:focus-visible { outline: 2px solid var(--accent, #3390ec); outline-offset: 1px; }
    button.x { color: var(--err, #d93025); }
    code { background: var(--soft, #eef3f8); padding: 1px 5px; border-radius: 4px; font-size: 12px; white-space: nowrap; }
    .field { border: 1px solid var(--border, #dfe3e8); border-radius: 8px; padding: 8px; margin-bottom: 6px; display: flex; flex-direction: column; gap: 6px; }
    .row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
    .row .grow { flex: 1 1 160px; width: auto; }
    .row select { width: auto; flex: 0 1 auto; }
    .check { flex-direction: row; align-items: center; gap: 5px; margin: 0; white-space: nowrap; }
    .check input { width: auto; accent-color: var(--accent, #3390ec); }
    ul { list-style: none; margin: 0; padding: 0; max-height: 220px; overflow: auto; border: 1px solid var(--border, #dfe3e8); border-radius: 8px; }
    li { display: flex; gap: 8px; align-items: center; padding: 5px 8px; border-top: 1px solid var(--border, #dfe3e8); min-width: 0; }
    li:first-child { border-top: 0; }
    .where { flex: none; font-size: 11.5px; color: var(--muted, #707579); }
    .why { flex: 0 1 auto; max-width: 45%; font-size: 11.5px; color: var(--muted, #707579); text-align: right; cursor: help; }
    .value { flex: 1; min-width: 0; overflow-wrap: anywhere; }
    p { margin: 6px 0; line-height: 1.4; }
    .muted { color: var(--muted, #707579); font-size: 12.5px; }
    .warn { color: var(--warn, #b26a00); font-size: 12.5px; }
    .req { color: var(--err, #d93025); }
    .err { color: var(--err, #d93025); white-space: pre-line; margin: 0; }
    .err:empty { display: none; }`;

  constructor() {
    super();
    this.mode = 'edit';
    /** @type {Json | null} */
    this.template = null;
    /** @type {Set<string>} */
    this.fixed = new Set();
    this.error = '';
    this.name = '';
    /** @type {Json} */
    this.post = {};
    /** @type {TemplateField[]} */
    this.fields = [];
    /** @type {Record<string, string>} */
    this.values = {};
  }

  willUpdate(changed) {
    if (!changed.has('template') || !this.template) return;
    const t = this.template;
    this.name = t.title || '';
    this.post = clone(t.payload || {});
    const known = /** @type {TemplateField[]} */ (clone(t.fields || []));
    // a {{key}} typed into the post by hand is a field as well
    const typed = usedKeys(this.post).filter((k) => !known.some((f) => f.key === k))
      .map((key) => /** @type {TemplateField} */ ({ key, label: key, type: 'text', required: true, default: '' }));
    this.fields = [...known, ...typed];
    this.values = Object.fromEntries(known.map((f) => [f.key, f.default]));
  }

  /** What the page sends: {title, post, fields} in "edit", {title, values} in "fill". */
  get value() {
    return this.mode === 'fill' ? { title: this.name.trim(), values: { ...this.values } } : { title: this.name.trim(), post: this.post, fields: this.fields };
  }

  /** @param {Spot} spot */
  makeField(spot) {
    const base = spot.url ? 'link' : 'text';
    let key = base;
    for (let i = 2; this.fields.some((f) => f.key === key) || usedKeys(this.post).includes(key); i++) key = base + '_' + i;
    let label = spot.where;
    for (let i = 2; this.fields.some((f) => f.label === label); i++) label = spot.where + ' ' + i;
    const post = clone(this.post);
    let parent = post;
    for (const p of spot.path.slice(0, -1)) parent = parent[p];
    const last = spot.path[spot.path.length - 1];
    const old = parent[last];
    parent[last] = spot.start === undefined ? '{{' + key + '}}' : old.slice(0, spot.start) + '{{' + key + '}}' + old.slice(spot.end);
    this.post = post;
    this.fields = [...this.fields, { key, label, type: spot.url ? 'url' : spot.value.includes('\n') ? 'multiline' : 'text', required: true, default: spot.value }];
  }

  /** The post gets the field's default back in place of {{key}}. @param {number} i */
  removeField(i) {
    const f = this.fields[i];
    const mark = '{{' + f.key + '}}';
    this.post = mapStrings(this.post, (s, path) => s.split(mark).join(sourceOf(path) === 'html' ? escapeHtml(f.default) : f.default));
    this.fields = this.fields.filter((_, j) => j !== i);
  }

  /** @param {number} i @param {Partial<TemplateField>} change */
  setField(i, change) { this.fields = this.fields.map((f, j) => (j === i ? { ...f, ...change } : f)); }

  renderEdit() {
    const free = spots(this.post, this.fixed);
    const markdown = typeof (this.post.rich_message || {}).markdown === 'string';
    return html`
<label>Название шаблона<input name="title" .value=${this.name} placeholder="Дайджест недели" @input=${(e) => { this.name = e.target.value; }}></label>
<h4>Поля</h4>
${this.fields.length ? this.fields.map((f, i) => html`<div class="field">
  <div class="row"><code title="Так поле стоит в посте">{{${f.key}}}</code>
    <input class="grow" name=${'label-' + f.key} aria-label="Подпись поля в форме" placeholder="Подпись" .value=${f.label} @input=${(e) => this.setField(i, { label: e.target.value })}>
    <button class="x" title="Убрать поле: в посте снова будет значение по умолчанию" aria-label=${'Убрать поле ' + f.label} @click=${() => this.removeField(i)}>✕</button></div>
  <div class="row">
    <select aria-label="Тип поля" @change=${(e) => this.setField(i, { type: e.target.value })}>${Object.entries(TYPES).map(([v, t]) => html`<option value=${v} ?selected=${f.type === v}>${t}</option>`)}</select>
    <label class="check"><input type="checkbox" .checked=${f.required} @change=${(e) => this.setField(i, { required: e.target.checked })}>обязательное</label></div>
  <label class="small">По умолчанию${f.type === 'multiline'
    ? html`<textarea name=${'default-' + f.key} .value=${f.default} @input=${(e) => this.setField(i, { default: e.target.value })}></textarea>`
    : html`<input name=${'default-' + f.key} .value=${f.default} @input=${(e) => this.setField(i, { default: e.target.value })}>`}</label>
</div>`) : html`<p class="muted">Полей пока нет: по шаблону получится копия поста. Выберите ниже строки, которые меняются от выпуска к выпуску.</p>`}
<h4>Строки поста</h4>
${markdown ? html`<p class="warn">Пост в Markdown: в его тексте поля пока не подставляются. Переведите пост в визуальный режим или в HTML.</p>` : nothing}
${free.length ? html`<ul class="spots">${free.map((s) => html`<li><span class="where">${s.where}</span><span class="value">${short(s.value)}</span>
  ${s.why ? html`<span class="why" title=${ANSWERED_WHY}>${s.why}</span>`
    : html`<button title="Сделать полем: в посте встанет {{…}}, а эта строка — значением по умолчанию" @click=${() => this.makeField(s)}>→ поле</button>`}</li>`)}</ul>`
    : html`<p class="muted">Строк, которые можно сделать полем, не осталось.</p>`}
<p class="muted">В HTML можно вписать {{имя}} прямо в текст поста — поле появится здесь. Значение при заполнении всегда текст: разметки оно не создаёт.</p>
<p class="err" role="alert">${this.error}</p>`;
  }

  renderFill() {
    const fields = /** @type {TemplateField[]} */ ((this.template && this.template.fields) || []);
    const set = (key, v) => { this.values = { ...this.values, [key]: v }; };
    return html`
<label>Название черновика<input name="title" .value=${this.name} @input=${(e) => { this.name = e.target.value; }}></label>
${fields.map((f) => html`<label><span>${f.label}${f.required ? html`<span class="req" title="Обязательное поле"> *</span>` : nothing}</span>
  ${f.type === 'multiline'
    ? html`<textarea name=${f.key} .value=${this.values[f.key] ?? ''} @input=${(e) => set(f.key, e.target.value)}></textarea>`
    : html`<input name=${f.key} inputmode=${f.type === 'url' ? 'url' : 'text'} placeholder=${f.type === 'url' ? 'https://… или upload:имя файла' : ''} .value=${this.values[f.key] ?? ''} @input=${(e) => set(f.key, e.target.value)}>`}</label>`)}
${fields.length ? nothing : html`<p class="muted">В шаблоне нет полей: черновик будет его копией.</p>`}
<p class="muted">Значения встанут в пост как текст. Получится новый черновик без каналов: канал выберите перед публикацией.</p>
<p class="err" role="alert">${this.error}</p>`;
  }

  render() { return this.mode === 'fill' ? this.renderFill() : this.renderEdit(); }
}

customElements.define('rs-template-form', RsTemplateForm);
