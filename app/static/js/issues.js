// Validation messages in the author's words («Раскрывашка №3 › Заголовок: пустой текст») and a jump to that block.
import { cardOf } from './forms.js';
import { BLOCK_LABELS, FIELD_LABELS, RT_LABELS } from './labels.js';
import { S } from './state.js';

const PATH = /^(rich_message(?:\.\w+|\[\d+\])*)(?::\s*|\s+)([\s\S]*)$/;
const CONTAINERS = new Set(['blocks', 'items', 'cells', 'media', 'buttons']);

/**
 * @param {string} msg a message from /api/validate, starting with a JSON path
 * @returns {{ text: string, block?: object, field?: string }}
 */
export function readIssue(msg) {
  const m = PATH.exec(msg);
  if (!m) return { text: msg };
  const tokens = [...m[1].matchAll(/\.(\w+)|\[(\d+)\]/g)].map((x) => (x[1] !== undefined ? x[1] : Number(x[2])));
  /** @type {any} */
  let cur = { blocks: S.buf.blocks, media: S.media };
  const names = [];
  let block, field;
  let parent = '';
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    const next = cur == null ? undefined : cur[t];
    if (typeof t === 'number') {
      if (parent === 'blocks') { names.push(`${BLOCK_LABELS[next?.type] || next?.type || 'Блок'} №${t + 1}`); if (next && typeof next === 'object') { block = next; field = undefined; } }
      else if (parent === 'cells' && typeof tokens[i + 1] === 'number') {
        const c = /** @type {number} */ (tokens[i + 1]);
        names.push(`ячейка ${t + 1}:${c + 1}`);
        cur = next?.[c]; i++; parent = ''; continue;
      }
      else if (parent === 'items') names.push(`пункт ${t + 1}`);
      else if (parent === 'media') names.push(`медиа ${t + 1}`);
      else if (parent === 'buttons') names.push(`кнопка ${t + 1}`);
      else if (next && typeof next === 'object' && RT_LABELS[next.type]) names.push(RT_LABELS[next.type]); // a piece of rich text
    } else if (!CONTAINERS.has(t) || typeof tokens[i + 1] !== 'number') {
      names.push(FIELD_LABELS[t] || t);
      if (block && field === undefined) field = t;
    }
    parent = typeof t === 'string' ? t : '';
    cur = next;
  }
  return { text: names.length ? names.join(' › ') + ': ' + m[2] : m[2], block, field };
}

/** Scroll to the block a message is about, open it and blink the field. False when the block is not on screen. */
export function reveal(block, field) {
  const card = block && cardOf.get(block);
  if (!card || !card.isConnected || S.mode !== 'blocks') return false;
  for (let p = card; p; p = p.parentElement?.closest('.block')) p.classList.remove('collapsed');
  const row = field ? card.querySelector(`:scope > .block-body > .fields > [data-field="${CSS.escape(field)}"]`) : null;
  const target = row || card;
  target.scrollIntoView({ block: 'center', behavior: 'smooth' });
  target.classList.remove('flash');
  void target.offsetWidth; // restart the animation
  target.classList.add('flash');
  /** @type {HTMLElement | null} */ (row?.querySelector('.rte, input, textarea, select') || null)?.focus({ preventScroll: true });
  return true;
}
