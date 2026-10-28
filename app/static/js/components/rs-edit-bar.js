// <rs-edit-bar>: shown above the editor while the draft is a working copy of a published or scheduled post.
// It says which post the editor changes and offers the actions that apply to that post; the page does the work
// on the events: update, reschedule, history, leave.
import { css, html, LitElement, nothing } from '../../vendor/lit-core.min.js';

const STATUS = { sent: 'опубликован', scheduled: 'отложен', missed: 'пропущен', sending: 'отправляется' };
const when = (t) => new Date(t * 1000).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' });

export class RsEditBar extends LitElement {
  static properties = { post: { attribute: false } };

  static styles = css`
    :host { display: block; }
    .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px 12px; margin-bottom: 10px;
      border: 1px solid var(--accent, #3390ec); border-radius: 10px; background: var(--soft, #eef4fb); font-size: 13px; }
    .what { flex: 1 1 220px; min-width: 0; }
    b { font-weight: 600; }
    .muted { opacity: .7; }
    button { font: inherit; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--border, #c9d3de);
      background: transparent; color: inherit; cursor: pointer; }
    button.primary { background: var(--accent, #3390ec); border-color: var(--accent, #3390ec); color: var(--accent-text, #fff); }
    button:focus-visible { outline: 2px solid var(--accent, #3390ec); outline-offset: 1px; }`;

  constructor() {
    super();
    /** @type {PostRow | null} */
    this.post = null;
  }

  /** @param {string} name */
  fire(name) { this.dispatchEvent(new CustomEvent(name, { detail: this.post, bubbles: true, composed: true })); }

  render() {
    const p = this.post;
    if (!p) return nothing;
    const waiting = p.status === 'scheduled' || p.status === 'missed';
    const at = waiting && p.scheduled_at ? ' на ' + when(p.scheduled_at) : p.sent_at ? ' ' + when(p.sent_at) : '';
    return html`<div class="bar" role="status">
  <span class="what">Правите пост <b>${p.message_id ? '#' + p.message_id : ''} в ${p.chat_id}</b>
    <span class="muted">· ${STATUS[p.status] || p.status}${at}</span></span>
  ${p.status === 'sent' || waiting
    ? html`<button class="primary" @click=${() => this.fire('update')} title="Заменить этот пост содержимым редактора">Обновить пост</button>` : nothing}
  ${waiting ? html`<button @click=${() => this.fire('reschedule')}>${p.status === 'missed' ? 'Отложить заново' : 'Перенести'}</button>` : nothing}
  <button @click=${() => this.fire('history')} title="Как пост выглядел до замен">История</button>
  <button @click=${() => this.fire('leave')} title="Черновик останется, связь с постом снимется" aria-label="Больше не править этот пост">✕</button>
</div>`;
  }
}

customElements.define('rs-edit-bar', RsEditBar);
