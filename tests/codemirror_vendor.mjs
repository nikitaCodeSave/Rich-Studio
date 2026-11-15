// The vendored CodeMirror (app/static/vendor/codemirror.js, made by scripts/vendor_codemirror.mjs) loads in Node without
// a DOM the way a browser loads it: every import is a file of the set. A bare import is refused here instead of being
// quietly found in node_modules, so V8's own parser checks what the script's patterns rewrote. Also the base for the
// completion tests of the source editor: they build an EditorState and a CompletionContext the same way.
// Run: npm run test:codemirror (Node 22.15 or newer, for module.registerHooks)
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';

const vendor = new URL('../app/static/vendor/', import.meta.url).href;
const loaded = new Set();
assert.ok(registerHooks, 'module.registerHooks is missing: Node 22.15 or newer is needed');
registerHooks({
  resolve(specifier, context, next) {
    const fromVendor = context.parentURL?.startsWith(vendor);
    if (fromVendor && !specifier.startsWith('./')) throw new Error(`${context.parentURL} imports '${specifier}': a browser cannot load it (make vendor)`);
    const found = next(specifier, context);
    if (fromVendor && !found.url.startsWith(vendor)) throw new Error(`${context.parentURL} imports ${found.url} outside app/static/vendor/`);
    if (found.url.startsWith(vendor)) loaded.add(found.url.slice(vendor.length));
    return found;
  },
});
const cm = await import('../app/static/vendor/codemirror.js');

// one set, all of it reachable from codemirror.js, and the manifest names the same files
const sets = readdirSync(fileURLToPath(vendor)).filter((f) => /^codemirror-[0-9a-f]+$/.test(f));
assert.equal(sets.length, 1, `one set of CodeMirror in app/static/vendor/, found ${sets.join(', ')}`);
const [set] = sets;
const files = readdirSync(fileURLToPath(vendor + set)).filter((f) => f.endsWith('.js')).map((f) => `${set}/${f}`).sort();
assert.deepEqual([...loaded].filter((f) => f !== 'codemirror.js').sort(), files, 'codemirror.js loads every file of the set');
const manifest = JSON.parse(readFileSync(fileURLToPath(`${vendor}${set}/manifest.json`), 'utf8'));
assert.deepEqual(Object.values(manifest.packages).map((p) => `${set}/${p.file}`).sort(), files, 'manifest.json lists the files of the set');
console.log(`PASS  ${files.length} files of ${set} load in Node, no bare imports`);

// extensions from every package in one state: a second copy of @codemirror/state would throw "Unrecognized extension value"
const { EditorState } = cm.state;
const { EditorView, keymap, lineNumbers } = cm.view;
const { syntaxTree, ensureSyntaxTree, bracketMatching, syntaxHighlighting, defaultHighlightStyle } = cm.language;
const { autocompletion, closeBrackets, CompletionContext } = cm.autocomplete;
const editing = [
  cm.commands.history(), keymap.of([...cm.commands.defaultKeymap, ...cm.commands.historyKeymap, ...cm.search.searchKeymap]),
  lineNumbers(), bracketMatching(), closeBrackets(), autocompletion(), cm.search.search({ top: true }),
  syntaxHighlighting(defaultHighlightStyle), EditorView.lineWrapping,
];

function parse(language, doc) {
  const state = EditorState.create({ doc, extensions: [language, ...editing] });
  assert.ok(ensureSyntaxTree(state, doc.length, 5000), 'parsed in time');
  const names = new Set();
  syntaxTree(state).iterate({ enter: (node) => { names.add(node.name); } });
  return { state, names };
}
const expect = (lang, names, want) => {
  for (const name of want) assert.ok(names.has(name), `${lang}: no ${name} in [${[...names].join(', ')}]`);
  console.log(`PASS  ${lang} parses: ${want.join(', ')}`);
};
expect('html', parse(cm.langHtml.html(), '<b>bold</b> <a href="https://t.me/s">link</a>').names, ['Element', 'TagName', 'AttributeName', 'AttributeValue']);
expect('markdown', parse(cm.langMarkdown.markdown(), '**bold** _it_ [link](https://t.me/s)\n\n> quote').names, ['StrongEmphasis', 'Emphasis', 'Link', 'Blockquote']);
expect('json', parse(cm.langJson.json(), '{"rich_message": {"html": "<b>x</b>"}, "pin": true}').names, ['Object', 'Property', 'PropertyName', 'String', 'True']);

// a completion source registered for JSON gets a real CompletionContext; from goes after the opening quote,
// or CodeMirror filters the options by '"ht' instead of 'ht'
const doc = '{"rich_message": {"ht": 1}}';
const pos = doc.indexOf('"ht"') + 3;
const source = (ctx) => {
  const node = syntaxTree(ctx.state).resolveInner(ctx.pos, -1);
  return node.name === 'PropertyName' ? { from: node.from + 1, options: [{ label: 'html' }, { label: 'markdown' }] } : null;
};
const { state } = parse([cm.langJson.json(), cm.langJson.jsonLanguage.data.of({ autocomplete: source })], doc);
const sources = state.languageDataAt('autocomplete', pos);
assert.equal(sources.length, 1, 'the JSON completion source is found at the cursor');
const result = await sources[0](new CompletionContext(state, pos, true));
assert.equal(state.sliceDoc(result.from, pos), 'ht');
assert.deepEqual(result.options.map((o) => o.label), ['html', 'markdown']);
console.log('PASS  json completion source gets a CompletionContext at the property name');
