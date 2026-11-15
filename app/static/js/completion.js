// Suggestions for the source tabs. Data only: Bot API fields from /api/spec (types with const_field, and `post`, the
// post's own fields and the method parameters behind options and poll_after), HTML tags from snippets.js. No DOM, no
// post state and no import of CodeMirror: code-editor.js passes its syntax tree in, and tests/completion_unit.mjs
// runs the same functions in Node on the vendored parsers.

/** @typedef {import('@lezer/common').Tree} Tree */
/** @typedef {import('@lezer/common').SyntaxNode} SyntaxNode */
/** @typedef {import('@codemirror/view').EditorView} EditorView */
/** @typedef {import('@codemirror/autocomplete').Completion} Completion */
/** @typedef {{ from: number, to: number, options: Completion[], validFor?: RegExp }} Result */
/** @typedef {{ description?: string, fields?: SpecField[], subtypes?: string[], const?: string | null, const_field?: string | null }} Shape */

const ARRAY = 'Array of ';
const WORD = /[\w$.+-]/; // a bare word: true, a number, a key typed without its quotes
const VALID = /^[\w$-]*$/; // CodeMirror keeps filtering the same list while the typed text matches

// ---------- the spec ----------
/** @param {SpecSummary} spec @param {string} name @returns {Shape | undefined} */
const shape = (spec, name) => spec.post?.types[name] ?? spec.types[name];

/** Object types the names stand for, unions opened; arrays and scalars are not objects. */
export function members(spec, names, seen = new Set()) {
  /** @type {string[]} */
  const out = [];
  for (const name of names) {
    if (seen.has(name)) continue;
    seen.add(name);
    const t = shape(spec, name);
    if (t?.subtypes?.length) out.push(...members(spec, t.subtypes, seen));
    else if (t?.fields) out.push(name);
  }
  return out;
}

/** What the elements of an array of these types are ("Array of X" is X; a union may hold an array, as RichText does). */
function elements(spec, names, seen = new Set()) {
  /** @type {string[]} */
  const out = [];
  for (const name of names) {
    if (name.startsWith(ARRAY)) out.push(name.slice(ARRAY.length));
    else if (!seen.has(name)) {
      seen.add(name);
      out.push(...elements(spec, shape(spec, name)?.subtypes || [], seen));
    }
  }
  return out;
}

/**
 * Fields an object of these types can have. The members of a union that the object's strings select by their
 * const_field and const ("type": "list" is InputRichBlockList); none selected yet: the fields all members share.
 * @param {SpecSummary} spec @param {string[]} names @param {Record<string, unknown>} values @returns {SpecField[]}
 */
export function fieldsOf(spec, names, values) {
  const all = members(spec, names);
  const chosen = all.filter((n) => { const t = shape(spec, n); return !!t?.const_field && values[t.const_field] === t.const; });
  const lists = (chosen.length ? chosen : all).map((n) => shape(spec, n)?.fields || []);
  if (lists.length < 2) return lists[0] || [];
  return lists[0].filter((f) => lists.every((l) => l.some((g) => g.name === f.name)));
}

/**
 * Values for a key: for the field that tells the members of a union apart, the const of each member; else the
 * field's enum or a boolean. "type" of InputRichBlockListItem is an enum of list markers: it tells nothing apart.
 * @param {SpecSummary} spec @param {string[]} names @param {Record<string, unknown>} values @param {string} key
 * @returns {{ value: string | boolean, detail: string, info: string }[]}
 */
export function valuesOf(spec, names, values, key) {
  const tagged = members(spec, names).filter((n) => shape(spec, n)?.const_field === key);
  if (tagged.length) {
    const seen = new Set();
    return tagged.map((n) => ({ value: /** @type {string} */ (shape(spec, n)?.const), detail: n, info: shape(spec, n)?.description || '' }))
      .filter((o) => !seen.has(o.value) && seen.add(o.value));
  }
  const field = fieldsOf(spec, names, values).find((f) => f.name === key);
  if (!field) return [];
  if (field.enum?.length) return field.enum.map((v) => ({ value: v, detail: field.name, info: field.description }));
  const flags = field.types.includes('Boolean') ? [true, false] : field.types.includes('True') ? [true] : [];
  return flags.map((v) => ({ value: v, detail: field.types.join(' | '), info: field.description }));
}

/** What goes after "key": so that the cursor lands where the value is typed. @returns {[string, number]} */
function template(spec, types, seen = new Set()) {
  const first = types[0] || '';
  if (first.startsWith(ARRAY)) return ['[]', 1];
  if (first === 'String') return ['""', 1];
  const t = seen.has(first) ? undefined : shape(spec, first);
  seen.add(first);
  if (t?.subtypes?.length) return template(spec, t.subtypes, seen);
  if (t?.fields) return ['{}', 1];
  return ['', 0];
}

// ---------- where the cursor is in JSON ----------
/**
 * The token at the cursor: a string (its quotes included; an unclosed one ends at the cursor, the rest of the line
 * is not its text) or a bare word. Strings never span lines in JSON, so the line before the cursor tells which.
 * @param {string} doc @param {number} pos
 */
export function tokenAt(doc, pos) {
  const lineStart = doc.lastIndexOf('\n', pos - 1) + 1;
  let open = -1;
  for (let i = lineStart; i < pos; i++) {
    if (open >= 0 && doc[i] === '\\') i++;
    else if (doc[i] === '"') open = open >= 0 ? -1 : i;
  }
  if (open >= 0) {
    let close = -1;
    for (let i = pos; i < doc.length && doc[i] !== '\n'; i++) {
      if (doc[i] === '\\') i++;
      else if (doc[i] === '"') { close = i; break; }
    }
    return { from: open, to: close >= 0 ? close + 1 : pos, text: doc.slice(open + 1, pos), quoted: true };
  }
  let from = pos;
  while (from > 0 && WORD.test(doc[from - 1])) from--;
  let to = pos;
  while (to < doc.length && WORD.test(doc[to])) to++;
  return { from, to, text: doc.slice(from, pos), quoted: false };
}

/** @param {string} doc @param {SyntaxNode | null} property */
function keyOf(doc, property) {
  const name = property?.getChild('PropertyName');
  try { return name ? String(JSON.parse(doc.slice(name.from, name.to))) : null; } catch { return null; }
}

/** The string values of an object's properties, what its discriminator is read from; `skip`: the one being typed. */
function stringsOf(doc, object, skip) {
  /** @type {Record<string, unknown>} */
  const out = {};
  for (const p of object.getChildren('Property')) {
    if (skip >= p.from && skip <= p.to) continue;
    const key = keyOf(doc, p);
    const v = p.getChild('String');
    if (key !== null && v) try { out[key] = JSON.parse(doc.slice(v.from, v.to)); } catch { /* half-typed */ }
  }
  return out;
}

/**
 * Where the cursor is: the key being typed in an object, the value of a key, or neither; the types of the object
 * it is in, walked down from the post (spec.post.root). Works on unfinished JSON: lezer's tree gives the objects
 * and arrays up to the cursor.
 * @param {SpecSummary} spec @param {Tree} tree @param {string} doc @param {number} pos
 */
export function jsonContext(spec, tree, doc, pos) {
  const token = tokenAt(doc, pos);
  let i = token.from - 1;
  while (i >= 0 && /\s/.test(doc[i])) i--;
  /** @type {SyntaxNode | null} */
  let node = tree.resolveInner(token.from, -1);
  while (node && node.name !== 'Object' && node.name !== 'Array') node = node.parent;
  if (!node) return null;
  /** @type {SyntaxNode[]} */
  const path = [];
  for (let n = /** @type {SyntaxNode | null} */ (node); n; n = n.parent) if (n.name === 'Object' || n.name === 'Array') path.unshift(n);
  let names = [spec.post.root];
  for (let k = 1; k < path.length; k++) {
    const parent = path[k - 1];
    if (parent.name === 'Array') { names = elements(spec, names); continue; }
    const key = keyOf(doc, path[k].parent?.name === 'Property' ? path[k].parent : null);
    names = (key !== null && fieldsOf(spec, names, stringsOf(doc, parent, -1)).find((f) => f.name === key)?.types) || [];
  }
  const object = path[path.length - 1];
  if (object.name !== 'Object') return null;
  if (doc[i] === ':') {
    // the value of the key before the colon: the property that holds the token
    let p = tree.resolveInner(i, 1);
    while (p && p.name !== 'Property') p = /** @type {SyntaxNode} */ (p.parent);
    const key = keyOf(doc, p);
    return key === null ? null : { at: /** @type {const} */ ('value'), key, names, values: stringsOf(doc, object, token.from), token, object };
  }
  return { at: /** @type {const} */ ('key'), key: null, names, values: stringsOf(doc, object, token.from), token, object };
}

// ---------- JSON completion ----------
/** Replaces the token at the cursor when a completion is picked (the cursor may have moved since the list opened). */
const replaceToken = (/** @type {(doc: string, t: ReturnType<typeof tokenAt>) => [string, number]} */ make) => (/** @type {EditorView} */ view) => {
  const doc = view.state.doc.toString();
  const t = tokenAt(doc, view.state.selection.main.head);
  const [insert, cursor] = make(doc, t);
  view.dispatch({ changes: { from: t.from, to: t.to, insert }, selection: { anchor: t.from + cursor }, userEvent: 'input.complete', scrollIntoView: true });
};

/**
 * Completions at pos for the post's JSON, or null. Unless asked (Ctrl+Space), only inside quotes or for a bare
 * value being typed: a new line or a comma opens nothing.
 * @param {SpecSummary} spec @param {Tree} tree @param {string} doc @param {number} pos @param {boolean} explicit
 * @returns {Result | null}
 */
export function completeJson(spec, tree, doc, pos, explicit) {
  if (!spec?.post) return null;
  const at = jsonContext(spec, tree, doc, pos);
  if (!at || (!explicit && !at.token.quoted && !(at.at === 'value' && at.token.text))) return null;
  const from = at.token.quoted ? at.token.from + 1 : at.token.from;
  /** @type {Completion[]} */
  let options;
  if (at.at === 'key') {
    const taken = new Set(at.object.getChildren('Property').filter((p) => !(at.token.from >= p.from && at.token.from <= p.to)).map((p) => keyOf(doc, p)));
    options = fieldsOf(spec, at.names, at.values).filter((f) => !taken.has(f.name)).map((f) => {
      const [value, back] = template(spec, f.types);
      return {
        label: f.name, detail: f.types.join(' | '), type: 'property', boost: f.required ? 1 : 0,
        info: (f.required ? 'Обязательное поле. ' : '') + f.description,
        apply: replaceToken((doc, t) => {
          const colon = /^[ \t]*:/.test(doc.slice(t.to));
          const key = JSON.stringify(f.name);
          return colon ? [key, key.length] : [`${key}: ${value}`, key.length + 2 + back];
        }),
      };
    });
  } else {
    options = valuesOf(spec, at.names, at.values, /** @type {string} */ (at.key)).map((o) => ({
      label: String(o.value), detail: o.detail, info: o.info, type: typeof o.value === 'boolean' ? 'keyword' : 'enum',
      apply: replaceToken(() => { const text = JSON.stringify(o.value); return [text, text.length]; }),
    }));
  }
  return options.length ? { from, to: pos, options, validFor: VALID } : null;
}

// ---------- HTML (and HTML inside Markdown) ----------
/** @typedef {Record<string, { title: string, attrs: Record<string, { values: string[], bare: boolean }> }>} HtmlCatalog */

/**
 * Tags, their attributes and the word-like values the snippets give them (snippets.js: the grammar of Rich HTML is
 * not in the machine-readable spec, so the snippets are the list of what Telegram takes).
 * @param {{ items: { snip: string, label: string, title?: string }[] }[]} groups @returns {HtmlCatalog}
 */
export function htmlCatalog(groups) {
  /** @type {HtmlCatalog} */
  const tags = {};
  for (const g of groups || []) for (const it of g.items || []) {
    for (const m of it.snip.matchAll(/<([a-z][\w-]*)([^<>]*)>/gi)) {
      const tag = (tags[m[1].toLowerCase()] ||= { title: it.title || it.label, attrs: {} });
      for (const a of m[2].matchAll(/([a-z][\w-]*)(?:\s*=\s*"([^"]*)")?/gi)) {
        const attr = (tag.attrs[a[1]] ||= { values: [], bare: a[2] === undefined });
        if (a[2] !== undefined) attr.bare = false;
        if (a[2] && /^[a-z_][\w-]*$/i.test(a[2]) && !attr.values.includes(a[2])) attr.values.push(a[2]);
      }
    }
  }
  return tags;
}

/**
 * Completions at pos for HTML: a tag name after "<", an attribute of the open tag after a space, a value inside its
 * quotes. Read from the line before the cursor, so the same works inside Markdown. Closing tags: lang-html's
 * autoCloseTags writes them.
 * @param {HtmlCatalog} catalog @param {string} doc @param {number} pos @returns {Result | null}
 */
export function completeHtml(catalog, doc, pos) {
  const before = doc.slice(Math.max(doc.lastIndexOf('\n', pos - 1) + 1, pos - 500), pos);
  const attrs = String.raw`(?:\s+[\w-]+(?:\s*=\s*"[^"]*")?)*`;
  let m = before.match(/<([\w-]*)$/);
  if (m) {
    const options = Object.entries(catalog).map(([tag, t]) => ({ label: tag, detail: t.title, type: 'type' }));
    return { from: pos - m[1].length, to: pos, options, validFor: /^[\w-]*$/ };
  }
  m = before.match(new RegExp(String.raw`<([\w-]+)${attrs}\s+([\w-]+)\s*=\s*"([^"]*)$`));
  if (m) {
    const values = catalog[m[1].toLowerCase()]?.attrs[m[2]]?.values || [];
    return values.length ? { from: pos - m[3].length, to: pos, options: values.map((v) => ({ label: v, type: 'enum' })), validFor: /^[\w-]*$/ } : null;
  }
  m = before.match(new RegExp(String.raw`<([\w-]+)(${attrs})\s+([\w-]*)$`));
  const tag = m && catalog[m[1].toLowerCase()];
  if (!m || !tag) return null;
  const used = new Set([...m[2].matchAll(/([\w-]+)(?:\s*=|\s|$)/g)].map((a) => a[1]));
  const options = Object.entries(tag.attrs).filter(([name]) => !used.has(name)).map(([name, a]) => ({
    label: name, detail: a.values.join(' | '), type: 'property',
    apply: a.bare ? name : (/** @type {EditorView} */ view, /** @type {Completion} */ _c, /** @type {number} */ from, /** @type {number} */ to) => {
      view.dispatch({ changes: { from, to, insert: `${name}=""` }, selection: { anchor: from + name.length + 2 }, userEvent: 'input.complete' });
    },
  }));
  return options.length ? { from: pos - m[3].length, to: pos, options, validFor: /^[\w-]*$/ } : null;
}

// ---------- CodeMirror sources ----------
/** @typedef {import('@codemirror/autocomplete').CompletionContext} CompletionContext */
/** @typedef {(state: import('@codemirror/state').EditorState) => Tree} SyntaxTreeOf */

/** The JSON tab's source; getSpec gives the current /api/spec (it changes when the spec is refreshed). */
export const jsonSource = (/** @type {SyntaxTreeOf} */ syntaxTree, /** @type {() => SpecSummary} */ getSpec) =>
  (/** @type {CompletionContext} */ ctx) => completeJson(getSpec(), syntaxTree(ctx.state), ctx.state.doc.toString(), ctx.pos, ctx.explicit);

/** The HTML and Markdown tabs' source. */
export const htmlSource = (/** @type {() => HtmlCatalog} */ getCatalog) =>
  (/** @type {CompletionContext} */ ctx) => completeHtml(getCatalog(), ctx.state.doc.toString(), ctx.pos);
