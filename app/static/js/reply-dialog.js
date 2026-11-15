// The rich reply to a callback button (callbacks[data].reply, Bot API 10.4): sendRichMessage for an ephemeral
// message only the person who pressed sees, by choice in place of the post. The dialog edits its HTML and that flag,
// draws it through <rs-preview> (RichRender, the sanitizer) and lists what the studio's check says about it.
// It never touches the post itself: the answer goes in and out through `onChange`.
import { openDialog } from './dialogs.js';
import { clone, debounce, h } from './dom.js';
import { dark } from './state.js';
import './components/rs-preview.js';

const FLAG = 'replace_callback_query_message';

/** The text of a reply as a line for the button's card: what RichRender draws of it, never its HTML.
 * @param {Json} reply */
export function replySummary(reply) {
  const rm = (reply && reply.rich_message) || {};
  const text = (RichRender.render({ rich_message: rm }, {}).querySelector('.tg-body')?.textContent || '').trim().replace(/\s+/g, ' ');
  return text.length > 60 ? text.slice(0, 59) + '…' : text;
}

/**
 * @param {{ label: string, answer: Json, onChange: (answer: Json) => void, issues: () => Promise<{ errors: string[], warnings: string[] }> }} o
 *   answer: the button's answer object as it is now; issues: the check's messages about this button
 */
export function openReplyDialog({ label, answer, onChange, issues }) {
  const a = clone(answer || {});
  /** @type {Json} */
  const reply = a.reply && typeof a.reply === 'object' && !Array.isArray(a.reply) ? a.reply : { rich_message: { html: '' } };
  if (!reply.rich_message || typeof reply.rich_message !== 'object') reply.rich_message = { html: '' };
  const rm = reply.rich_message;
  // blocks or Markdown come from the JSON tab: kept as they are, edited there
  const asHtml = rm.blocks === undefined && rm.markdown === undefined;
  const eph = () => (reply.ephemeral_message_parameters && typeof reply.ephemeral_message_parameters === 'object' ? reply.ephemeral_message_parameters : null);
  const preview = /** @type {import('./components/rs-preview.js').RsPreview} */ (h('rs-preview', { class: 'reply-preview' }));
  const list = h('div', { class: 'reply-issues' });
  const showIssues = debounce(async () => {
    try {
      const r = await issues();
      list.replaceChildren(...[
        !r.errors.length && !r.warnings.length ? h('span', { class: 'ok small', text: filled() ? '✓ Ответ проходит проверку' : 'Ответа нет: впишите его выше' }) : null,
        r.errors.length ? h('ul', {}, r.errors.map((e) => h('li', { class: 'e', text: e }))) : null,
        r.warnings.length ? h('ul', {}, r.warnings.map((e) => h('li', { class: 'w', text: e }))) : null].filter(Boolean));
    } catch (e) { list.replaceChildren(h('span', { class: 'e small', text: 'Проверка недоступна: ' + e.message })); }
  }, 400);
  const filled = () => (asHtml ? !!(rm.html || '').trim() : true);
  const draw = () => {
    preview.dark = dark.value;
    preview.channelTitle = 'Видит только нажавший' + (eph() && eph()[FLAG] ? ' · вместо поста' : '');
    preview.post = filled() ? { rich_message: rm, ...(reply.reply_markup ? { reply_markup: reply.reply_markup } : {}) } : null;
  };
  const put = () => {
    const next = { ...a };
    if (filled()) next.reply = reply; else delete next.reply;
    onChange(next);
    draw();
    showIssues();
  };
  const src = asHtml
    ? h('textarea', { class: 'code', id: 'reply-src', style: 'min-height:120px', value: rm.html || '', placeholder: '<p>Только для вас: промокод <code>RICH2026</code></p>', 'aria-label': 'Rich-ответ (HTML)', oninput: (e) => { rm.html = e.target.value; put(); } })
    : h('p', { class: 'w small', text: `Ответ задан во вкладке JSON (${rm.blocks !== undefined ? 'blocks' : 'markdown'}): здесь он только показан, правится там.` });
  const flag = h('label', { class: 'row', style: 'margin:2px 0', title: 'ephemeral_message_parameters.' + FLAG },
    h('input', { type: 'checkbox', id: 'reply-replace', checked: !!(eph() && eph()[FLAG]), onchange: (e) => {
      const p = { ...(eph() || {}) };
      if (e.target.checked) p[FLAG] = true; else delete p[FLAG];
      if (Object.keys(p).length) reply.ephemeral_message_parameters = p; else delete reply.ephemeral_message_parameters;
      put();
    } }), 'Показать вместо поста (только нажавшему)');
  // closes the dialog (value): nothing in it is the button's answer any more
  const remove = h('button', { class: 'ghost', value: 'ok', text: 'Убрать rich-ответ', onclick: () => { const next = { ...a }; delete next.reply; onChange(next); } });
  openDialog([
    h('h3', { text: `Rich-ответ на «${label}»` }),
    h('p', { class: 'muted small', text: 'Сообщение, которое увидит только нажавший: студия отправит его сразу после нажатия (sendRichMessage с ephemeral_message_parameters), затем ответит на нажатие, как обычно. Telegram принимает такой ответ в течение 15 секунд после нажатия и не обещает доставку; повторов нет, сбой виден в журнале и у нажатий поста.' }),
    h('p', { class: 'w small', text: 'Под постом канала это ещё не проверено: ответ может не дойти. Проверьте кнопкой «Мне в личку», а потом на тестовом канале.' }),
    src, flag,
    h('div', { class: 'small muted', text: 'Предпросмотр' }), preview, list,
  ], [remove, h('button', { value: 'ok', text: 'Готово' })]);
  draw();
  showIssues();
}
