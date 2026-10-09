// <rs-preview>: the post as Telegram shows it, in its own shadow root.
// tg.css and KaTeX styles apply only inside, the editor's app.css never reaches the post.
// The HTML itself comes from RichRender (the sanitizer), never from a Lit template.
import { css, html, LitElement } from '../../vendor/lit-core.min.js';

export class RsPreview extends LitElement {
  static properties = {
    post: { attribute: false },
    dark: { type: Boolean, reflect: true },
    channelTitle: { attribute: 'channel-title' },
  };

  // the chat background fills the host however short the post is
  static styles = css`:host { display: flex; flex-direction: column; } .tg-preview { flex: 1 0 auto; }`;

  constructor() {
    super();
    /** @type {Json | null} */
    this.post = null;
    this.dark = false;
    this.channelTitle = '';
  }

  // the page's #hash navigation does not see ids inside a shadow root, so in-post links scroll here
  followAnchor(e) {
    const a = e.target instanceof Element && e.target.closest('a[href^="#"]');
    if (!a) return;
    e.preventDefault();
    const id = decodeURIComponent((a.getAttribute('href') || '').slice(1));
    /** @type {ShadowRoot} */ (this.renderRoot).getElementById(id)?.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }

  render() {
    const post = this.post;
    const comment = post && post.comment;
    return html`<link rel="stylesheet" href="/static/vendor/katex/katex.min.css"><link rel="stylesheet" href="/static/tg.css">
<div class="tg-preview ${this.dark ? 'tg-dark' : ''}" @click=${this.followAnchor}>${post ? RichRender.render(post, { channelTitle: this.channelTitle }) : ''}${comment
  ? html`<div class="tg-comment-label">Первый комментарий в обсуждении</div>${RichRender.render({ rich_message: comment }, {})}`
  : ''}</div>`;
  }
}

customElements.define('rs-preview', RsPreview);
