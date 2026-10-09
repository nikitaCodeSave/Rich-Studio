// Editor state shared by all modules, and the target channels typed in the header.
import { $ } from './dom.js';

// ---------- state ----------
export const S = {
  spec: null, config: {}, draftId: null, format: 'blocks', mode: 'blocks',
  buf: { blocks: [], html: '', markdown: '' }, media: [], flags: {}, keyboard: [], options: {},
  extra: { pin: false, comment: '', commentObj: null, callbacks: {} }, updates: null,
  uploads: [], posts: [], drafts: [], dark: matchMedia('(prefers-color-scheme: dark)').matches, dirty: false,
};

export const chats = () => $('#chat').value.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
export const chat = () => chats()[0];
// body fields for one or several channels
export const target = () => { const c = chats(); return c.length > 1 ? { chat_ids: c } : { chat_id: c[0] }; };
export const targetLabel = () => { const c = chats(); return c.length ? c.join(', ') : S.config.default_chat; };
