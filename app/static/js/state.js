// Editor state shared by all modules, and the target channels typed in the header.
import { signal } from '../vendor/signals-core.js';
import { $ } from './dom.js';

// ---------- signals: state the page reacts to on its own (effects in main.js) ----------
export const dirty = signal(false);
export const saveState = signal(''); // text next to the Save button
export const dark = signal(matchMedia('(prefers-color-scheme: dark)').matches);

// ---------- state ----------
/** @type {EditorState} */
export const S = {
  // boot() in main.js loads the spec before anything renders, so modules may treat it as present
  spec: /** @type {SpecSummary} */ (/** @type {unknown} */ (null)), config: {}, draftId: null, format: 'blocks', mode: 'blocks',
  buf: { blocks: [], html: '', markdown: '' }, media: [], flags: {}, keyboard: [], options: {},
  extra: { pin: false, comment: '', commentObj: null, callbacks: {} }, updates: null,
  uploads: [], posts: [], drafts: [],
};

export const chats = () => $('#chat').value.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
export const chat = () => chats()[0];
// body fields for one or several channels
export const target = () => { const c = chats(); return c.length > 1 ? { chat_ids: c } : { chat_id: c[0] }; };
export const targetLabel = () => { const c = chats(); return c.length ? c.join(', ') : S.config.default_chat; };
