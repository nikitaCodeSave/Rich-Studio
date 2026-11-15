// <rs-templates>: post templates (a rubric: a digest, an announcement) at the top of «Черновики». A folded line
// «Шаблоны N»; open, the list with «Новый пост» (a form from the template's fields → a new draft), «Поля» and ✕,
// and «Текущий пост шаблоном». The page loads the list and does the requests on the events
// template-new, template-fill, template-edit, template-delete (detail: the template row).
import { css, html, LitElement, nothing } from '../../vendor/lit-core.min.js';

export class RsTemplates extends LitElement {
  static properties = { templates: { attribute: false }, open: { type: Boolean } };

  static styles = css`
    :host { display: block; margin-bottom: 6px; font-size: 13px; }
    details { border: 1px solid var(--border, #dfe3e8); border-radius: 8px; }
    summary { cursor: pointer; padding: 6px 8px; color: var(--text, #17212b); font-weight: 600; list-style-position: inside; }
    summary:focus-visible, button:focus-visible { outline: 2px solid var(--accent, #3390ec); outline-offset: 1px; }
    .n { font-weight: 400; color: var(--muted, #707579); }
    .body { padding: 0 8px 8px; display: flex; flex-direction: column; gap: 6px; }
    ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    li { border-top: 1px solid var(--border, #dfe3e8); padding-top: 6px; display: flex; flex-direction: column; gap: 3px; min-width: 0; }
    .title { overflow-wrap: anywhere; }
    .meta { font-size: 11.5px; color: var(--muted, #707579); overflow-wrap: anywhere; }
    .acts { display: flex; flex-wrap: wrap; gap: 4px; }
    p { margin: 0; color: var(--muted, #707579); font-size: 12px; line-height: 1.4; }
    button { font: inherit; font-size: 12.5px; padding: 3px 8px; border-radius: 8px; border: 1px solid var(--border, #dfe3e8);
      background: transparent; color: var(--accent, #3390ec); cursor: pointer; white-space: nowrap; }
    button:hover { border-color: var(--accent, #3390ec); }
    button.primary { background: var(--accent, #3390ec); border-color: var(--accent, #3390ec); color: var(--accent-text, #fff); }
    button.x { color: var(--err, #d93025); }`;

  constructor() {
    super();
    /** @type {TemplateRow[]} */
    this.templates = [];
    this.open = false;
  }

  /** @param {string} name @param {TemplateRow} [t] */
  fire(name, t) { this.dispatchEvent(new CustomEvent(name, { detail: t, bubbles: true, composed: true })); }

  render() {
    const list = this.templates;
    return html`<details ?open=${this.open} @toggle=${(e) => { this.open = e.target.open; }}>
  <summary>Шаблоны${list.length ? html` <span class="n">${list.length}</span>` : nothing}</summary>
  <div class="body">
    <button class="add" @click=${() => this.fire('template-new')}
      title="Сохранить пост из редактора шаблоном и выбрать строки, которые станут полями">＋ Текущий пост шаблоном</button>
    ${list.length ? html`<ul>${list.map((t) => html`<li>
      <span class="title">${t.title}</span>
      <span class="meta">${t.fields.length ? t.fields.map((f) => f.label).join(' · ') : 'без полей: копия поста'}</span>
      <span class="acts">
        <button class="primary" @click=${() => this.fire('template-fill', t)} title="Заполнить поля: получится новый черновик">Новый пост</button>
        <button @click=${() => this.fire('template-edit', t)} title="Название и поля шаблона">Поля</button>
        <button class="x" @click=${() => this.fire('template-delete', t)} title="Удалить шаблон" aria-label=${'Удалить шаблон ' + t.title}>✕</button>
      </span></li>`)}</ul>`
    : html`<p>Рубрика один раз: сохраните пост шаблоном и сделайте полями то, что меняется от выпуска к выпуску. Новый пост — форма из этих полей.</p>`}
  </div>
</details>`;
  }
}

customElements.define('rs-templates', RsTemplates);
