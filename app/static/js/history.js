// Undo/redo for the whole post: a stack of draft snapshots, one per pause in editing.
import { computed, signal } from '../vendor/signals-core.js';
import { autosave, draftPayload, loadPost, setMode } from './editor.js';
import { dirty, parsed, S, sameChat, saveState, showParsed, topicHome } from './state.js';

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

// a step with a forum topic keeps the chat the topic belongs to (as showTarget takes it)
const snapshot = () => {
  const post = draftPayload();
  if (post.options?.message_thread_id === undefined) return JSON.stringify({ post });
  return JSON.stringify({ post, chat: S.topicChat ?? topicHome() });
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

/** The step as it shows here: in another chat its topic would send the post to a wrong thread or nowhere, so it stays
 * behind. */
function here(snap) {
  const { post, chat } = JSON.parse(snap);
  if (post.options?.message_thread_id === undefined || sameChat(chat, topicHome())) return snap;
  delete post.options.message_thread_id;
  return JSON.stringify({ post });
}

function restore(snap) {
  current = snap;
  const inJson = S.mode === 'json';
  loadPost(JSON.parse(snap).post);
  if (inJson) setMode('json', true); // an undo while typing JSON keeps the person in the JSON tab
  dirty.value = true;
  saveState.value = 'есть изменения';
  autosave();
}

/** One Ctrl+Z or Ctrl+Y changes what is shown: a step that differed only by a topic left behind in another chat looks
 * here like its neighbour, so it goes along, before the change and after it (or the next press would change nothing).
 * @param {boolean} back */
function go(back) {
  if (timer) commit(); // an edit still waiting for its pause is the step to undo
  const [from, to] = back ? [past, future] : [future, past];
  const top = () => (back ? from.value.at(-1) : from.value[0]);
  const move = () => {
    const step = here(top());
    from.value = back ? from.value.slice(0, -1) : from.value.slice(1);
    to.value = back ? [current, ...to.value] : [...to.value, current];
    current = step;
  };
  const shown = current;
  const stacks = [past.value, future.value];
  while (from.value.length && current === shown) move();
  while (from.value.length && here(top()) === current) move();
  if (current !== shown) restore(current);
  else [past.value, future.value] = stacks; // nothing here to change: the press changes nothing at all
}

export function undo() { go(true); }
export function redo() { go(false); }
