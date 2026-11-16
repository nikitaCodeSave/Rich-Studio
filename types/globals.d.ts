// Globals the classic scripts put on window for the editor modules.
interface RenderOptions {
  mediaBase?: string;
  channelTitle?: string;
  views?: string | number;
}
declare const RichRender: {
  render(post: object, opts?: RenderOptions): HTMLElement;
  renderRT(rt: unknown, ctx: object): Node;
  renderBlocks(blocks: unknown[], ctx: object): Node;
  formatTime(unix: number, fmt?: string): string;
  mediaSrc(media: unknown, ctx: object): string | null;
  safeUrl(url: string): string | null;
  outline(post: object): string;
  /** a user-friendly TON address Telegram finds in text: the right form and checksum */
  isTonAddress(s: unknown): boolean;
  /** a link address that goes through: what makes Telegram drop a link %-encoded, U+2028 taken out */
  percentAddress(url: string): string;
};
interface Window {
  RichRender: typeof RichRender;
  // vendored libraries the preview uses when present
  katex?: { render(expr: string, el: HTMLElement, opts?: object): void };
  markdownit?: (opts?: object) => any;
  // snippets.js: snippet groups for the html and markdown modes
  SNIPPETS: Record<string, Record<string, any>[]>;
}
