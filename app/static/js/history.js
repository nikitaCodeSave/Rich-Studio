// Undo/redo for the whole post: a stack of draft snapshots, one per pause in editing.
import { computed, signal } from '../vendor/signals-core.js';
import { autosave, draftPayload, loadPost, setMode } from './editor.js';
import { chats, dirty, parsed, S, saveState, showParsed } from './state.js';

const LIMIT = 100;
const PAUSE_MS = 400; // edits closer than this become one undo step

/** @type {import('../vendor/signals-core.js').Signal<string[]>} */
const past = signal([]);
/** @type {import('../vendor/signals-core.js').Signal<string[]>} */
const future = signal([]);
let current = '';
/** @type {ReturnType<typeof setTimeout> | undefined} */
let timer;

export const canUndo = computed(() => past.value.length > 0);
export const canRedo = computed(() => future.value.length > 0);

// a step with a forum topic keeps the chat the topic belongs to: where it was chosen or loaded, else the one chat open
// (as showTarget takes it)
const snapshot = () => {
  const post = draftPayload();
  if (post.options?.message_thread_id === undefined) return JSON.stringify({ post });
  const list = chats();
  return JSON.stringify({ post, chat: S.topicChat ?? (list.length === 1 ? list[0] : undefined) });
};

function commit() {
  clearTimeout(timer);
  timer = undefined;
  const snap = snapshot();
  if (snap === current) return;
  past.value = [...past.value, current].slice(-LIMIT);
  future.value = [];
  current = snap;
}

/** The open draft changed elsewhere and was reloaded: one more undo step, so Ctrl+Z brings back what was here. */
export function pushHistory() { commit(); }

/** A new draft is open: its loaded state is the bottom of the stack. Telegram's check was for another post. */
export function resetHistory() {
  parsed.value = null;
  showParsed.value = false;
  clearTimeout(timer);
  timer = undefined;
  past.value = [];
  future.value = [];
  current = snapshot();
}

/** Called on every edit; the snapshot is taken once editing pauses. */
export function recordHistory() {
  clearTimeout(timer);
  timer = setTimeout(commit, PAUSE_MS);
}

function restore(snap) {
  const { post, chat } = JSON.parse(snap);
  const list = chats();
  // undone in another chat, the step's topic would send the post to a wrong thread or nowhere: it stays behind
  const moved = post.options?.message_thread_id !== undefined && !(list.length === 1 && list[0] === chat);
  if (moved) delete post.options.message_thread_id;
  const inJson = S.mode === 'json';
  loadPost(post);
  current = moved ? snapshot() : snap;
  if (inJson) setMode('json', true); // an undo while typing JSON keeps the person in the JSON tab
  dirty.value = true;
  saveState.value = 'есть изменения';
  autosave();
}

export function undo() {
  if (timer) commit(); // an edit still waiting for its pause is the step to undo
  const prev = past.value.at(-1);
  if (prev === undefined) return;
  past.value = past.value.slice(0, -1);
  future.value = [current, ...future.value];
  restore(prev);
}

export function redo() {
  if (timer) commit();
  const next = future.value[0];
  if (next === undefined) return;
  future.value = future.value.slice(1);
  past.value = [...past.value, current];
  restore(next);
}
