// <rs-parse-check>: the preview checked against Telegram's own parser.
// After «Мне в личку» Telegram answers with the blocks it made of the post. The page compares their outline
// with the preview's (RichRender.outline) and passes the result here; this bar says whether they agree, lets the
// author look at Telegram's version and, for HTML or Markdown, take it into the visual mode.
// Events: toggle (show Telegram's version or the preview), to-blocks.
import { css, html, LitElement, nothing } from '../../vendor/lit-core.min.js';

export class RsParseCheck extends LitElement {
  static properties = {
    check: { attribute: false },
    showing: { type: Boolean },
    convertible: { type: Boolean },
  };

  static styles = css`
    :host { display: block; }
    .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; padding: 6px 12px; font-size: 13px;
      background: var(--panel, #fff); border-bottom: 1px solid var(--border, #dfe3e8); }
    .what { flex: 1 1 100%; min-width: 0; }
    .ok { color: var(--ok, #1e8e3e); } .diff { color: var(--warn, #b26a00); } .muted { color: var(--muted, #707579); }
    .where { flex-basis: 100%; display: grid; grid-template-columns: auto 1fr; gap: 2px 8px; font-size: 12px; }
    .where code { font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
    button { font: inherit; padding: 3px 8px; border-radius: 8px; border: 1px solid var(--border, #dfe3e8);
      background: transparent; color: var(--accent, #3390ec); cursor: pointer; white-space: nowrap; }
    button[aria-pressed="true"] { background: var(--accent, #3390ec); border-color: var(--accent, #3390ec); color: var(--accent-text, #fff); }
    button:focus-visible { outline: 2px solid var(--accent, #3390ec); outline-offset: 1px; }`;

  constructor() {
    super();
    /** @type {ParseCheck | null} */
    this.check = null;
    this.showing = false;
    this.convertible = false;
  }

  /** @param {string} name */
  fire(name) { this.dispatchEvent(new CustomEvent(name, { bubbles: true, composed: true })); }

  render() {
    const c = this.check;
    if (!c) return nothing;
    if (c.status === 'stale') {
      return html`<div class="bar" role="status"><span class="what muted">Пост изменился после отправки в личку. Отправьте ещё раз, чтобы сверить с Telegram.</span></div>`;
    }
    const same = c.status === 'same';
    return html`<div class="bar" role="status">
  <span class="what ${same ? 'ok' : 'diff'}">${same ? '✓ Telegram разобрал пост так же, как предпросмотр' : '≠ Telegram разобрал пост иначе, чем показывает предпросмотр'}</span>
  <button aria-pressed=${this.showing ? 'true' : 'false'} @click=${() => this.fire('toggle')}
    title="Показать пост из блоков, которые вернул Telegram">Как разобрал Telegram</button>
  ${this.convertible ? html`<button @click=${() => this.fire('to-blocks')}
    title="Заменить визуальный режим блоками Telegram; исходный текст останется в своей вкладке">В визуальный</button>` : nothing}
  ${!same && c.mine !== undefined ? html`<div class="where">
    <span class="muted">Предпросмотр:</span><code>${c.mine}</code>
    <span class="muted">Telegram:</span><code>${c.theirs}</code></div>` : nothing}
</div>`;
  }
}

customElements.define('rs-parse-check', RsParseCheck);
