// Editor state shared by all modules, and the target channels typed in the header.
import { signal } from '../vendor/signals-core.js';
import { $ } from './dom.js';

// ---------- signals: state the page reacts to on its own (effects in main.js) ----------
export const dirty = signal(false);
export const saveState = signal(''); // text next to the Save button
export const dark = signal(matchMedia('(prefers-color-scheme: dark)').matches);
/** the published or scheduled post the open draft is a working copy of (<rs-edit-bar> shows it) */
export const editing = signal(/** @type {PostRow | null} */ (null));
/** the blocks Telegram made of the post at the last «Мне в личку» (<rs-parse-check> compares the preview with them) */
export const parsed = signal(/** @type {ParsedPost | null} */ (null));
/** the preview shows Telegram's blocks instead of the editor's post */
export const showParsed = signal(false);

// ---------- state ----------
/** @type {EditorState} */
export const S = {
  // boot() in main.js loads the spec before anything renders, so modules may treat it as present
  spec: /** @type {SpecSummary} */ (/** @type {unknown} */ (null)), config: {}, draftId: null, draftVersion: null, conflict: false, format: 'blocks', mode: 'blocks',
  buf: { blocks: [], html: '', markdown: '' }, media: [], flags: {}, keyboard: [], options: {},
  extra: { pin: false, comment: '', commentObj: null, callbacks: {}, poll: null }, updates: null,
  uploads: [], posts: [], drafts: [], channels: [], topicChat: undefined,
};

// each channel once, as the server counts them (@Chan and @chan are one channel)
export const chats = () => { const seen = new Set(); return $('#chat').value.split(/[\s,;]+/).map((x) => x.trim()).filter((x) => x && !seen.has(x.toLowerCase()) && seen.add(x.toLowerCase())); };
export const chat = () => chats()[0];
// body fields for one or several channels
// always the explicit list the confirmation showed, never "whatever the saved draft says"
export const target = () => { const c = chats(); return { chat_ids: c.length ? c : [S.config.default_chat].filter(Boolean) }; };
/** @param {string | undefined} id */
export function channelOf(id) {
  if (!id) return null;
  return S.channels.find((c) => c.chat_id === id || String(c.chat_num) === id || (c.username && '@' + c.username === id)) || null;
}
/** The channel's title for the preview, the header and confirmations; the id itself while nobody checked it. */
export function channelTitle(id) {
  const c = channelOf(id);
  return (c && c.title) || id || '';
}
/** A channel as the person reads it: its title and its id, so two channels never look alike. @param {string} id */
export const chatLabel = (id) => { const t = channelTitle(id); return t === id ? id : `${t} · ${id}`; };
/** Where the post goes, as the person reads it: titles, and the forum topic when there is one. */
export const targetLabel = () => {
  const c = chats();
  const ids = c.length ? c : [S.config.default_chat].filter(Boolean);
  if (!ids.length) return '';
  const tid = S.options.message_thread_id;
  const one = ids.length === 1 ? channelOf(ids[0]) : null;
  const topic = tid !== undefined ? ((one && one.topics.find((t) => t.id === tid)) || { name: '№' + tid }).name : '';
  return ids.map((id) => { const t = channelTitle(id); return t === id ? id : `«${t}» (${id})`; }).join(', ') + (topic ? `, тема «${topic}»` : '');
};
