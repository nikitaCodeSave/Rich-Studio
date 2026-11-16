// What Telegram refuses in the chats the post goes to: `chat_rules` of GET /api/spec (spec/chats.py reads them from
// the descriptions of the spec) against the types of the chats the studio checked in «Каналы». The check below the
// editor says the same for the whole post (/api/validate with chat_ids); here a card marks its own fields.
import { channelOf, S, target } from './state.js';

// how a chat is named: «не работает в канале «Новости»» (spec/chats.py PLACES)
const PLACES = { channel: 'в канале', supergroup: 'в группе', group: 'в группе', private: 'в личке' };

/** @typedef {{ type: string, words: string }} Place */
/** @typedef {{ field: string, here: Place[], rule: ChatRule }} Refusal */

/** The chats the post goes to whose type the studio knows, as a warning names them. A chat nobody checked has none.
 * @returns {Place[]} */
export function places() {
  return target().chat_ids.map(channelOf).filter((c) => c && c.type).map((c) => ({
    type: /** @type {string} */ (c.type),
    words: `${c.type === 'supergroup' && c.is_forum ? 'в форуме' : PLACES[c.type] || 'в чате'} ${c.title ? `«${c.title}»` : c.chat_id}`,
  }));
}

/**
 * Fields of `obj` (an object of type `typeName`) Telegram refuses where the post goes.
 * @param {string} typeName @param {Json} obj @returns {Refusal[]}
 */
export function refusals(typeName, obj) {
  const rules = S.spec.chat_rules || {};
  const all = places();
  return Object.keys(obj || {}).flatMap((field) => {
    const rule = rules[`${typeName}.${field}`];
    const here = rule ? all.filter((p) => rule.refused[p.type]) : [];
    return here.length ? [{ field, here, rule }] : [];
  });
}

/** One refusal in words: where, why (each reason once), and the way out (a direct link for web_app…). @param {Refusal} r */
export function refusalText({ field, here, rule }) {
  const where = [...new Set(here.map((p) => p.words))].join(', ');
  const said = [...new Set(here.map((p) => rule.refused[p.type]))].map((s) => (s.measured
    ? `так отвечает Telegram: «${s.why}» (замер: ${s.measured}; в спецификации этого нет)`
    : `в спецификации: «${s.why.replace(/\.$/, '')}»`)).join('; ');
  const hint = rule.hint ? ` ${rule.hint[0].toUpperCase()}${rule.hint.slice(1)}.` : '';
  return `⚠ ${field} не работает ${where} — ${said}.${hint}`;
}
