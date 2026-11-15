// Suggestions of the source tabs (app/static/js/completion.js) on the real spec and the vendored CodeMirror, in Node.
// The spec is what GET /api/spec gives (app/api/specs.py, run with Python, the bundled api.json), so the post's own
// fields and the method parameters come from the server as in the browser. Each case goes through a real
// CompletionContext of an EditorState, then checks what CodeMirror filters by (the text from `from` to the cursor:
// after the opening quote, or CodeMirror compares '"ty' with 'type') and what picking an option writes.
// Run: npm run test:completion (PYTHON=… with requirements.txt, else .venv or python3)
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cm from '../app/static/vendor/codemirror.js';
import { htmlCatalog, htmlSource, jsonSource } from '../app/static/js/completion.js';

const repo = fileURLToPath(new URL('..', import.meta.url));
const py = process.env.PYTHON || (existsSync(join(repo, '.venv/bin/python')) ? join(repo, '.venv/bin/python') : 'python3');
const dump = 'import sys; sys.path.insert(0, "."); import json, spec; spec.current.load_initial(); from api.specs import get_spec; print(json.dumps(get_spec()))';
const spec = JSON.parse(execFileSync(py, ['-I', '-c', dump], {
  cwd: join(repo, 'app'), env: { ...process.env, DATA_DIR: mkdtempSync(join(tmpdir(), 'rs-completion-')), SPEC_PIN: 'on' }, maxBuffer: 1 << 26,
}).toString());
// snippets.js is a classic script that sets window.SNIPPETS
globalThis.window = /** @type {any} */ ({});
await import('../app/static/snippets.js');
const catalog = htmlCatalog(/** @type {any} */ (globalThis.window).SNIPPETS.html);

const { EditorState } = cm.state;
const { CompletionContext } = cm.autocomplete;
const { ensureSyntaxTree, syntaxTree } = cm.language;
const LANG = { json: cm.langJson.json(), html: cm.langHtml.html(), markdown: cm.langMarkdown.markdown({ base: cm.langMarkdown.markdownLanguage }) };
const SOURCE = { json: jsonSource(syntaxTree, () => spec), html: htmlSource(() => catalog), markdown: htmlSource(() => catalog) };

const results = [];
const check = (name, ok, info = '') => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !info ? '' : '  ' + info}`); };

/** The doc with | at the cursor: what the source offers there (`source`: another one than the tab's). */
async function at(lang, marked, explicit = false, source = SOURCE[lang]) {
  const pos = marked.indexOf('|');
  const doc = marked.replace('|', '');
  const base = EditorState.create({ doc, selection: { anchor: pos }, extensions: [LANG[lang]] });
  ensureSyntaxTree(base, doc.length, 5000);
  const result = await source(new CompletionContext(base, pos, explicit));
  const typed = result ? base.sliceDoc(result.from, pos) : null;
  const labels = result ? result.options.map((o) => o.label) : [];
  // what picking an option writes, through a stand-in view that applies the transaction to the state
  const pick = (label) => {
    const o = result?.options.find((x) => x.label === label);
    if (!o) return null;
    let state = base;
    const view = { get state() { return state; }, dispatch(spec) { state = state.update(spec).state; } };
    if (typeof o.apply === 'function') o.apply(/** @type {any} */ (view), o, result.from, pos);
    else view.dispatch({ changes: { from: result.from, to: pos, insert: o.apply ?? o.label } });
    const head = state.selection.main.head;
    return state.doc.toString().slice(0, head) + '|' + state.doc.toString().slice(head);
  };
  return { result, typed, labels, pick, option: (label) => result?.options.find((x) => x.label === label) };
}
const blocks = spec.types.InputRichBlock.subtypes.map((n) => spec.types[n].const);

// the post's own fields come from models.Post on the server
let r = await at('json', '{"ri|');
check('a key of the post: the fields of models.Post, the typed text after the quote', r.typed === 'ri' && r.labels.includes('rich_message')
  && r.labels.includes('poll_after') && r.option('rich_message')?.boost === 1 && r.result?.validFor?.test('ric'), JSON.stringify([r.typed, r.labels]));
check('…picked: the key, a colon and the value\'s brackets, the cursor inside', r.pick('rich_message') === '{"rich_message": {|}', r.pick('rich_message'));
r = await at('json', '{"rich_message": {}, "|"}');
check('keys already in the object are not offered again', !r.labels.includes('rich_message') && r.labels.includes('options') && r.typed === '', JSON.stringify(r.labels));
r = await at('json', '{"rich_message": {"ht|"}}');
check('rich_message: the fields of InputRichMessage; a String gets its quotes', r.labels.includes('html') && r.labels.includes('blocks')
  && r.pick('html') === '{"rich_message": {"html": "|"}}', r.pick('html') ?? JSON.stringify(r.labels));
r = await at('json', '{"rich_message": {"bl|": []}}');
check('a key that has its colon: only the key is replaced', r.pick('blocks') === '{"rich_message": {"blocks"|: []}}', r.pick('blocks'));

// the discriminator: union members and their const
r = await at('json', '{"rich_message": {"blocks": [{"type": "ph|"}]}}');
check('"type" of a block: the const of every member of InputRichBlock', r.typed === 'ph' && blocks.every((c) => r.labels.includes(c))
  && r.labels.length === blocks.length && r.option('photo')?.detail === 'InputRichBlockPhoto', JSON.stringify(r.labels));
check('…picked: the value in its quotes', r.pick('photo') === '{"rich_message": {"blocks": [{"type": "photo"|}]}}', r.pick('photo'));
r = await at('json', '{"rich_message": {"blocks": [{"type": "ph|');
check('an unclosed string: the range ends at the cursor, the value gets its closing quote', r.typed === 'ph'
  && r.pick('photo') === '{"rich_message": {"blocks": [{"type": "photo"|', r.pick('photo'));
r = await at('json', '{"rich_message": {"blocks": [{"type": "heading", "|"}]}}');
const heading = spec.types[spec.types.InputRichBlock.subtypes.find((n) => spec.types[n].const === 'heading')].fields.map((f) => f.name);
check('a block with its type: that block\'s fields, the type not again', heading.filter((f) => f !== 'type').every((f) => r.labels.includes(f))
  && !r.labels.includes('type') && !r.labels.includes('items'), JSON.stringify(r.labels));
r = await at('json', '{"rich_message": {"blocks": [{"|"}]}}');
check('a block without a type yet: what every block has, the type first of all', r.labels.includes('type') && !r.labels.includes('items'), JSON.stringify(r.labels));
r = await at('json', '{"rich_message": {"blocks": [{"type": "list", "items": [{"type": "|"}]}]}}');
check('"type" of a list item is an enum of markers, not the blocks', r.labels.length > 0 && !r.labels.includes('paragraph')
  && r.labels.every((l) => spec.types.InputRichBlockListItem.fields.find((f) => f.name === 'type').enum.includes(l)), JSON.stringify(r.labels));
r = await at('json', '{"rich_message": {"blocks": [{"type": "paragraph", "text": ["a ", {"type": "bo|"}]}]}}');
check('rich text inside a block: the members of RichText (an array of it is a RichText too)', r.labels.includes('bold') && r.labels.includes('url'), JSON.stringify(r.labels));
r = await at('json', '{"rich_message": {"blocks": [{"type": "paragraph", "text": [{"type": "bold", "|"}]}]}}');
check('…and the fields of the member it chose', r.labels.includes('text') && !r.labels.includes('type'), JSON.stringify(r.labels));

// options and poll_after: the method's parameters, without what the studio sets
r = await at('json', '{"options": {"|"}}');
check('options: sendRichMessage without chat_id, rich_message, reply_markup', r.labels.includes('disable_notification') && r.labels.includes('message_effect_id')
  && !['chat_id', 'rich_message', 'reply_markup'].some((l) => r.labels.includes(l)), JSON.stringify(r.labels));
r = await at('json', '{"options": {"disable_notification": tr|}}');
check('a Boolean typed bare: true, and picked it replaces the word', r.labels.includes('true') && r.typed === 'tr'
  && r.pick('true') === '{"options": {"disable_notification": true|}}', r.pick('true') ?? JSON.stringify(r.labels));
r = await at('json', '{"options": {"protect_content": |}}');
check('nothing typed after a colon: nothing opens by itself', r.result === null);
r = await at('json', '{"options": {"protect_content": |}}', true);
check('…and Ctrl+Space shows true and false', r.labels.join() === 'true,false', JSON.stringify(r.labels));
r = await at('json', '{"options": {\n  |\n}}');
check('a new line in an object opens nothing by itself', r.result === null);
r = await at('json', '{"options": {\n  |\n}}', true);
check('…Ctrl+Space shows its keys', r.labels.includes('disable_notification') && r.typed === '', JSON.stringify(r.labels));
r = await at('json', '{"poll_after": {"question": "?", "|"}}');
check('poll_after: sendPoll without chat_id', r.labels.includes('options') && r.labels.includes('is_anonymous') && !r.labels.includes('chat_id') && !r.labels.includes('question'), JSON.stringify(r.labels));
r = await at('json', '{"reply_markup": {"inline_keyboard": [[{"text": "a", "style": "|"}]]}}');
check('a button\'s style: its enum', ['danger', 'success', 'primary'].every((l) => r.labels.includes(l)), JSON.stringify(r.labels));
r = await at('json', '{"reply_markup": {"inline_keyboard": [[{"te|"}]]}}');
check('the keys of a button', r.labels.includes('text') && r.labels.includes('callback_data'), JSON.stringify(r.labels));

// where there is nothing to offer
r = await at('json', '{"rich_message": {"html": "<b>Привет|"}}');
check('the text of the post opens nothing', r.result === null);
r = await at('json', '{"callbacks": {"|"}}');
check('callbacks (not a Bot API type) open nothing', r.result === null);
r = await at('json', '["a|');
check('JSON that is not an object opens nothing', r.result === null);

// a new block, field and parameter in the spec reach the suggestions with no code: the same answer, changed in memory
const next = structuredClone(spec);
const field = (name, types, enumValues = null) => ({ name, types, required: false, description: 'новое', enum: enumValues });
next.types.InputRichBlockFuture = {
  description: 'Блок из будущей версии', subtypes: [], const_field: 'type', const: 'future', href: null,
  fields: [field('type', ['String']), field('mode', ['String'], ['fast', 'slow'])],
};
next.types.InputRichBlock.subtypes.push('InputRichBlockFuture');
next.types.InputRichBlockParagraph.fields.push(field('new_field', ['Boolean']));
next.post.types.PostOptions.fields.push(field('new_parameter', ['Boolean']));
const later = jsonSource(syntaxTree, () => next);
r = await at('json', '{"rich_message": {"blocks": [{"type": "fu|"}]}}', false, later);
const block = r.option('future');
r = await at('json', '{"rich_message": {"blocks": [{"type": "future", "mode": "|"}]}}', false, later);
const modes = r.labels;
r = await at('json', '{"rich_message": {"blocks": [{"type": "paragraph", "|"}]}}', false, later);
const newField = r.labels.includes('new_field');
r = await at('json', '{"options": {"new|"}}', false, later);
check('a new block, its enum, a new field and a new parameter in the spec are suggested with no change in the code',
  block?.detail === 'InputRichBlockFuture' && modes.join() === 'fast,slow' && newField && r.labels.includes('new_parameter'),
  JSON.stringify({ block, modes, newField, options: r.labels }));

// HTML from snippets.js, the same inside Markdown
r = await at('html', '<p>Это <tg-sp|');
check('a tag after "<": the tags of the snippets', r.typed === 'tg-sp' && r.labels.includes('tg-spoiler') && r.labels.includes('b')
  && !r.labels.includes('div'), JSON.stringify([r.typed, r.labels.length]));
r = await at('html', '<tg-button |');
check('an attribute of the open tag', ['type', 'url', 'style'].every((l) => r.labels.includes(l)), JSON.stringify(r.labels));
check('…picked: name="" with the cursor inside', r.pick('url') === '<tg-button url="|"', r.pick('url'));
r = await at('html', '<tg-button type="url" |');
check('attributes already on the tag are not offered again', !r.labels.includes('type') && r.labels.includes('url'), JSON.stringify(r.labels));
r = await at('html', '<tg-button type="|');
check('an attribute\'s value', ['url', 'copy_text', 'callback_data'].every((l) => r.labels.includes(l)), JSON.stringify(r.labels));
r = await at('html', '<blockquote ex|');
check('a bare attribute is written bare', r.pick('expandable') === '<blockquote expandable|', r.pick('expandable'));
r = await at('html', 'Просто текст|');
check('text outside a tag opens nothing', r.result === null);
r = await at('markdown', '**Жирный** и <tg-sp|');
check('Markdown: the same tags', r.labels.includes('tg-spoiler') && r.typed === 'tg-sp', JSON.stringify(r.labels.length));

const failed = results.filter((ok) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
