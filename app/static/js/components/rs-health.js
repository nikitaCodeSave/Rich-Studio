// <rs-health>: the studio's own health in the header, from GET /api/health. A dot when all is well,
// a dot and the number of problems otherwise; the page fetches the report and opens the details on `details`.
import { css, html, LitElement } from '../../vendor/lit-core.min.js';

const WORD = { ok: 'в порядке', warn: 'требует внимания', fail: 'есть сбой', down: 'не отвечает' };

export class RsHealth extends LitElement {
  static properties = { report: { attribute: false }, down: { type: Boolean } };

  static styles = css`
    :host { display: inline-flex; }
    button { display: inline-flex; align-items: center; gap: 5px; font: inherit; font-size: 12px; padding: 4px 8px;
      border-radius: 999px; border: 1px solid var(--border, #dfe3e8); background: var(--soft, #eef3f8);
      color: var(--text, #17212b); cursor: pointer; white-space: nowrap; }
    button:focus-visible { outline: 2px solid var(--accent, #3390ec); outline-offset: 1px; }
    .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted, #707579); flex: none; }
    .ok .dot { background: var(--ok, #1e8e3e); }
    .warn .dot { background: var(--warn, #b26a00); }
    .warn { border-color: var(--warn, #b26a00); }
    .fail .dot, .down .dot { background: var(--err, #d93025); }
    .fail, .down { border-color: var(--err, #d93025); color: var(--err, #d93025); }`;

  constructor() {
    super();
    /** @type {HealthReport | null} */
    this.report = null;
    this.down = false;
  }

  render() {
    const r = this.report;
    const status = this.down ? 'down' : r ? r.status : 'unknown';
    const problems = !this.down && r ? r.problems : [];
    const title = r || this.down
      ? 'Студия ' + WORD[status] + (problems.length ? ':\n' + problems.join('\n') : '') + '\nПодробнее по нажатию'
      : 'Состояние студии проверяется';
    return html`<button class=${status} title=${title} aria-label=${'Состояние студии: ' + (WORD[status] || 'проверяется')}
  @click=${() => this.dispatchEvent(new CustomEvent('details', { bubbles: true, composed: true }))}>
  <span class="dot"></span>${problems.length ? html`<span>${problems.length}</span>` : ''}
</button>`;
  }
}

customElements.define('rs-health', RsHealth);
