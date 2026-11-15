// <rs-draft-notes>: above the editor while the open draft has warnings in `_studio.warnings` (what an import from a
// forwarded post could not carry over, and anything else that made the draft). A small list the person can fold;
// folded, it stays folded for the same warnings until they change (another draft, new warnings from an agent).
// The warnings are someone else's text: they go in as Lit text, never as HTML.
import { css, html, LitElement, nothing } from '../../vendor/lit-core.min.js';

export class RsDraftNotes extends LitElement {
  static properties = { warnings: { attribute: false }, folded: { state: true } };

  static styles = css`
    :host { display: block; }
    details { margin-bottom: 10px; padding: 5px 10px; font-size: 13px; border: 1px solid var(--border, #dfe3e8);
      border-left: 3px solid var(--warn, #b26a00); border-radius: 8px; background: var(--panel, #fff); }
    summary { cursor: pointer; color: var(--warn, #b26a00); }
    summary:focus-visible { outline: 2px solid var(--accent, #3390ec); outline-offset: 2px; border-radius: 4px; }
    .hint { color: var(--muted, #707579); }
    ul { margin: 4px 0 2px; padding-left: 18px; }
    li { overflow-wrap: anywhere; }`;

  constructor() {
    super();
    /** @type {string[]} */
    this.warnings = [];
    /** the warnings (joined) the person folded */
    this.folded = '';
  }

  /** @param {Event} e */
  toggled(e) {
    this.folded = /** @type {HTMLDetailsElement} */ (e.target).open ? '' : this.key();
  }

  key() { return JSON.stringify(this.warnings); }

  render() {
    const w = this.warnings;
    if (!w || !w.length) return nothing;
    const open = this.folded !== this.key();
    return html`<details ?open=${open} @toggle=${this.toggled}>
  <summary>⚠ Замечания к черновику: ${w.length} <span class="hint">${open ? '· скрыть' : '· показать'}</span></summary>
  <ul>${w.map((x) => html`<li>${x}</li>`)}</ul>
</details>`;
  }
}

customElements.define('rs-draft-notes', RsDraftNotes);
